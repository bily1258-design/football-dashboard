#!/usr/bin/env python3
"""merge_and_build.py — processed → results.json + index.html

读取 data/processed/*.json（优先）或直读 football.db
输出 data/results.json + docs/index.html

职责单一：只管数据聚合 + 页面渲染
"""

import os, sys, json, sqlite3, re, math, argparse
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
from collections import defaultdict

from team_aliases import canonical as _canonical, match_key as _match_key

# ===== 联赛白名单（与 extract_fids_from_live.py 保持一致） =====
LEAGUE_WHITELIST = {
    # 国内
    '中超', '中甲',
    # 韩国
    'K1联赛', 'K2联赛',
    # 北欧（北单覆盖）
    '芬超', '芬甲', '冰岛超', '瑞典超', '挪甲', '爱甲',
    # 美洲
    '美冠', '巴乙', '厄甲',
    # 国家队赛事
    '世界杯',
}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
PROCESSED_DIR = os.path.join(REPO_DIR, "data", "processed")
DATA_DIR = os.path.join(REPO_DIR, "data")
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DB_PATH = os.environ.get('FOOTBALL_DB_PATH',
    os.path.join(REPO_DIR, 'data', 'football.db'))

WEEKDAY_CN = ['周一','周二','周三','周四','周五','周六','周日']


def _window_date(kickoff: str, fallback_date: str) -> str:
    """竞彩窗口归日：返回原始日期，不做调整。
    DB日期已由手动修正，无需应用竞彩窗口规则（00:00-11:59 归前一天）。
    函数保留以备将来启用。
    """
    return fallback_date


def load_from_processed(max_days=999) -> dict:
    """从 processed 目录加载数据，并做竞彩窗口调整（凌晨00:00-11:59归前一天）"""
    by_date = {}
    if not os.path.exists(PROCESSED_DIR):
        return by_date
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y%m%d')
    for fname in sorted(os.listdir(PROCESSED_DIR), reverse=True):
        if not fname.endswith('.json'):
            continue
        date_key = fname.replace('.json', '')
        if len(date_key) == 8 and date_key < cutoff:
            continue
        path = os.path.join(PROCESSED_DIR, fname)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        d = data.get('date', f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}")
        records = data.get('records', [])
        # 竞彩窗口归日：凌晨00:00-11:59的比赛归前一天（窗口12:00→次日11:59）
        for rec in records:
            d_new = _window_date(rec.get('kickoff', ''), rec.get('date', d))
            # source映射：om_only → beidan
            if rec.get('source') == 'om_only':
                rec['source'] = 'beidan'
            if d_new not in by_date:
                by_date[d_new] = []
            by_date[d_new].append(rec)
    return by_date


def _merge_missing(kept, discarded):
    """将 discarded 中有而 kept 中缺失的数据合并到 kept"""
    # 补kickoff
    ko = kept.get('kickoff', '')
    if not ko or ko == '待定':
        dko = discarded.get('kickoff', '')
        if dko and dko != '待定':
            kept['kickoff'] = dko
    # 补william_1x2
    w1 = kept.get('william_1x2', {})
    if not w1 or (w1.get('w', 0) == 0 and w1.get('d', 0) == 0 and w1.get('l', 0) == 0):
        dw1 = discarded.get('william_1x2', {})
        if dw1 and (dw1.get('w', 0) > 0 or dw1.get('d', 0) > 0 or dw1.get('l', 0) > 0):
            kept['william_1x2'] = dw1
    # 补william_ah
    wah = kept.get('william_ah', {})
    if not wah or (wah.get('handicap') is None or wah.get('handicap') == 0):
        dwah = discarded.get('william_ah', {})
        if dwah and dwah.get('handicap') is not None and dwah.get('handicap') != 0:
            kept['william_ah'] = dwah
    # 补william_ou
    wou = kept.get('william_ou', {})
    if not wou or (wou.get('over', 0) == 0):
        dwou = discarded.get('william_ou', {})
        if dwou and dwou.get('over', 0) > 0:
            kept['william_ou'] = dwou
    # 补pin_ah
    pah = kept.get('pin_ah', {})
    if not pah or (pah.get('handicap') is None or pah.get('handicap') == 0):
        dpah = discarded.get('pin_ah', {})
        if dpah and dpah.get('handicap') is not None and dpah.get('handicap') != 0:
            kept['pin_ah'] = dpah
    # 补pin_ou
    pou = kept.get('pin_ou', {})
    if not pou or (pou.get('line') is None or pou.get('line') == 0):
        dpou = discarded.get('pin_ou', {})
        if dpou and dpou.get('line') is not None and dpou.get('line') != 0:
            kept['pin_ou'] = dpou


def load_from_db(db_path: str, max_days=999) -> dict:
    """fallback: 直读DB"""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # 确保DB有所有新列（兼容旧DB）
    _NEW_COLUMNS = [
        ('pin_ah_handicap', 'REAL'), ('pin_ah_home_water', 'REAL'), ('pin_ah_away_water', 'REAL'),
        ('pin_ou_line', 'REAL'), ('pin_ou_over', 'REAL'), ('pin_ou_under', 'REAL'),
        ('pin_ah_open_handicap', 'REAL'), ('pin_ah_open_home_water', 'REAL'), ('pin_ah_open_away_water', 'REAL'),
        ('pin_ou_open_line', 'REAL'), ('pin_ou_open_over', 'REAL'), ('pin_ou_open_under', 'REAL'),
        ('ou_over', 'REAL'), ('ou_line', 'REAL'), ('ou_under', 'REAL'),
        ('ou_open_over', 'REAL'), ('ou_open_line', 'REAL'), ('ou_open_under', 'REAL'),
        ('liji_ou_over', 'REAL'), ('liji_ou_line', 'REAL'), ('liji_ou_under', 'REAL'),
        ('liji_ou_open_over', 'REAL'), ('liji_ou_open_line', 'REAL'), ('liji_ou_open_under', 'REAL'),
        ('ms_ou_over', 'REAL'), ('ms_ou_line', 'REAL'), ('ms_ou_under', 'REAL'),
        ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL'),
        ('pinnacle_open_w', 'REAL'), ('pinnacle_open_d', 'REAL'), ('pinnacle_open_l', 'REAL'),
        ('hkjc_ah_handicap', 'REAL'), ('hkjc_ah_home_water', 'REAL'), ('hkjc_ah_away_water', 'REAL'),
        ('hkjc_ah_open_handicap', 'REAL'), ('hkjc_ah_open_home_water', 'REAL'), ('hkjc_ah_open_away_water', 'REAL'),
        ('hkjc_ou_line', 'REAL'), ('hkjc_ou_over', 'REAL'), ('hkjc_ou_under', 'REAL'),
        ('hkjc_ou_open_line', 'REAL'), ('hkjc_ou_open_over', 'REAL'), ('hkjc_ou_open_under', 'REAL'),
        ('hkjc_open_w', 'REAL'), ('hkjc_open_d', 'REAL'), ('hkjc_open_l', 'REAL'),
        ('william_1x2_w', 'REAL'), ('william_1x2_d', 'REAL'), ('william_1x2_l', 'REAL'),
        ('liji_1x2_w', 'REAL'), ('liji_1x2_d', 'REAL'), ('liji_1x2_l', 'REAL'),
        ('liji_1x2_open_w', 'REAL'), ('liji_1x2_open_d', 'REAL'), ('liji_1x2_open_l', 'REAL'),
        ('ms_1x2_w', 'REAL'), ('ms_1x2_d', 'REAL'), ('ms_1x2_l', 'REAL'),
        ('ms_1x2_open_w', 'REAL'), ('ms_1x2_open_d', 'REAL'), ('ms_1x2_open_l', 'REAL'),
        ('william_ah_handicap', 'REAL'), ('william_ah_home_water', 'REAL'), ('william_ah_away_water', 'REAL'),
        ('william_ah_open_handicap', 'REAL'), ('william_ah_open_home_water', 'REAL'), ('william_ah_open_away_water', 'REAL'),
        ('william_ou_over', 'REAL'), ('william_ou_line', 'REAL'), ('william_ou_under', 'REAL'),
        ('bet365_open_w', 'REAL'), ('bet365_open_d', 'REAL'), ('bet365_open_l', 'REAL'),
        ('bet365_close_w', 'REAL'), ('bet365_close_d', 'REAL'), ('bet365_close_l', 'REAL'),
        ('bet365_ah_handicap', 'REAL'), ('bet365_ah_home_water', 'REAL'), ('bet365_ah_away_water', 'REAL'),
        ('bet365_ah_open_handicap', 'REAL'), ('bet365_ah_open_home_water', 'REAL'), ('bet365_ah_open_away_water', 'REAL'),
        ('bet365_ou_line', 'REAL'), ('bet365_ou_over', 'REAL'), ('bet365_ou_under', 'REAL'),
        ('bet365_ou_open_line', 'REAL'), ('bet365_ou_open_over', 'REAL'), ('bet365_ou_open_under', 'REAL'),
    ]
    cur.execute("PRAGMA table_info(poisson_predictions)")
    existing = {row[1] for row in cur.fetchall()}
    for col, ctype in _NEW_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype} DEFAULT 0")
    conn.commit()
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d') if max_days < 999 else '2000-01-01'
    cur.execute("SELECT id, date, league, home_team, away_team, kickoff_time, \
        prediction, prediction_prob, odds_win, odds_draw, odds_loss, \
        poisson_win, poisson_draw, poisson_loss, final_win, final_draw, final_loss, \
        fusion_win, fusion_draw, fusion_loss, actual_outcome, risk_level, \
        confidence_index, reference_score, best_direction_cn, confidence_tier, calibrated_prob, source, \
        ev_win, ev_draw, ev_loss, kelly_win, kelly_draw, kelly_loss, \
        odds_ev_win, odds_ev_draw, odds_ev_loss, odds_ev_dir, ev_signal, \
        pinnacle_open_w, pinnacle_open_d, pinnacle_open_l, \
        pinnacle_close_w, pinnacle_close_d, pinnacle_close_l, \
        hkjc_close_w, hkjc_close_d, hkjc_close_l, cold_risk, odds_source, \
        home_lambda, away_lambda, home_ranking, away_ranking, \
        hhad_handicap, hhad_win, hhad_draw, hhad_loss, \
        ah_handicap, ah_home_water, ah_away_water, ah_source, \
        ah_open_handicap, ah_open_home_water, ah_open_away_water, \
        liji_handicap, liji_home_water, liji_away_water, \
        liji_open_handicap, liji_open_home_water, liji_open_away_water, \
        ms_handicap, ms_home_water, ms_away_water, \
        ms_open_handicap, ms_open_home_water, ms_open_away_water, \
        pin_ah_handicap, pin_ah_home_water, pin_ah_away_water, \
        pin_ah_open_handicap, pin_ah_open_home_water, pin_ah_open_away_water, \
        pin_ou_line, pin_ou_over, pin_ou_under, \
        pin_ou_open_line, pin_ou_open_over, pin_ou_open_under, \
        ou_over, ou_line, ou_under, \
        ou_open_over, ou_open_line, ou_open_under, \
        liji_ou_over, liji_ou_line, liji_ou_under, \
        liji_ou_open_over, liji_ou_open_line, liji_ou_open_under, \
        ms_ou_over, ms_ou_line, ms_ou_under, \
        ms_ou_open_over, ms_ou_open_line, ms_ou_open_under, \
        hkjc_open_w, hkjc_open_d, hkjc_open_l, \
        hkjc_ah_handicap, hkjc_ah_home_water, hkjc_ah_away_water, \
        hkjc_ah_open_handicap, hkjc_ah_open_home_water, hkjc_ah_open_away_water, \
        hkjc_ou_line, hkjc_ou_over, hkjc_ou_under, \
        hkjc_ou_open_line, hkjc_ou_open_over, hkjc_ou_open_under, \
        william_1x2_w, william_1x2_d, william_1x2_l, \
        william_ah_handicap, william_ah_home_water, william_ah_away_water, \
        william_ah_open_handicap, william_ah_open_home_water, william_ah_open_away_water, \
        william_ou_over, william_ou_line, william_ou_under, \
        liji_1x2_w, liji_1x2_d, liji_1x2_l, \
        liji_1x2_open_w, liji_1x2_open_d, liji_1x2_open_l, \
        ms_1x2_w, ms_1x2_d, ms_1x2_l, \
        ms_1x2_open_w, ms_1x2_open_d, ms_1x2_open_l, \
        bet365_open_w, bet365_open_d, bet365_open_l, \
        bet365_close_w, bet365_close_d, bet365_close_l, \
        bet365_ah_handicap, bet365_ah_home_water, bet365_ah_away_water, \
        bet365_ah_open_handicap, bet365_ah_open_home_water, bet365_ah_open_away_water, \
        bet365_ou_line, bet365_ou_over, bet365_ou_under, \
        bet365_ou_open_line, bet365_ou_open_over, bet365_ou_open_under, \
        avg_margin, ev_value, risk_warning, cold_signals, deviation_analysis \
        FROM poisson_predictions WHERE date >= ? ORDER BY date DESC, kickoff_time, id", (cutoff,))
    by_date = {}
    for r in cur.fetchall():
        d = dict(r)
        date = d['date']
        # 竞彩窗口归日：凌晨00:00-11:59的比赛归前一天
        wd = _window_date(d.get('kickoff_time', ''), date)
        if wd not in by_date:
            by_date[wd] = []
        # 简化序列化
        outcome = d.get('actual_outcome', '') or ''
        m = re.search(r'(\d+-\d+)', outcome)
        score = m.group(1) if m else ''
        result = '主胜' if '主胜' in outcome else ('客胜' if '客胜' in outcome else ('平局' if '平局' in outcome else ''))
        # EV方向: 直接用DB的best_direction_cn（由value_bet.py计算，已考虑赔率优势∩概率优势）
        ev_dir = d.get('best_direction_cn', '') or '主胜'
        ev_hit = (ev_dir == result) if result else False
        fw = d.get('final_win', 0) or 0
        fd = d.get('final_draw', 0) or 0
        fl = d.get('final_loss', 0) or 0
        # 如果 final_* 全为0，降级使用 fusion_*（赔率pipeline未运行时 fusion 已有值）
        if fw == 0 and fd == 0 and fl == 0:
            fw = d.get('fusion_win', 0) or 0
            fd = d.get('fusion_draw', 0) or 0
            fl = d.get('fusion_loss', 0) or 0
        prob_dir = '主胜' if fw >= fd and fw >= fl else ('客胜' if fl >= fw and fl >= fd else '平局')
        # 如果 prediction_prob 为0但 final_*（或降级的 fusion_*）有值，取最大值作为预测概率
        raw_pred_prob = d.get('prediction_prob') or 0
        if raw_pred_prob == 0 and (fw > 0 or fd > 0 or fl > 0):
            raw_pred_prob = max(fw, fd, fl)
        prob_hit = (prob_dir == result) if result else False
        ci = d.get('confidence_index') or 0
        stars = min(5, max(1, round(ci * 5))) if isinstance(ci, (int, float)) and ci > 0 else 0
        # 信心分层推算：DB 为空时从 risk_level / ev_signal 衍生
        _raw_tier = d.get('confidence_tier', '') or ''
        if not _raw_tier:
            rl = d.get('risk_level', '') or ''
            if rl == '高':
                _raw_tier = 'high'
            elif rl == '中':
                _raw_tier = 'medium'
            elif rl == '低':
                _raw_tier = 'low'
            else:
                es = d.get('ev_signal', '') or ''
                if '双重确认' in es:
                    _raw_tier = 'medium'
                elif '市场优先' in es:
                    _raw_tier = 'medium'
                elif '模型' in es or '降级' in es:
                    _raw_tier = 'low'
                else:
                    _raw_tier = 'very_low'
        # calibrated_prob 推算：DB 为空时用最终/融合概率最大值估算
        _raw_cp = d.get('calibrated_prob', 0) or 0
        if _raw_cp == 0:
            _raw_cp = round(max(fw, fd, fl), 3)
        by_date[wd].append({
            'id': d['id'], 'date': date, 'league': d.get('league',''),
            'home': d['home_team'], 'away': d['away_team'],
            'kickoff': d.get('kickoff_time',''), 'prediction': d.get('prediction',''),
            'prediction_prob': round(raw_pred_prob, 3),
            'odds': {'w': d.get('odds_win',0) or 0, 'd': d.get('odds_draw',0) or 0, 'l': d.get('odds_loss',0) or 0},
            'poisson': {'w': round(d.get('poisson_win',0) or 0, 3), 'd': round(d.get('poisson_draw',0) or 0, 3), 'l': round(d.get('poisson_loss',0) or 0, 3)},
            'final_prob': {'w': round(fw,3), 'd': round(fd,3), 'l': round(fl,3)},
            'fusion_prob': {'w': round(d.get('fusion_win',0) or 0,3), 'd': round(d.get('fusion_draw',0) or 0,3), 'l': round(d.get('fusion_loss',0) or 0,3)},
            'result': result, 'score': score,
            'ev_direction': ev_dir, 'ev_hit': ev_hit,
            'prob_direction': prob_dir, 'prob_hit': prob_hit,
            'fusion_direction': '主胜' if (d.get('fusion_win',0) or 0) >= (d.get('fusion_draw',0) or 0) and (d.get('fusion_win',0) or 0) >= (d.get('fusion_loss',0) or 0) else '客胜' if (d.get('fusion_loss',0) or 0) >= (d.get('fusion_win',0) or 0) else '平局',
            'ev': {'w': round(d.get('ev_win',0) or 0,4), 'd': round(d.get('ev_draw',0) or 0,4), 'l': round(d.get('ev_loss',0) or 0,4)},
            'odds_ev': {'w': round(d.get('odds_ev_win',0) or 0,4), 'd': round(d.get('odds_ev_draw',0) or 0,4), 'l': round(d.get('odds_ev_loss',0) or 0,4)},
            'ev_signal': d.get('ev_signal', '') or '',
            'kelly': {'w': round(d.get('kelly_win',0) or 0,4), 'd': round(d.get('kelly_draw',0) or 0,4), 'l': round(d.get('kelly_loss',0) or 0,4)},
            'pinnacle': {
                'w': d.get('pinnacle_close_w',0) or 0, 'd': d.get('pinnacle_close_d',0) or 0, 'l': d.get('pinnacle_close_l',0) or 0,
                'open': {'w': d.get('pinnacle_open_w',0) or 0, 'd': d.get('pinnacle_open_d',0) or 0, 'l': d.get('pinnacle_open_l',0) or 0},
            },
            'bet365': {
                'w': d.get('bet365_close_w',0) or 0, 'd': d.get('bet365_close_d',0) or 0, 'l': d.get('bet365_close_l',0) or 0,
                'open': {'w': d.get('bet365_open_w',0) or 0, 'd': d.get('bet365_open_d',0) or 0, 'l': d.get('bet365_open_l',0) or 0},
            },
            'hkjc': {
                'w': d.get('hkjc_close_w',0) or 0,
                'd': d.get('hkjc_close_d',0) or 0,
                'l': d.get('hkjc_close_l',0) or 0,
            },
            'hkjc_open': {
                'w': d.get('hkjc_open_w',0) or 0,
                'd': d.get('hkjc_open_d',0) or 0,
                'l': d.get('hkjc_open_l',0) or 0,
            },
            'risk_level': d.get('risk_level','') or '', 'stars': stars,
            'confidence_index': round(ci,2), 'reference_score': d.get('reference_score','') or '',
            'cold_risk': d.get('cold_risk','') or '', 'source': 'beidan' if d.get('source') == 'om_only' else ('jingcai' if d.get('source') in ('future_500', None, '') else d.get('source')),
            'cold_signals': d.get('cold_signals','') or '',
            'risk_warning': d.get('risk_warning','') or '',
            'actual_outcome': d.get('actual_outcome','') or '',
            'avg_margin': round(d.get('avg_margin',0) or 0, 4),
            'ev_value': round(d.get('ev_value',0) or 0, 4),
            'deviation_analysis': d.get('deviation_analysis','') or '',
            'odds_source': d.get('odds_source','had'),
            'confidence_tier': _raw_tier,
            'calibrated_prob': _raw_cp,
            'home_lambda': round(d.get('home_lambda',0) or 0,3),
            'away_lambda': round(d.get('away_lambda',0) or 0,3),
            'home_ranking': d.get('home_ranking',0) or 0,
            'away_ranking': d.get('away_ranking',0) or 0,
            'hhad': {
                'handicap': d.get('hhad_handicap', None),
                'w': d.get('hhad_win',0) or 0,
                'd': d.get('hhad_draw',0) or 0,
                'l': d.get('hhad_loss',0) or 0,
            },
            'ah': {
                'handicap': d.get('ah_handicap', None),
                'home_w': d.get('ah_home_water', 0) or 0,
                'away_w': d.get('ah_away_water', 0) or 0,
                'source': d.get('ah_source', '') or '',
                'open': {
                    'handicap': d.get('ah_open_handicap', None),
                    'home_w': d.get('ah_open_home_water', 0) or 0,
                    'away_w': d.get('ah_open_away_water', 0) or 0,
                },
            },
            'liji': {
                '1x2': {
                    'close': {
                        'w': d.get('liji_1x2_w', 0) or 0,
                        'd': d.get('liji_1x2_d', 0) or 0,
                        'l': d.get('liji_1x2_l', 0) or 0,
                    },
                    'open': {
                        'w': d.get('liji_1x2_open_w', 0) or 0,
                        'd': d.get('liji_1x2_open_d', 0) or 0,
                        'l': d.get('liji_1x2_open_l', 0) or 0,
                    },
                },
                'close': {
                    'handicap': d.get('liji_handicap', None),
                    'home_w': d.get('liji_home_water', 0) or 0,
                    'away_w': d.get('liji_away_water', 0) or 0,
                },
                'open': {
                    'handicap': d.get('liji_open_handicap', None),
                    'home_w': d.get('liji_open_home_water', 0) or 0,
                    'away_w': d.get('liji_open_away_water', 0) or 0,
                },
            },
            'ms': {
                '1x2': {
                    'close': {
                        'w': d.get('ms_1x2_w', 0) or 0,
                        'd': d.get('ms_1x2_d', 0) or 0,
                        'l': d.get('ms_1x2_l', 0) or 0,
                    },
                    'open': {
                        'w': d.get('ms_1x2_open_w', 0) or 0,
                        'd': d.get('ms_1x2_open_d', 0) or 0,
                        'l': d.get('ms_1x2_open_l', 0) or 0,
                    },
                },
                'close': {
                    'handicap': d.get('ms_handicap', None),
                    'home_w': d.get('ms_home_water', 0) or 0,
                    'away_w': d.get('ms_away_water', 0) or 0,
                },
                'open': {
                    'handicap': d.get('ms_open_handicap', None),
                    'home_w': d.get('ms_open_home_water', 0) or 0,
                    'away_w': d.get('ms_open_away_water', 0) or 0,
                },
            },
            'pin_ah': {
                'handicap': d.get('pin_ah_handicap', None),
                'home_w': d.get('pin_ah_home_water', 0) or 0,
                'away_w': d.get('pin_ah_away_water', 0) or 0,
                'open': {
                    'handicap': d.get('pin_ah_open_handicap', None),
                    'home_w': d.get('pin_ah_open_home_water', 0) or 0,
                    'away_w': d.get('pin_ah_open_away_water', 0) or 0,
                },
            },
            'pin_ou': {
                'line': d.get('pin_ou_line', None),
                'over': d.get('pin_ou_over', 0) or 0,
                'under': d.get('pin_ou_under', 0) or 0,
                'open': {
                    'line': d.get('pin_ou_open_line', None),
                    'over': d.get('pin_ou_open_over', 0) or 0,
                    'under': d.get('pin_ou_open_under', 0) or 0,
                },
            },
            'ou': {
                'over': d.get('ou_over', 0) or 0,
                'line': d.get('ou_line', None),
                'under': d.get('ou_under', 0) or 0,
                'open': {
                    'over': d.get('ou_open_over', 0) or 0,
                    'line': d.get('ou_open_line', None),
                    'under': d.get('ou_open_under', 0) or 0,
                },
            },
            'liji_ou': {
                'over': d.get('liji_ou_over', 0) or 0,
                'line': d.get('liji_ou_line', None),
                'under': d.get('liji_ou_under', 0) or 0,
                'open': {
                    'over': d.get('liji_ou_open_over', 0) or 0,
                    'line': d.get('liji_ou_open_line', None),
                    'under': d.get('liji_ou_open_under', 0) or 0,
                },
            },
            'ms_ou': {
                'over': d.get('ms_ou_over', 0) or 0,
                'line': d.get('ms_ou_line', None),
                'under': d.get('ms_ou_under', 0) or 0,
                'open': {
                    'over': d.get('ms_ou_open_over', 0) or 0,
                    'line': d.get('ms_ou_open_line', None),
                    'under': d.get('ms_ou_open_under', 0) or 0,
                },
            },
            'hkjc_ah': {
                'handicap': d.get('bet365_ah_handicap') if d.get('bet365_ah_handicap') else d.get('hkjc_ah_handicap'),
                'home_w': (d.get('bet365_ah_home_water', 0) or 0) or (d.get('hkjc_ah_home_water', 0) or 0),
                'away_w': (d.get('bet365_ah_away_water', 0) or 0) or (d.get('hkjc_ah_away_water', 0) or 0),
                'open': {
                    'handicap': d.get('bet365_ah_open_handicap') if d.get('bet365_ah_open_handicap') else d.get('hkjc_ah_open_handicap'),
                    'home_w': (d.get('bet365_ah_open_home_water', 0) or 0) or (d.get('hkjc_ah_open_home_water', 0) or 0),
                    'away_w': (d.get('bet365_ah_open_away_water', 0) or 0) or (d.get('hkjc_ah_open_away_water', 0) or 0),
                },
            },
            'hkjc_ou': {
                'line': d.get('bet365_ou_line') if d.get('bet365_ou_line') else d.get('hkjc_ou_line'),
                'over': (d.get('bet365_ou_over', 0) or 0) or (d.get('hkjc_ou_over', 0) or 0),
                'under': (d.get('bet365_ou_under', 0) or 0) or (d.get('hkjc_ou_under', 0) or 0),
                'open': {
                    'line': d.get('bet365_ou_open_line') if d.get('bet365_ou_open_line') else d.get('hkjc_ou_open_line'),
                    'over': (d.get('bet365_ou_open_over', 0) or 0) or (d.get('hkjc_ou_open_over', 0) or 0),
                    'under': (d.get('bet365_ou_open_under', 0) or 0) or (d.get('hkjc_ou_open_under', 0) or 0),
                },
            },
            'william_1x2': {
                'w': d.get('william_1x2_w', 0) or 0,
                'd': d.get('william_1x2_d', 0) or 0,
                'l': d.get('william_1x2_l', 0) or 0,
            },
            'william_ah': {
                'handicap': d.get('william_ah_handicap', None),
                'home_w': d.get('william_ah_home_water', 0) or 0,
                'away_w': d.get('william_ah_away_water', 0) or 0,
                'open': {
                    'handicap': d.get('william_ah_open_handicap', None),
                    'home_w': d.get('william_ah_open_home_water', 0) or 0,
                    'away_w': d.get('william_ah_open_away_water', 0) or 0,
                },
            },
            'william_ou': {
                'over': d.get('william_ou_over', 0) or 0,
                'line': d.get('william_ou_line', None),
                'under': d.get('william_ou_under', 0) or 0,
                'open': {
                    'over': d.get('william_ou_open_over', 0) or 0,
                    'line': d.get('william_ou_open_line', None),
                    'under': d.get('william_ou_open_under', 0) or 0,
                },
            },
        })
    conn.close()

    # A3去重+补AH+补kickoff/william：同 (canonical_home, canonical_away) 只保留一条
    # 保留记录缺少字段时从被丢弃的同 key 记录补
    total_dedup = 0
    total_ah_fixed = 0
    total_alias_hits = 0
    total_kickoff_fixed = 0
    total_william_fixed = 0
    for date in list(by_date.keys()):
        records = by_date[date]
        if len(records) <= 1:
            continue
        groups = {}
        for r in records:
            key = _match_key(r['home'], r['away'])
            groups.setdefault(key, []).append(r)
        seen = {}
        for r in records:
            key = _match_key(r['home'], r['away'])
            if key not in seen:
                seen[key] = r
            else:
                prev = seen[key]
                if r['source'] == 'jingcai' and prev['source'] != 'jingcai':
                    seen[key] = r
                elif r['source'] == prev['source'] and r['id'] > prev['id']:
                    seen[key] = r
                if r['home'] != prev['home'] or r['away'] != prev['away']:
                    total_alias_hits += 1
        # 补AH + 补kickoff/william
        ah_fixed = 0
        for key, kept in seen.items():
            # 补AH
            ah = kept.get('ah', {})
            if not ah or ah.get('handicap') is None or ah.get('handicap') == 0:
                for r in reversed(groups[key]):
                    rah = r.get('ah', {})
                    if rah and rah.get('handicap') is not None and rah.get('handicap') != 0:
                        kept['ah'] = rah
                        ah_fixed += 1
                        break
            # 补kickoff：保留记录kickoff为"待定"或空时，从同key其他记录补
            ko = kept.get('kickoff', '')
            if not ko or ko == '待定':
                for r in reversed(groups[key]):
                    rko = r.get('kickoff', '')
                    if rko and rko != '待定':
                        kept['kickoff'] = rko
                        total_kickoff_fixed += 1
                        break
            # 补william_1x2：保留记录william全0时，从同key其他记录补
            w1 = kept.get('william_1x2', {})
            if not w1 or (w1.get('w', 0) == 0 and w1.get('d', 0) == 0 and w1.get('l', 0) == 0):
                for r in reversed(groups[key]):
                    rw = r.get('william_1x2', {})
                    if rw and (rw.get('w', 0) > 0 or rw.get('d', 0) > 0 or rw.get('l', 0) > 0):
                        kept['william_1x2'] = rw
                        total_william_fixed += 1
                        break
            # 补william_ah
            wah = kept.get('william_ah', {})
            if not wah or (wah.get('handicap') is None or wah.get('handicap') == 0):
                for r in reversed(groups[key]):
                    rwah = r.get('william_ah', {})
                    if rwah and rwah.get('handicap') is not None and rwah.get('handicap') != 0:
                        kept['william_ah'] = rwah
                        total_william_fixed += 1
                        break
            # 补william_ou
            wou = kept.get('william_ou', {})
            if not wou or (wou.get('over', 0) == 0):
                for r in reversed(groups[key]):
                    rwou = r.get('william_ou', {})
                    if rwou and rwou.get('over', 0) > 0:
                        kept['william_ou'] = rwou
                        total_william_fixed += 1
                        break
        if len(seen) < len(records):
            by_date[date] = list(seen.values())
            total_dedup += len(records) - len(seen)
            total_ah_fixed += ah_fixed
    if total_dedup or total_ah_fixed or total_alias_hits or total_kickoff_fixed or total_william_fixed:
        print(f"  [merge] 去重 {total_dedup} 条, 补AH {total_ah_fixed} 条, 补kickoff {total_kickoff_fixed} 条, 补william {total_william_fixed} 条, 别名匹配 {total_alias_hits} 条")

    return by_date


def build_daily_stats(by_date):
    stats = {}
    for date, records in by_date.items():
        n = sum(1 for r in records if r.get('result'))
        ev = sum(1 for r in records if r.get('ev_hit'))
        pb = sum(1 for r in records if r.get('prob_hit'))
        # 信心分层统计
        tier_stats = {}
        for tier in ['high', 'medium', 'low', 'very_low']:
            tier_recs = [r for r in records if r.get('confidence_tier') == tier and r.get('result')]
            tn = len(tier_recs)
            tev = sum(1 for r in tier_recs if r.get('ev_hit'))
            tpb = sum(1 for r in tier_recs if r.get('prob_hit'))
            tier_stats[tier] = {
                'total': tn,
                'ev_hits': tev, 'prob_hits': tpb,
                'ev_rate': round(tev/tn*100, 1) if tn else 0,
                'prob_rate': round(tpb/tn*100, 1) if tn else 0,
            }
        stats[date] = {
            'total': len(records), 'with_result': n,
            'ev_hits': ev, 'prob_hits': pb,
            'any_hits': sum(1 for r in records if r.get('ev_hit') or r.get('prob_hit')),
            'ev_rate': round(ev/n*100, 1) if n else 0,
            'prob_rate': round(pb/n*100, 1) if n else 0,
            'any_rate': round(sum(1 for r in records if r.get('ev_hit') or r.get('prob_hit'))/n*100, 1) if n else 0,
            'tiers': tier_stats,
        }
    return stats


def build_summary(daily_stats):
    tn = tev = tpb = tany = 0
    # 信心分层汇总
    tier_totals = {t: {'total': 0, 'ev_hits': 0, 'prob_hits': 0} for t in ['high', 'medium', 'low', 'very_low']}
    for s in daily_stats.values():
        tn += s['with_result']; tev += s['ev_hits']; tpb += s['prob_hits']; tany += s['any_hits']
        if 'tiers' in s:
            for t in tier_totals:
                tier_totals[t]['total'] += s.get('tiers', {}).get(t, {}).get('total', 0)
                tier_totals[t]['ev_hits'] += s.get('tiers', {}).get(t, {}).get('ev_hits', 0)
                tier_totals[t]['prob_hits'] += s.get('tiers', {}).get(t, {}).get('prob_hits', 0)
    tier_summary = {}
    for t, v in tier_totals.items():
        n = v['total']
        tier_summary[t] = {
            'total': n,
            'ev_hits': v['ev_hits'], 'prob_hits': v['prob_hits'],
            'ev_rate': round(v['ev_hits']/n*100, 1) if n else 0,
            'prob_rate': round(v['prob_hits']/n*100, 1) if n else 0,
        }
    return {
        'total_matches': tn, 'ev_hits': tev, 'prob_hits': tpb, 'any_hits': tany,
        'ev_rate': round(tev/tn*100, 1) if tn else 0,
        'prob_rate': round(tpb/tn*100, 1) if tn else 0,
        'any_rate': round(tany/tn*100, 1) if tn else 0,
        'days': len(daily_stats),
        'tiers': tier_summary,
        'last_updated': datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M'),
    }


def build_league_score_freq(db_path: str) -> dict:
    """从DB统计每个联赛的历史比分频率（用于泊松排序加权）"""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""SELECT league, actual_outcome FROM poisson_predictions
        WHERE home_lambda > 0 AND away_lambda > 0
        AND actual_outcome IS NOT NULL AND actual_outcome != ''""")
    league_scores = defaultdict(lambda: defaultdict(int))
    for league, outcome in cur.fetchall():
        m = re.search(r'(\d+-\d+)', outcome or '')
        if not m:
            continue
        league_scores[league][m.group(1)] += 1
    conn.close()
    return {lg: dict(scores) for lg, scores in league_scores.items()}


def filter_by_league_by_date(by_date, verbose=True, max_days=10):
    """限制看板天数，避免数据过大。去掉了联赛白名单过滤。"""
    dates = sorted(by_date.keys(), reverse=True)
    total_before = sum(len(v) for v in by_date.values())
    kept = 0
    if len(dates) > max_days:
        for date in dates[max_days:]:
            kept += len(by_date[date])
            del by_date[date]
        if verbose:
            print(f'📅 天数截断: {len(dates)}天 → {max_days}天 (去掉第{max_days+1}天及之后 {kept} 场)')
    # 不再按联赛过滤
    total_after = sum(len(v) for v in by_date.values())
    if verbose and total_before != total_after:
        print(f'   总场次: {total_before} → {total_after} 场')


def generate_results_json(by_date, daily_stats, summary, output_dir=None):
    if not output_dir:
        output_dir = DATA_DIR
    os.makedirs(output_dir, exist_ok=True)
    dates = sorted(by_date.keys(), reverse=True)
    output = {
        'meta': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_dates': len(dates),
            'total_matches': sum(len(v) for v in by_date.values()),
            'date_range': {'from': dates[-1] if dates else '', 'to': dates[0] if dates else ''},
        },
        'summary': summary,
        'daily_stats': daily_stats,
        'dates': dates,
        'matches': by_date,
    }
    path = os.path.join(output_dir, 'results.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'✅ results.json → {path} ({os.path.getsize(path)//1024}KB)')
    # 前端加载 .gz 版本，同步压缩
    gz_path = path + '.gz'
    import gzip
    with open(path, 'rb') as src, gzip.open(gz_path, 'wb', compresslevel=6) as dst:
        dst.writelines(src)
    print(f'✅ results.json.gz → {gz_path} ({os.path.getsize(gz_path)//1024}KB)')
    return path


def generate_index_html(by_date, daily_stats, summary, league_score_freq=None, output_dir=None):
    """生成 index.html（引用外部 style.css + script.js）"""
    if not output_dir:
        output_dir = DOCS_DIR
    os.makedirs(output_dir, exist_ok=True)

    dates = sorted(by_date.keys(), reverse=True)
    today = datetime.now().strftime('%Y-%m-%d')
    default_date = today if today in by_date else (dates[0] if dates else '')
    league_freq_json = json.dumps(league_score_freq or {}, ensure_ascii=False)

    # 计算信心分层统计卡片
    tiers = summary.get('tiers', {})
    rec_total = tiers.get('high', {}).get('total', 0) + tiers.get('medium', {}).get('total', 0)
    rec_ev = tiers.get('high', {}).get('ev_hits', 0) + tiers.get('medium', {}).get('ev_hits', 0)
    rec_prob = tiers.get('high', {}).get('prob_hits', 0) + tiers.get('medium', {}).get('prob_hits', 0)
    rec_ev_rate = round(rec_ev/rec_total*100, 1) if rec_total else 0
    rec_prob_rate = round(rec_prob/rec_total*100, 1) if rec_total else 0
    h_tier = tiers.get('high', {})
    m_tier = tiers.get('medium', {})

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>泊松预测看板</title>
<link rel="stylesheet" href="style.css">
<script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
</head>
<body>
<div class="header">
<h1>⚽ 泊松预测看板</h1>
<div class="sub">更新于 {summary['last_updated']} | 共 {summary['total_matches']} 场已开奖 | {summary['days']} 天数据</div>
</div>

<div class="summary-cards">
<div class="card"><div class="label">EV方向命中率</div><div class="value" style="color:{'#4caf50' if summary['ev_rate']>=55 else '#f44336' if summary['ev_rate']<40 else '#ff9800'}">{summary['ev_rate']}%</div><div class="label">{summary['ev_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">概率最高命中率</div><div class="value" style="color:{'#4caf50' if summary['prob_rate']>=60 else '#f44336' if summary['prob_rate']<45 else '#ff9800'}">{summary['prob_rate']}%</div><div class="label">{summary['prob_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">任一命中</div><div class="value blue">{summary['any_rate']}%</div><div class="label">{summary['any_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">总天数</div><div class="value blue">{summary['days']}</div></div>
</div>
<div class="summary-cards" style="margin-top:8px">
<div class="card" style="border-color:#4caf50"><div class="label">⭐ 推荐命中率(EV)</div><div class="value" style="color:{'#4caf50' if rec_ev_rate>=60 else '#ff9800'}">{rec_ev_rate}%</div><div class="label">{rec_ev}/{rec_total} (高+中信心)</div></div>
<div class="card" style="border-color:#4caf50"><div class="label">⭐ 推荐命中率(概率)</div><div class="value" style="color:{'#4caf50' if rec_prob_rate>=60 else '#ff9800'}">{rec_prob_rate}%</div><div class="label">{rec_prob}/{rec_total} (高+中信心)</div></div>
<div class="card"><div class="label">🔴 高信心命中率</div><div class="value" style="color:{'#4caf50' if h_tier.get('ev_rate',0)>=60 else '#ff9800'}">{h_tier.get('ev_rate',0)}%</div><div class="label">{h_tier.get('ev_hits',0)}/{h_tier.get('total',0)}</div></div>
<div class="card"><div class="label">🟡 中信心命中率</div><div class="value" style="color:{'#4caf50' if m_tier.get('ev_rate',0)>=55 else '#ff9800'}">{m_tier.get('ev_rate',0)}%</div><div class="label">{m_tier.get('ev_hits',0)}/{m_tier.get('total',0)}</div></div>
</div>

<div class="tabs">
<div class="tab active" data-tab="matches">📊 比赛数据</div>
<div class="tab" data-tab="daily">📈 每日统计</div>
<div class="tab" data-tab="leagues">🏆 联赛统计</div>
</div>

<div id="tab-matches" class="tab-content active">
<div class="controls">
<label>日期：</label>
<select id="dateSelect"></select>
<div class="source-tabs">
<button class="source-tab active" data-source="all">全部</button>
<button class="source-tab" data-source="jingcai">香港马会</button>
<button class="source-tab" data-source="beidan">北京单场</button>
</div>
<label><input type="checkbox" id="showResulted" checked> 已开奖</label>
<label><input type="checkbox" id="showPending" checked> 待开奖</label>
<button id="btnExcel" onclick="downloadExcel()" style="margin-left:auto;padding:4px 12px;background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px">📥 下载Excel</button>
</div>
<div style="overflow-x:auto">
<table id="matchTable">
<thead><tr>
<th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>亚盘</th><th>客队</th>
<th>推荐</th><th>概率推荐</th><th>赛果</th><th>比分</th>
<th>泊松W/D/L</th><th>综合W/D/L</th>
<th>EV</th><th>凯利</th>

</tr></thead>
<tbody id="matchBody"></tbody>
</table>
</div>
</div>

<div id="tab-daily" class="tab-content">
<div class="section-title">每日统计</div>
<div style="overflow-x:auto">
<table id="dailyTable">
<thead><tr><th>日期</th><th>总场次</th><th>已开奖</th><th>EV命中率</th><th>概率命中率</th><th>任一命中率</th></tr></thead>
<tbody id="dailyBody"></tbody>
</table>
</div>
</div>

<div id="tab-leagues" class="tab-content">
<div class="section-title">联赛统计</div>
<div style="overflow-x:auto">
<table id="leagueTable">
<thead><tr><th>联赛</th><th>总场次</th><th>EV命中</th><th>概率命中</th><th>EV率</th><th>概率率</th></tr></thead>
<tbody id="leagueBody"></tbody>
</table>
</div>
</div>


<!-- 泊松比分分布模态框 -->
<div id="poissonModal" class="modal-overlay" style="display:none">
<div class="modal-box">
<div class="modal-header">
  <span id="modalTitle">比分概率分布</span>
  <span class="modal-close" id="modalClose">&times;</span>
</div>
<div class="modal-controls">
  <label>加权 α: <input type="range" id="alphaSlider" min="0" max="100" value="50" style="width:120px;vertical-align:middle">
  <span id="alphaValue">0.50</span></label>
  <label><input type="checkbox" id="showHeatmap" checked> 热力图</label>
</div>
<div id="modalBarChart" class="modal-bars"></div>
<div id="modalHeatmap" class="modal-heatmap-wrap"></div>
</div>
</div>

<!-- 亚盘赔率详情模态框 -->
<div id="ahModal" class="modal-overlay" style="display:none">
<div class="modal-box ah-modal-box">
<div class="modal-header">
  <span id="ahModalTitle">赔率详情</span>
  <span class="modal-close" id="ahModalClose">&times;</span>
</div>
<div id="ahModalContent"></div>
</div>
</div>
<script>const LEAGUE_SCORE_FREQ = {league_freq_json};</script>
<script src="script.js"></script>
</body>
</html>'''

    path = os.path.join(output_dir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ index.html → {path}')
    return path


def main():
    parser = argparse.ArgumentParser(description='processed → results.json + index.html')
    parser.add_argument('--json-only', action='store_true')
    parser.add_argument('--html-only', action='store_true')
    parser.add_argument('--db', type=str, default=None)
    parser.add_argument('--output', type=str, default=None)
    args = parser.parse_args()

    db_path = args.db or DB_PATH

    # 优先读processed，再用DB补充缺失日期和新增字段
    by_date = load_from_processed()
    if os.path.exists(db_path):
        db_data = load_from_db(db_path)
        if db_data:
            missing = {k: v for k, v in db_data.items() if k not in by_date}
            if missing:
                by_date.update(missing)
                print(f'📌 DB补充{len(missing)}天缺失数据: {sorted(missing.keys())}')
            # DB中同日期记录数更多时也更新（processed可能缺新插入的场次）
            # 同时用DB的新字段（pin_ah/ou等）补充processed旧记录
            _NEW_KEYS = {'pin_ah', 'pin_ou', 'ou', 'liji_ou', 'ms_ou', 'hkjc_ah', 'hkjc_ou', 'william_1x2', 'william_ah', 'william_ou', 'bet365'}
            # ah/liji/ms字段：processed为空或handicap=0时，用DB非零值覆盖
            _AH_KEYS = {'ah', 'liji', 'ms'}
            # hkjc 1X2初盘同步
            _HKJC_KEYS = {'hkjc'}
            n_merged = 0
            n_ah_merged = 0
            n_hkjc_merged = 0
            for d_key, db_records in db_data.items():
                if d_key in by_date:
                    if len(db_records) > len(by_date[d_key]):
                        by_date[d_key] = db_records
                    else:
                        # 用归一化(home, away)匹配（别名归一化后跨源同场）
                        db_by_match = {}
                        for r in db_records:
                            mk = _match_key(r.get('home',''), r.get('away',''))
                            db_by_match.setdefault(mk, r)
                        db_by_id = {r['id']: r for r in db_records}
                        for proc_rec in by_date[d_key]:
                            # 优先id匹配，回退到归一化(home,away)匹配
                            db_rec = db_by_id.get(proc_rec.get('id'))
                            if not db_rec:
                                match_k = _match_key(proc_rec.get('home',''), proc_rec.get('away',''))
                                db_rec = db_by_match.get(match_k)
                            if db_rec:
                                for k in _NEW_KEYS:
                                    if (k not in proc_rec or not isinstance(proc_rec.get(k), dict)) and k in db_rec and isinstance(db_rec.get(k), dict):
                                        proc_rec[k] = db_rec[k]
                                        n_merged += 1
                                # 用DB的赛果回填processed空值（review后actual_outcome已更新）
                                _RESULT_KEYS = ('score', 'result', 'ev_hit', 'prob_hit', 'ev_direction', 'prob_direction')
                                for k in _RESULT_KEYS:
                                    if not proc_rec.get(k) and db_rec.get(k):
                                        proc_rec[k] = db_rec[k]
                                        n_merged += 1
                                # 补充信心分层字段（processed旧JSON没有confidence_tier/calibrated_prob）
                                _CONFIDENCE_KEYS = ('confidence_tier', 'calibrated_prob', 'best_direction_cn')
                                for k in _CONFIDENCE_KEYS:
                                    if k not in proc_rec or not proc_rec.get(k):
                                        db_val = db_rec.get(k)
                                        if db_val:
                                            proc_rec[k] = db_val
                                            n_merged += 1
                                # 补充信号/市场数据字段（processed旧JSON没有cold_signals/avg_margin等）
                                _SIGNAL_KEYS = ('cold_signals', 'risk_warning', 'actual_outcome',
                                                'avg_margin', 'ev_value', 'deviation_analysis',
                                                'ev_signal')
                                for k in _SIGNAL_KEYS:
                                    if k not in proc_rec or not proc_rec.get(k):
                                        db_val = db_rec.get(k)
                                        if db_val:
                                            proc_rec[k] = db_val
                                            n_merged += 1
                                # 补充概率/赔率字段（processed旧JSON中final_prob/fusion_prob/ev/kelly为全0，用DB覆盖）
                                _PROB_KEYS = ('final_prob', 'fusion_prob', 'ev', 'kelly', 'odds_ev')
                                for k in _PROB_KEYS:
                                    p_val = proc_rec.get(k)
                                    d_val = db_rec.get(k)
                                    if isinstance(d_val, dict) and isinstance(p_val, dict):
                                        # 只要DB的字典里至少有一个非零值就覆盖
                                        if any(v for v in d_val.values() if isinstance(v, (int, float)) and v != 0):
                                            proc_rec[k] = d_val
                                            n_merged += 1
                                # 补充prediction_prob（processed旧JSON中为0，用DB校正后的值覆盖）
                                if not proc_rec.get('prediction_prob') and db_rec.get('prediction_prob'):
                                    proc_rec['prediction_prob'] = db_rec['prediction_prob']
                                    n_merged += 1
                                # final_prob被DB覆盖后，同步更新prob_direction/prob_hit
                                fp = proc_rec.get('final_prob')
                                if isinstance(fp, dict) and any(fp.values()):
                                    fw, fd_, fl_ = fp.get('w',0), fp.get('d',0), fp.get('l',0)
                                    new_dir = '主胜' if fw >= fd_ and fw >= fl_ else ('客胜' if fl_ >= fw and fl_ >= fd_ else '平局')
                                    if new_dir != proc_rec.get('prob_direction'):
                                        proc_rec['prob_direction'] = new_dir
                                        proc_rec['prob_hit'] = (new_dir == proc_rec.get('result'))
                                        n_merged += 1
                                # 补充ah/liji/ms：processed为空/非dict或handicap=0时用DB覆盖
                                for k in _AH_KEYS:
                                    p_val = proc_rec.get(k)
                                    d_val = db_rec.get(k)
                                    # 处理processed里字段为空字符串或非dict的情况
                                    if not isinstance(p_val, dict):
                                        p_val = {}
                                    if not isinstance(d_val, dict):
                                        continue
                                    p_hc = p_val.get('handicap') if k == 'ah' else (p_val.get('close', {}) or {}).get('handicap')
                                    d_hc = d_val.get('handicap') if k == 'ah' else (d_val.get('close', {}) or {}).get('handicap')
                                    if (p_hc is None or p_hc == 0) and d_hc is not None and d_hc != 0:
                                        proc_rec[k] = d_val
                                        n_ah_merged += 1
                                # hkjc 1X2+AH+OU同步：DB有open/ah/ou数据时覆盖
                                for k in _HKJC_KEYS:
                                    p_val = proc_rec.get(k)
                                    d_val = db_rec.get(k)
                                    if not isinstance(p_val, dict):
                                        p_val = {}
                                    if not isinstance(d_val, dict):
                                        continue
                                    # DB有open初盘或AH/OU数据时覆盖
                                    d_has_open = isinstance(d_val.get('open'), dict) and (d_val['open'].get('w') or 0) > 0
                                    p_has_open = isinstance(p_val.get('open'), dict) and (p_val['open'].get('w') or 0) > 0
                                    if d_has_open and not p_has_open:
                                        proc_rec[k] = d_val
                                        n_hkjc_merged += 1
            if n_merged:
                print(f'📌 DB补充{n_merged}条记录的新字段')
            if n_ah_merged:
                print(f'📌 DB补充{n_ah_merged}条记录的ah/liji/ms字段')
            if n_hkjc_merged:
                print(f'📌 DB补充{n_hkjc_merged}条记录的hkjc字段')
    if not by_date:
        print('[ERROR] 无数据'); sys.exit(1)

    # 最终去重：同一天内同(canonical_home,canonical_away)可能重复
    total_final_dedup = 0
    total_final_alias = 0
    for d_key in list(by_date.keys()):
        records = by_date[d_key]
        seen = {}
        for r in records:
            key = _match_key(r.get('home',''), r.get('away',''))
            if key not in seen:
                seen[key] = r
            else:
                prev = seen[key]
                # 竞彩优先，id更大优先
                if r.get('source') == 'jingcai' and prev.get('source') != 'jingcai':
                    # 丢弃prev前补数据
                    _merge_missing(prev, r)
                    seen[key] = r
                elif r.get('id', 0) > prev.get('id', 0):
                    _merge_missing(r, prev)
                    seen[key] = r
                else:
                    _merge_missing(prev, r)
                if r.get('home','') != prev.get('home','') or r.get('away','') != prev.get('away',''):
                    total_final_alias += 1
        if len(seen) < len(records):
            by_date[d_key] = list(seen.values())
            total_final_dedup += len(records) - len(seen)
    if total_final_dedup:
        print(f'📌 最终去重 {total_final_dedup} 条（跨源重复）')
    if total_final_alias:
        print(f'📌 别名归一化匹配 {total_final_alias} 条（跨源队名不一致）')

    # 跨日期去重：同一场比赛可能因 source 不同出现在两个日期（如 future_500 + beidan）
    # 用 (canonical_home, canonical_away) 做键（不带日期），优先保留日期与开赛时间匹配的版本
    def _cross_date_score(rec, date):
        """评分：越高越应该保留。开赛时间匹配日期得3分，有开赛时间得1分。"""
        ko = rec.get('kickoff', '') or ''
        score = 0
        if ko and ko != '待定':
            score += 1
            if ko[:10] == date:
                score += 2
        return score

    cross_date_dedup = 0
    cross_date_kickoff_fixed = 0
    all_matches = {}  # (canonical_home, canonical_away) → (date, record)
    for d_key in sorted(by_date.keys()):
        kept = []
        for r in by_date[d_key]:
            mk = _match_key(r.get('home', ''), r.get('away', ''))
            if mk in all_matches:
                prev_date, prev_rec = all_matches[mk]
                prev_score = _cross_date_score(prev_rec, prev_date)
                curr_score = _cross_date_score(r, d_key)
                if curr_score > prev_score:
                    # 当前版本更"正确" → 替换之前的
                    _merge_missing(r, prev_rec)
                    by_date[prev_date] = [x for x in by_date[prev_date] if x is not prev_rec]
                    all_matches[mk] = (d_key, r)
                    if curr_score >= 3 and prev_score < 3:
                        cross_date_kickoff_fixed += 1
                    cross_date_dedup += 1
                    kept.append(r)
                else:
                    # 之前的更"正确" → 补数据后丢弃当前
                    _merge_missing(prev_rec, r)
                    cross_date_dedup += 1
            else:
                all_matches[mk] = (d_key, r)
                kept.append(r)
        by_date[d_key] = kept
    if cross_date_dedup:
        print(f'📌 跨日期去重 {cross_date_dedup} 条（kickoff补全 {cross_date_kickoff_fixed} 条）')



    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)

    # 联赛比分频率（用于前端加权排序）
    league_score_freq = build_league_score_freq(db_path)
    n_leagues = len(league_score_freq)
    n_scores = sum(len(v) for v in league_score_freq.values())
    print(f'📊 {len(by_date)}天, {summary["total_matches"]}场已开奖, EV={summary["ev_rate"]}%, 概率={summary["prob_rate"]}%')
    print(f'   联赛比分频率: {n_leagues}个联赛, {n_scores}个比分记录')

    # 按联赛白名单过滤看板展示
    filter_by_league_by_date(by_date)
    # 过滤后重建统计数据
    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)

    out_base = args.output or REPO_DIR
    # results.json 必须输出到 docs/data/ 下，GitHub Pages 才能访问
    if not args.html_only:
        generate_results_json(by_date, daily_stats, summary, os.path.join(out_base, 'docs', 'data'))
    if not args.json_only:
        generate_index_html(by_date, daily_stats, summary, league_score_freq, os.path.join(out_base, 'docs'))

    print('✅ 构建完成')


if __name__ == '__main__':
    main()

