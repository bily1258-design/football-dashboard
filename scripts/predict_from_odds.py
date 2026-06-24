#!/usr/bin/env python3
"""predict_from_odds.py — 从 OM 赔率反推泊松预测，INSERT 到 DB

职责：
- 读 data/raw/oddsmagnet/{date}.json
- 对每场比赛：
  - 优先用 OM 百家平均 implied(去抽水) 作为市场预期
  - avg 为空时，从 Pinnacle 赔率反推隐含概率（1/odds，去抽水归一化）
  - 用 implied 反推实力差距 → 估算 λ_home/λ_away
  - 用泊松分布算 P(主胜/平/客胜)
  - final = 0.7×poisson + 0.3×implied
  - prediction = max(final) 方向
- INSERT 到 poisson_predictions 表（按 date+home+away 去重）

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

# === 算法常量（基于历史 DB 5/15~6/5 数据反推）===
BASE_TOTAL_GOALS = 2.4    # 联赛平均总进球（主+客）
HOME_ADV = 0.15            # 主场加成（λ_home 多 0.15）
SKILL_FACTOR = 0.6         # 实力调整系数
POISSON_WEIGHT = 0.5 # final = 0.5*poisson + 0.5*implied
IMPLIED_WEIGHT = 0.5
LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0


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


def estimate_lambdas(imp_w, imp_d, imp_l):
    """从 implied 反推 λ_home / λ_away"""
    base = BASE_TOTAL_GOALS / 2
    denom = max(imp_w + imp_l, 0.01)
    share_h = imp_w / denom
    skill_adj = SKILL_FACTOR * (share_h - 0.5)
    lam_h = base + HOME_ADV + skill_adj
    lam_a = base - HOME_ADV - skill_adj
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

        # 算 λ + 泊松
        lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l)
        p_pois_w, p_pois_d, p_pois_l = poisson_match_probs(lam_h, lam_a)

        # final
        f_w = POISSON_WEIGHT * p_pois_w + IMPLIED_WEIGHT * p_w
        f_d = POISSON_WEIGHT * p_pois_d + IMPLIED_WEIGHT * p_d
        f_l = POISSON_WEIGHT * p_pois_l + IMPLIED_WEIGHT * p_l
        s2 = f_w + f_d + f_l
        f_w, f_d, f_l = f_w/s2, f_d/s2, f_l/s2

        # prediction
        if f_w >= f_d and f_w >= f_l:
            prediction = '主胜'
            pred_prob = f_w
        elif f_l >= f_d:
            prediction = '客胜'
            pred_prob = f_l
        else:
            prediction = '平局'
            pred_prob = f_d

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
    """INSERT 到 poisson_predictions（A1: 入库前清掉 om_only 旧记录 → 治本去重）"""
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT')]:
        try:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass
    
    # A1: 清掉 om_only 旧记录
    dates_to_clean = sorted({r["date"] for r in rows})
    if dates_to_clean:
        placeholders = ",".join("?" * len(dates_to_clean))
        cur.execute(
            f"DELETE FROM poisson_predictions WHERE date IN ({placeholders}) AND source = 'om_only'",
            dates_to_clean
        )
        print(f"  [A1] 清掉 om_only 旧记录: {cur.rowcount} 条 (date: {', '.join(dates_to_clean)})")
    
    # 清掉脏数据（日期格式不正确的记录，如 "2026-2026-"）
    cur.execute("SELECT DISTINCT date FROM poisson_predictions WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'")
    bad_dates = [r[0] for r in cur.fetchall()]
    if bad_dates:
        placeholders = ",".join("?" * len(bad_dates))
        cur.execute(f"DELETE FROM poisson_predictions WHERE date IN ({placeholders})", bad_dates)
        print(f"  [A1] 清掉脏日期记录: {cur.rowcount} 条 (dates: {bad_dates[:3]}...)")
    
    inserted = 0
    skipped = 0
    for r in rows:
        cur.execute("""
            SELECT id FROM poisson_predictions
            WHERE date = ? AND home_team = ? AND away_team = ?
        """, (r['date'], r['home_team'], r['away_team']))
        if cur.fetchone():
            skipped += 1
            continue
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
                ah_handicap, ah_home_water, ah_away_water, ah_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            r['ah_handicap'], r['ah_home_water'], r['ah_away_water'], r['ah_source']
        ))
        inserted += 1
    conn.commit()
    conn.close()
    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(description='从 OM 赔率生成泊松预测，INSERT 到 DB')
    parser.add_argument('--date', type=str, required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--db', type=str, default=None, help='数据库路径（默认 data/football.db）')
    args = parser.parse_args()

    db_path = args.db or os.environ.get('FOOTBALL_DB_PATH', DB_PATH)
    if not os.path.exists(db_path):
        print(f'[ERROR] DB 不存在: {db_path}')
        return 1

    print(f'📊 预测日期: {args.date}')
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

    inserted, skipped = insert_predictions(rows, db_path)
    print(f'✅ 新增: {inserted} 场')
    print(f'⏭️ 跳过（已存在）: {skipped} 场')

    return 0


if __name__ == '__main__':
    sys.exit(main())
