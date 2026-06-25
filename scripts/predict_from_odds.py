#!/usr/bin/env python3
"""predict_from_odds.py — 从 OM 赔率反推泊松预测，UPSERT 到 DB

职责：
- 读 data/raw/oddsmagnet/{date}.json
- 对每场比赛：
  - 优先用 OM 百家平均 implied(去抽水) 作为市场预期
  - avg 为空时，从 Pinnacle 赔率反推隐含概率（1/odds，去抽水归一化）
  - 用 implied 反推实力差距 → 估算 λ_home/λ_away
  - 用泊松分布算 P(主胜/平/客胜)
  - final = 0.7×poisson + 0.3×implied
  - prediction = max(final) 方向
- UPSERT 到 poisson_predictions 表（按 date+home+away 唯一约束）

依赖：raw/oddsmagnet/{date}.json
输出：DB 中新插入的 N 条预测记录

用法：
  python predict_from_odds.py --date 2026-06-06
  python predict_from_odds.py --date 2026-06-06 --db data/football.db
"""

import os
import sys
import json
import math
import sqlite3
import argparse
import re
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_OM = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")
RAW_BSD = os.path.join(REPO_DIR, "data", "raw", "bsd")
RAW_ODDSMAGNET = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")
DB_PATH = os.path.join(REPO_DIR, "data", "football.db")


def name_sim(a: str, b: str) -> float:
    """字符集相似度（与fetch_pinnacle_odds.team_name_similarity公式对齐：交集/较长串长度）"""
    if not a or not b:
        return 0.0
    return len(set(a) & set(b)) / max(len(a), len(b), 1)


def load_ah_for_date(date_str: str) -> list:
    """读 ah_YYYYMMDD.json + ah_YYYYMMDD.json（前一天），返回亚盘列表"""
    results = []
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    prev = (dt - timedelta(days=1)).strftime('%Y%m%d')
    for tag in (date_str.replace('-', ''), prev):
        path = os.path.join(RAW_ODDSMAGNET, f"ah_{tag}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for key, ah in data.items():
                if not ah:
                    continue
                results.append(ah)
        except Exception as e:
            print(f'  WARN 读 {path} 失败: {e}')
    return results

# === 算法常量（默认值，可被 calibration_params.json 覆盖）===
BASE_TOTAL_GOALS = 2.4    # 联赛平均总进球（主+客）
HOME_ADV = 0.15            # 主场加成（λ_home 多 0.15）
SKILL_FACTOR = 0.6         # 实力调整系数
POISSON_WEIGHT = 0.5 # final = 0.5*poisson + 0.5*implied
IMPLIED_WEIGHT = 0.5
LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0

# === 校准参数 ===
CALIB_PATH = os.path.join(REPO_DIR, "data", "calibration_params.json")
_league_params = {}   # league → params dict
_global_params = {}   # fallback params
_calib_map = []       # isotonic regression calibration curve

def load_calibration():
    """加载校准参数（联赛分层+isotonic回归），文件不存在时静默跳过"""
    global _league_params, _global_params, _calib_map
    if not os.path.exists(CALIB_PATH):
        return
    try:
        with open(CALIB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _league_params = data.get('league_params', {})
        _global_params = data.get('global_params', {})
        _calib_map = data.get('global_calibration', [])
    except Exception as e:
        print(f'  WARN: 加载校准参数失败: {e}')

def get_league_params(league):
    """获取联赛特定参数，无则返回默认"""
    if league in _league_params:
        return _league_params[league]
    return {'base_total_goals': BASE_TOTAL_GOALS, 'home_adv': HOME_ADV,
            'skill_factor': SKILL_FACTOR, 'poisson_weight': POISSON_WEIGHT,
            'implied_weight': IMPLIED_WEIGHT}

def calibrate_prob(prob):
    """用isotonic回归校准概率输出"""
    if not _calib_map:
        return prob
    for i in range(len(_calib_map) - 1):
        lo_p, lo_a = _calib_map[i]
        hi_p, hi_a = _calib_map[i+1]
        if lo_p <= prob <= hi_p:
            t = (prob - lo_p) / max(hi_p - lo_p, 1e-9)
            return lo_a + t * (hi_a - lo_a)
    if prob < _calib_map[0][0]:
        return _calib_map[0][1]
    return _calib_map[-1][1]

def confidence_tier(prob):
    """根据校准后概率划分信心等级"""
    if prob >= 0.55: return 'high'
    elif prob >= 0.50: return 'medium'
    elif prob >= 0.45: return 'low'
    return 'very_low'


def poisson_pmf(lam, k):
    """泊松分布 P(X=k)"""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_match_probs(lam_h, lam_a, max_goals=10):
    """泊松独立分布算 P(主胜/平/客胜)"""
    p_h = [poisson_pmf(lam_h, k) for k in range(max_goals + 1)]
    p_a = [poisson_pmf(lam_a, k) for k in range(max_goals + 1)]
    p_w = p_d = p_l = 0.0
    for k in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = p_h[k] * p_a[j]
            if k > j:
                p_w += p
            elif k == j:
                p_d += p
            else:
                p_l += p
    return p_w, p_d, p_l


def estimate_lambdas(imp_w, imp_d, imp_l, league=None):
    """从 implied 反推 λ_home / λ_away，支持联赛分层参数"""
    lp = get_league_params(league) if league else None
    base_goals = lp['base_total_goals'] / 2 if lp else BASE_TOTAL_GOALS / 2
    home_adv = lp['home_adv'] if lp else HOME_ADV
    skill = lp['skill_factor'] if lp else SKILL_FACTOR
    denom = max(imp_w + imp_l, 0.01)
    share_h = imp_w / denom
    skill_adj = skill * (share_h - 0.5)
    lam_h = base_goals + home_adv + skill_adj
    lam_a = base_goals - home_adv - skill_adj
    lam_h = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_h))
    lam_a = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_a))
    return round(lam_h, 3), round(lam_a, 3)


def parse_kickoff_date(kickoff_str, fetch_date):
    """解析 kickoff 日期时间
    
    支持格式：
    - '2026-06-24 10:00:00' → ('2026-06-24', '10:00')
    - '2026-06-24 10:00'   → ('2026-06-24', '10:00')
    - '06-24 10:00'        → ('2026-06-24', '10:00')
    - '待定'               → (fetch_date, '00:00')
    """
    if not kickoff_str or kickoff_str == '待定' or len(kickoff_str) < 5:
        return fetch_date, '00:00'
    
    # 格式1: 完整日期时间 'YYYY-MM-DD HH:MM(:SS)'
    m = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})', kickoff_str)
    if m:
        return m.group(1), m.group(2)
    
    # 格式2: 短日期 'MM-DD HH:MM'
    m = re.match(r'(\d{2}-\d{2})\s+(\d{1,2}:\d{2})', kickoff_str)
    if m:
        year = fetch_date[:4]
        return f'{year}-{m.group(1)}', m.group(2)
    
    # 格式3: 只有日期 'YYYY-MM-DD' 或 'MM-DD'
    m = re.match(r'(\d{4}-\d{2}-\d{2})', kickoff_str)
    if m:
        return m.group(1), '00:00'
    
    m = re.match(r'(\d{2}-\d{2})', kickoff_str)
    if m:
        year = fetch_date[:4]
        return f'{year}-{m.group(1)}', '00:00'
    
    return fetch_date, '00:00'


def load_om_matches(date_str):
    """读 OM raw 数据（检查3天窗口：前一天/当天/后一天，合并去重）"""
    merged = {}
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    for offset in [-1, 0, 1]:
        d = (dt + timedelta(days=offset)).strftime('%Y%m%d')
        path = os.path.join(RAW_OM, f'{d}.json')
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in data.get('matches', {}).items():
                if k not in merged:
                    merged[k] = v
        except Exception as e:
            print(f'  WARN 读 {path} 失败: {e}')
    return merged


def extract_pinnacle_odds(odds_dict):
    """从 OM 单场比赛 odds 抽 Pinnacle/HKJC close"""
    pin = odds_dict.get('pinnacle', {}) or {}
    hkjc = odds_dict.get('hkjc', {}) or {}
    return {
        'pinnacle_w': pin.get('odds_w', 0) or 0,
        'pinnacle_d': pin.get('odds_d', 0) or 0,
        'pinnacle_l': pin.get('odds_l', 0) or 0,
        'pinnacle_margin': pin.get('margin', 0) or 0,
        'hkjc_w': hkjc.get('odds_w', 0) or 0,
        'hkjc_d': hkjc.get('odds_d', 0) or 0,
        'hkjc_l': hkjc.get('odds_l', 0) or 0,
    }


def implied_from_odds(odds_w, odds_d, odds_l):
    """从欧赔反推隐含概率（去抽水归一化）"""
    if not odds_w or not odds_d or not odds_l:
        return None, None, None
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None
    raw_w = 1.0 / odds_w
    raw_d = 1.0 / odds_d
    raw_l = 1.0 / odds_l
    total = raw_w + raw_d + raw_l
    return raw_w / total, raw_d / total, raw_l / total


def build_predictions(om_matches, fetch_date):
    """从 OM matches 生成 INSERT 行
    
    优先级：
    1. avg.implied_prob（百家平均隐含概率，去抽水）
    2. pinnacle 赔率反推隐含概率
    3. hkjc 赔率反推隐含概率
    """
    rows = []
    n_avg = 0
    n_pin = 0
    n_hkjc = 0
    n_skip = 0
    
    for key, m in om_matches.items():
        info = m.get('info', {})
        odds = m.get('odds', {})
        avg = odds.get('avg', {}) or {}
        imp = avg.get('implied_prob', {}) or {}

        p_w, p_d, p_l = None, None, None
        odds_source = 'avg'
        odds_w_used = avg.get('odds_w', 0) or 0
        odds_d_used = avg.get('odds_d', 0) or 0
        odds_l_used = avg.get('odds_l', 0) or 0
        margin_used = avg.get('margin', 0) or 0

        # 优先级1: avg implied_prob
        if imp.get('w') and imp.get('d') and imp.get('l'):
            p_w, p_d, p_l = imp['w'], imp['d'], imp['l']
            odds_source = 'avg'
            odds_w_used = avg.get('odds_w', 0) or 0
            odds_d_used = avg.get('odds_d', 0) or 0
            odds_l_used = avg.get('odds_l', 0) or 0
            margin_used = avg.get('margin', 0) or 0
            n_avg += 1
        else:
            # 优先级2: pinnacle 赔率反推
            ext = extract_pinnacle_odds(odds)
            if ext['pinnacle_w'] and ext['pinnacle_d'] and ext['pinnacle_l']:
                p_w, p_d, p_l = implied_from_odds(
                    ext['pinnacle_w'], ext['pinnacle_d'], ext['pinnacle_l'])
                if p_w is not None:
                    odds_source = 'pinnacle'
                    odds_w_used = ext['pinnacle_w']
                    odds_d_used = ext['pinnacle_d']
                    odds_l_used = ext['pinnacle_l']
                    margin_used = ext['pinnacle_margin']
                    n_pin += 1
                else:
                    ext = None  # fall through to hkjc
            if p_w is None:
                # 优先级3: hkjc 赔率反推
                hkjc = odds.get('hkjc', {}) or {}
                hkjc_w = hkjc.get('odds_w', 0) or 0
                hkjc_d = hkjc.get('odds_d', 0) or 0
                hkjc_l = hkjc.get('odds_l', 0) or 0
                if hkjc_w and hkjc_d and hkjc_l:
                    p_w, p_d, p_l = implied_from_odds(hkjc_w, hkjc_d, hkjc_l)
                    if p_w is not None:
                        odds_source = 'hkjc'
                        odds_w_used = hkjc_w
                        odds_d_used = hkjc_d
                        odds_l_used = hkjc_l
                        margin_used = 0
                        n_hkjc += 1

        if p_w is None:
            n_skip += 1
            continue

        # 归一
        s = p_w + p_d + p_l
        p_w, p_d, p_l = p_w/s, p_d/s, p_l/s

        # 联赛名（用于分层参数）
        league_name = info.get('league', '') or ''

        # 算 λ + 泊松（联赛分层参数）
        lp = get_league_params(league_name)
        pw = lp.get('poisson_weight', POISSON_WEIGHT)
        iw = lp.get('implied_weight', IMPLIED_WEIGHT)
        lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l, league_name)
        p_pois_w, p_pois_d, p_pois_l = poisson_match_probs(lam_h, lam_a)

        # final（联赛权重）
        f_w = pw * p_pois_w + iw * p_w
        f_d = pw * p_pois_d + iw * p_d
        f_l = pw * p_pois_l + iw * p_l
        s2 = f_w + f_d + f_l
        f_w, f_d, f_l = f_w/s2, f_d/s2, f_l/s2

        # prediction（概率最高方向）
        if f_w >= f_d and f_w >= f_l:
            prediction = '主胜'
            pred_prob = f_w
        elif f_l >= f_d:
            prediction = '客胜'
            pred_prob = f_l
        else:
            prediction = '平局'
            pred_prob = f_d

        # 校准概率 + 信心等级
        cal_prob = calibrate_prob(pred_prob)
        cal_tier = confidence_tier(cal_prob)

        # EV方向 = 概率最高方向（用于命中率统计）
        # 价值方向 = 模型概率 vs 市场隐含概率差值最大的方向（用于投注价值）
        best_direction_cn = prediction

        # 解析 kickoff 实际日期
        kickoff = info.get('kickoff', '')
        ko_date, ko_time = parse_kickoff_date(kickoff, fetch_date)

        # 抽 Pinnacle/HKJC
        ext = extract_pinnacle_odds(odds)

        rows.append({
            'date': ko_date,
            'kickoff_time': f'{ko_date} {ko_time}',
            'league': info.get('league', '') or '',
            'home_team': info.get('home', ''),
            'away_team': info.get('away', ''),
            'prediction': prediction,
            'prediction_prob': round(pred_prob, 3),
            'odds_win': odds_w_used,
            'odds_draw': odds_d_used,
            'odds_loss': odds_l_used,
            'poisson_win': round(p_pois_w, 3),
            'poisson_draw': round(p_pois_d, 3),
            'poisson_loss': round(p_pois_l, 3),
            'final_win': round(f_w, 3),
            'final_draw': round(f_d, 3),
            'final_loss': round(f_l, 3),
            'implied_prob_w': round(p_w, 3),
            'implied_prob_d': round(p_d, 3),
            'implied_prob_l': round(p_l, 3),
            'home_lambda': lam_h,
            'away_lambda': lam_a,
            'pinnacle_close_w': ext['pinnacle_w'],
            'pinnacle_close_d': ext['pinnacle_d'],
            'pinnacle_close_l': ext['pinnacle_l'],
            'pinnacle_margin': ext['pinnacle_margin'],
            'hkjc_close_w': ext['hkjc_w'],
            'hkjc_close_d': ext['hkjc_d'],
            'hkjc_close_l': ext['hkjc_l'],
            'avg_odds_close_w': odds_w_used if odds_source == 'avg' else (avg.get('odds_w', 0) or 0),
            'avg_odds_close_d': odds_d_used if odds_source == 'avg' else (avg.get('odds_d', 0) or 0),
            'avg_odds_close_l': odds_l_used if odds_source == 'avg' else (avg.get('odds_l', 0) or 0),
            'avg_margin': margin_used if odds_source == 'avg' else (avg.get('margin', 0) or 0),
            'confidence_tier': cal_tier,
            'calibrated_prob': round(cal_prob, 3),
            'best_direction_cn': best_direction_cn,
            'source': 'om_only',
            'odds_source': odds_source,
            'ah_handicap': 0,
            'ah_home_water': 0,
            'ah_away_water': 0,
            'ah_source': '',
        })
    
    if n_pin or n_hkjc or n_skip:
        print(f'  📊 赔率源: avg={n_avg} pinnacle={n_pin} hkjc={n_hkjc} skip={n_skip}')
    return rows


def insert_predictions(rows, db_path):
    """UPSERT 到 poisson_predictions（按 date+home+away 唯一约束，COALESCE 保留旧好数据）"""
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT'),
                         ('confidence_tier', 'TEXT'), ('calibrated_prob', 'REAL'), ('best_direction_cn', 'TEXT')]:
        try:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass
    
    # 清掉脏数据（日期格式不正确的记录）
    cur.execute("SELECT DISTINCT date FROM poisson_predictions WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'")
    bad_dates = [r[0] for r in cur.fetchall()]
    if bad_dates:
        placeholders = ",".join("?" * len(bad_dates))
        cur.execute(f"DELETE FROM poisson_predictions WHERE date IN ({placeholders})", bad_dates)
        print(f"  [清理] 脏日期记录: {cur.rowcount} 条 (dates: {bad_dates[:3]}...)")
    
    # 清理已有重复记录：同(date, home, away)保留id最小的（最早写入的数据最完整）
    cur.execute("""
        DELETE FROM poisson_predictions
        WHERE id NOT IN (
            SELECT MIN(id) FROM poisson_predictions
            GROUP BY date, home_team, away_team
        )
    """)
    if cur.rowcount:
        print(f"  [去重] 清理重复记录: {cur.rowcount} 条")
    
    # 创建唯一索引（清理重复后才能成功）
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pred_uq
        ON poisson_predictions(date, home_team, away_team)
    """)
    
    # UPSERT: INSERT OR REPLACE，用 COALESCE 保留旧值
    # 规则：kickoff 新值非"待定"/"00:00"才覆盖；william/pinnacle/avg 新值非0/None才覆盖
    def _ko(new_val):
        """kickoff: 新值有效则覆盖，否则保留旧值"""
        if new_val and new_val not in ('待定', '00:00', ''):
            return new_val
        return None  # COALESCE 会取旧值
    
    def _num(new_val):
        """数值字段: 新值非0非None则覆盖"""
        if new_val is not None and new_val != 0:
            return new_val
        return None
    
    upserted = 0
    inserted = 0
    for r in rows:
        # 先查旧记录
        cur.execute("""
            SELECT kickoff_time, william_1x2_w, william_1x2_d, william_1x2_l,
                   william_ah_handicap, william_ah_home_water, william_ah_away_water,
                   william_ou_over, william_ou_line, william_ou_under,
                   pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
                   avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                   pin_ah_handicap, pin_ah_home_water, pin_ah_away_water,
                   pin_ou_line, pin_ou_over, pin_ou_under
            FROM poisson_predictions
            WHERE date = ? AND home_team = ? AND away_team = ?
        """, (r['date'], r['home_team'], r['away_team']))
        old = cur.fetchone()
        
        if old:
            # 有旧记录：DELETE + INSERT（带 COALESCE 逻辑在 Python 侧合并）
            old_ko = old[0]
            # kickoff: 新值有效覆盖，否则保留旧值
            new_ko = _ko(r['kickoff_time'])
            final_ko = new_ko if new_ko else old_ko
            
            # 数值字段合并
            def _merge(new_val, old_val):
                nv = _num(new_val)
                return nv if nv is not None else old_val
            
            final_william_w = _merge(r.get('william_1x2_w'), old[1])
            final_william_d = _merge(r.get('william_1x2_d'), old[2])
            final_william_l = _merge(r.get('william_1x2_l'), old[3])
            final_william_ah_h = _merge(r.get('william_ah_handicap'), old[4])
            final_william_ah_hw = _merge(r.get('william_ah_home_water'), old[5])
            final_william_ah_aw = _merge(r.get('william_ah_away_water'), old[6])
            final_william_ou_o = _merge(r.get('william_ou_over'), old[7])
            final_william_ou_l = _merge(r.get('william_ou_line'), old[8])
            final_william_ou_u = _merge(r.get('william_ou_under'), old[9])
            final_pin_w = _merge(r['pinnacle_close_w'], old[10])
            final_pin_d = _merge(r['pinnacle_close_d'], old[11])
            final_pin_l = _merge(r['pinnacle_close_l'], old[12])
            final_avg_w = _merge(r['avg_odds_close_w'], old[13])
            final_avg_d = _merge(r['avg_odds_close_d'], old[14])
            final_avg_l = _merge(r['avg_odds_close_l'], old[15])
            final_pin_ah_h = _merge(r.get('pin_ah_handicap'), old[16])
            final_pin_ah_hw = _merge(r.get('pin_ah_home_water'), old[17])
            final_pin_ah_aw = _merge(r.get('pin_ah_away_water'), old[18])
            final_pin_ou_l = _merge(r.get('pin_ou_line'), old[19])
            final_pin_ou_o = _merge(r.get('pin_ou_over'), old[20])
            final_pin_ou_u = _merge(r.get('pin_ou_under'), old[21])
            
            # 删除旧记录后插入合并后的记录
            cur.execute("DELETE FROM poisson_predictions WHERE date = ? AND home_team = ? AND away_team = ?",
                        (r['date'], r['home_team'], r['away_team']))
            cur.execute("""
                INSERT INTO poisson_predictions (
                    date, kickoff_time, league, home_team, away_team,
                    prediction, prediction_prob,
                    odds_win, odds_draw, odds_loss,
                    poisson_win, poisson_draw, poisson_loss,
                    final_win, final_draw, final_loss,
                    implied_prob_w, implied_prob_d, implied_prob_l,
                    home_lambda, away_lambda,
                    pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
                    pinnacle_margin,
                    hkjc_close_w, hkjc_close_d, hkjc_close_l,
                    avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                    avg_margin,
                    source, odds_source,
                    ah_handicap, ah_home_water, ah_away_water, ah_source,
                    william_1x2_w, william_1x2_d, william_1x2_l,
                    william_ah_handicap, william_ah_home_water, william_ah_away_water,
                    william_ou_over, william_ou_line, william_ou_under,
                    pin_ah_handicap, pin_ah_home_water, pin_ah_away_water,
                    pin_ou_line, pin_ou_over, pin_ou_under,
                    confidence_tier, calibrated_prob, best_direction_cn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r['date'], final_ko, r['league'], r['home_team'], r['away_team'],
                r['prediction'], r['prediction_prob'],
                r['odds_win'], r['odds_draw'], r['odds_loss'],
                r['poisson_win'], r['poisson_draw'], r['poisson_loss'],
                r['final_win'], r['final_draw'], r['final_loss'],
                r['implied_prob_w'], r['implied_prob_d'], r['implied_prob_l'],
                r['home_lambda'], r['away_lambda'],
                final_pin_w, final_pin_d, final_pin_l,
                r['pinnacle_margin'],
                r['hkjc_close_w'], r['hkjc_close_d'], r['hkjc_close_l'],
                final_avg_w, final_avg_d, final_avg_l,
                r['avg_margin'],
                r['source'], r['odds_source'],
                r['ah_handicap'], r['ah_home_water'], r['ah_away_water'], r['ah_source'],
                final_william_w, final_william_d, final_william_l,
                final_william_ah_h, final_william_ah_hw, final_william_ah_aw,
                final_william_ou_o, final_william_ou_l, final_william_ou_u,
                final_pin_ah_h, final_pin_ah_hw, final_pin_ah_aw,
                final_pin_ou_l, final_pin_ou_o, final_pin_ou_u,
                r.get('confidence_tier', ''), r.get('calibrated_prob', 0), r.get('best_direction_cn', ''),
            ))
            upserted += 1
        else:
            # 无旧记录：直接INSERT
            cur.execute("""
                INSERT INTO poisson_predictions (
                    date, kickoff_time, league, home_team, away_team,
                    prediction, prediction_prob,
                    odds_win, odds_draw, odds_loss,
                    poisson_win, poisson_draw, poisson_loss,
                    final_win, final_draw, final_loss,
                    implied_prob_w, implied_prob_d, implied_prob_l,
                    home_lambda, away_lambda,
                    pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
                    pinnacle_margin,
                    hkjc_close_w, hkjc_close_d, hkjc_close_l,
                    avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                    avg_margin,
                    source, odds_source,
                    ah_handicap, ah_home_water, ah_away_water, ah_source,
                    confidence_tier, calibrated_prob, best_direction_cn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r['date'], r['kickoff_time'], r['league'], r['home_team'], r['away_team'],
                r['prediction'], r['prediction_prob'],
                r['odds_win'], r['odds_draw'], r['odds_loss'],
                r['poisson_win'], r['poisson_draw'], r['poisson_loss'],
                r['final_win'], r['final_draw'], r['final_loss'],
                r['implied_prob_w'], r['implied_prob_d'], r['implied_prob_l'],
                r['home_lambda'], r['away_lambda'],
                r['pinnacle_close_w'], r['pinnacle_close_d'], r['pinnacle_close_l'],
                r['pinnacle_margin'],
                r['hkjc_close_w'], r['hkjc_close_d'], r['hkjc_close_l'],
                r['avg_odds_close_w'], r['avg_odds_close_d'], r['avg_odds_close_l'],
                r['avg_margin'],
                r['source'], r['odds_source'],
                r['ah_handicap'], r['ah_home_water'], r['ah_away_water'], r['ah_source'],
                r.get('confidence_tier', ''), r.get('calibrated_prob', 0), r.get('best_direction_cn', '')
            ))
            inserted += 1
    conn.commit()
    conn.close()
    return inserted, upserted


def main():
    parser = argparse.ArgumentParser(description='从 OM 赔率生成泊松预测，UPSERT 到 DB')
    parser.add_argument('--date', type=str, required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--db', type=str, default=None, help='数据库路径（默认 data/football.db）')
    args = parser.parse_args()

    db_path = args.db or os.environ.get('FOOTBALL_DB_PATH', DB_PATH)
    if not os.path.exists(db_path):
        print(f'[ERROR] DB 不存在: {db_path}')
        return 1

    load_calibration()
    if _league_params:
        print(f'🔧 校准参数: {len(_league_params)} 个联赛, isotonic={len(_calib_map)} 点')
    print(f'💾 DB: {db_path}')

    matches = load_om_matches(args.date)
    print(f'📥 OM matches: {len(matches)} 场')
    if not matches:
        print('⚠️ 无 OM 数据，跳过')
        return 0

    rows = build_predictions(matches, args.date)
    print(f'🧮 生成预测: {len(rows)} 场')

    ah_index = load_ah_for_date(args.date)
    if ah_index:
        filled = 0
        for r in rows:
            home = r.get('home_team', '')
            away = r.get('away_team', '')
            best = None
            best_sim = 0
            for ah in ah_index:
                ah_home = ah.get('home', '')
                ah_away = ah.get('away', '')
                close = ah.get('close', {})
                if not close:
                    continue
                h = close.get('handicap', 0)
                hw = close.get('home_w', 0)
                aw = close.get('away_w', 0)
                if h == 0 and hw == 0 and aw == 0:
                    continue
                sim_fwd = (name_sim(ah_home, home) + name_sim(ah_away, away)) / 2
                sim_rev = (name_sim(ah_home, away) + name_sim(ah_away, home)) / 2
                sim = max(sim_fwd, sim_rev)
                if sim > best_sim:
                    best_sim = sim
                    best = close
            if best and best_sim >= 0.4:
                r['ah_handicap'] = best.get('handicap', 0) or 0
                r['ah_home_water'] = best.get('home_w', 0) or 0
                r['ah_away_water'] = best.get('away_w', 0) or 0
                r['ah_source'] = 'avg'
                filled += 1
        print(f'🎯 AH匹配: {filled}/{len(rows)} 场带AH数据入库')

    inserted, upserted = insert_predictions(rows, db_path)
    print(f'✅ 新增: {inserted} 场')
    print(f'🔄 更新（UPSERT）: {upserted} 场')

    return 0


if __name__ == '__main__':
    sys.exit(main())
