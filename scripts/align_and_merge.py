#!/usr/bin/env python3
"""align_and_merge.py — 对齐合并 raw 数据 → processed

读取：
  data/raw/bsd/{date}.json        — 赛果
  data/raw/oddsmagnet/{date}.json — 赔率
  football.db (可选)               — 预测数据

输出：
  data/processed/{date}.json — 合并后的结构化数据

对齐策略：
  1. 以 DB 预测为主轴（有预测才有看板行）
  2. BSD 赛果按队名模糊匹配回填
  3. OddsMagnet 赔率按队名匹配补充 Pinnacle/HKJC
"""

import os, sys, json, re, sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_BSD = os.path.join(REPO_DIR, "data", "raw", "bsd")
RAW_OM = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")
PROCESSED_DIR = os.path.join(REPO_DIR, "data", "processed")

# DB路径：环境变量 > 相对路径
DB_PATH = os.environ.get('FOOTBALL_DB_PATH',
    os.path.join(REPO_DIR, '..', 'data', 'football.db'))

sys.path.insert(0, SCRIPT_DIR)
from utils import team_match, normalize_team, calc_implied_prob, calc_ev, calc_kelly, parse_score


def load_raw_bsd(date_str: str) -> Dict:
    """加载BSD赛果"""
    path = os.path.join(RAW_BSD, f"{date_str.replace('-','')}.json")
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_raw_oddsmagnet(date_str: str) -> Dict:
    """加载OddsMagnet赔率"""
    path = os.path.join(RAW_OM, f"{date_str.replace('-','')}.json")
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_db_predictions(db_path: str, date_str: str) -> List[Dict]:
    """从DB加载某日预测"""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    date_start = f"{date_str} 00:00"
    date_end = f"{date_str} 23:59"
    cur.execute("""
        SELECT id, date, league, home_team, away_team, kickoff_time,
            prediction, prediction_prob,
            odds_win, odds_draw, odds_loss,
            poisson_win, poisson_draw, poisson_loss,
            final_win, final_draw, final_loss,
            fusion_win, fusion_draw, fusion_loss,
            actual_outcome, risk_level, confidence_index, reference_score,
            best_direction_cn, source,
            ev_win, ev_draw, ev_loss,
            kelly_win, kelly_draw, kelly_loss,
            pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
            hkjc_close_w, hkjc_close_d, hkjc_close_l,
            cold_risk, odds_source,
            home_lambda, away_lambda,
            home_ranking, away_ranking
        FROM poisson_predictions
        WHERE date = ? OR (kickoff_time >= ? AND kickoff_time <= ?)
        ORDER BY kickoff_time, id
    """, (date_str, date_start, date_end))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # A3: 去重 — 同 (home, away) 只保留一条，优先 jingcai > om_only，同 source 取 id 更大
    if len(rows) > 1:
        seen = {}
        for r in rows:
            key = (r['home_team'], r['away_team'])
            if key not in seen:
                seen[key] = r
            else:
                prev = seen[key]
                if r['source'] == 'jingcai' and prev['source'] != 'jingcai':
                    seen[key] = r
                elif r['source'] == prev['source'] and r['id'] > prev['id']:
                    seen[key] = r
        if len(seen) != len(rows):
            print(f"  [A3] {date_str}: 去重 {len(rows)} -> {len(seen)} 条")
            rows = list(seen.values())

    return rows


def match_bsd_result(team_home: str, team_away: str, bsd_data: Dict) -> Optional[Dict]:
    """在BSD赛果中查找匹配"""
    if not bsd_data:
        return None
    # 先查竞彩
    for r in bsd_data.get('jingcai', []):
        if team_match(team_home, r['home']) and team_match(team_away, r['away']):
            return {'score': r['score'], 'home_score': r.get('home_score'),
                    'away_score': r.get('away_score'), 'source': 'jingcai'}
    # 再查完场
    for r in bsd_data.get('wanchang', []):
        if team_match(team_home, r['home']) and team_match(team_away, r['away']):
            return {'score': r['score'], 'home_score': r.get('home_score'),
                    'away_score': r.get('away_score'), 'source': 'wanchang'}
    return None


def match_om_odds(team_home: str, team_away: str, om_data: Dict) -> Optional[Dict]:
    """在OddsMagnet赔率中查找匹配（兼容dict和list两种格式）"""
    if not om_data:
        return None
    matches = om_data.get('matches', {})
    # 新格式: matches是dict {key: {info, odds}}
    if isinstance(matches, dict):
        for key, m in matches.items():
            info = m.get('info', {})
            mh = info.get('home', '')
            ma = info.get('away', '')
            if team_match(team_home, mh) and team_match(team_away, ma):
                # 展平为兼容格式
                result = {'home': mh, 'away': ma, 'match_id': info.get('match_id', '')}
                for src, o in m.get('odds', {}).items():
                    result[f'{src}_w'] = o.get('odds_w', 0)
                    result[f'{src}_d'] = o.get('odds_d', 0)
                    result[f'{src}_l'] = o.get('odds_l', 0)
                    result[f'{src}_margin'] = o.get('margin', 0)
                bsd = m.get('bsd', {})
                if bsd:
                    result['bsd'] = bsd
                return result
    # 旧格式: matches是list [{home, away, odds_w, ...}]
    elif isinstance(matches, list):
        for m in matches:
            if team_match(team_home, m.get('home', '')) and team_match(team_away, m.get('away', '')):
                return m
    return None


def merge_prediction(rec: Dict, bsd_result: Optional[Dict], om_match: Optional[Dict]) -> Dict:
    """合并单条预测记录 + BSD赛果 + OM赔率"""
    # 解析已有赛果
    existing_outcome = rec.get('actual_outcome', '') or ''
    result_label, score, hs, as_ = parse_score(existing_outcome)

    # BSD赛果回填（仅当DB无赛果时）
    if not result_label and bsd_result:
        score = bsd_result['score']
        hs = bsd_result.get('home_score')
        as_ = bsd_result.get('away_score')
        if hs is not None and as_ is not None:
            result_label = '主胜' if hs > as_ else ('客胜' if hs < as_ else '平局')

    # 方向命中判定
    ev_dir = rec.get('best_direction_cn') or rec.get('prediction') or ''
    ev_hit = (ev_dir == result_label) if result_label else False

    fw = rec.get('final_win', 0) or 0
    fd = rec.get('final_draw', 0) or 0
    fl = rec.get('final_loss', 0) or 0
    prob_dir = '主胜' if fw >= fd and fw >= fl else ('客胜' if fl >= fw and fl >= fd else '平局')
    prob_hit = (prob_dir == result_label) if result_label else False

    rw = rec.get('fusion_win', 0) or 0
    rd = rec.get('fusion_draw', 0) or 0
    rl = rec.get('fusion_loss', 0) or 0
    fusion_dir = '主胜' if rw >= rd and rw >= rl else ('客胜' if rl >= rw and rl >= rd else '平局')

    # 信心星级
    ci = rec.get('confidence_index') or 0
    stars = min(5, max(1, round(ci * 5))) if isinstance(ci, (int, float)) and ci > 0 else 0

    # Pinnacle / HKJC 优先从OddsMagnet补充
    pin_w = rec.get('pinnacle_close_w', 0) or 0
    pin_d = rec.get('pinnacle_close_d', 0) or 0
    pin_l = rec.get('pinnacle_close_l', 0) or 0
    hkjc_w = rec.get('hkjc_close_w', 0) or 0
    hkjc_d = rec.get('hkjc_close_d', 0) or 0
    hkjc_l = rec.get('hkjc_close_l', 0) or 0

    if om_match:
        pc = om_match.get('pinnacle_close', {})
        if pc.get('w', 0) > 0:
            pin_w, pin_d, pin_l = pc['w'], pc['d'], pc['l']
        elif om_match.get('pinnacle_w', 0) > 0:
            pin_w = om_match['pinnacle_w']
            pin_d = om_match['pinnacle_d']
            pin_l = om_match['pinnacle_l']
        hc = om_match.get('hkjc_close', {})
        if hc.get('w', 0) > 0:
            hkjc_w, hkjc_d, hkjc_l = hc['w'], hc['d'], hc['l']
        elif om_match.get('hkjc_w', 0) > 0:
            hkjc_w = om_match['hkjc_w']
            hkjc_d = om_match['hkjc_d']
            hkjc_l = om_match['hkjc_l']

    # EV
    ew = rec.get('ev_win', 0) or 0
    ed = rec.get('ev_draw', 0) or 0
    el = rec.get('ev_loss', 0) or 0

    return {
        'id': rec['id'],
        'date': rec.get('date', ''),
        'league': rec.get('league', ''),
        'home': rec['home_team'],
        'away': rec['away_team'],
        'kickoff': rec.get('kickoff_time', ''),
        'prediction': rec.get('prediction', ''),
        'prediction_prob': round(rec.get('prediction_prob', 0) or 0, 3),
        'odds': {
            'w': rec.get('odds_win', 0) or 0,
            'd': rec.get('odds_draw', 0) or 0,
            'l': rec.get('odds_loss', 0) or 0,
        },
        'poisson': {
            'w': round(rec.get('poisson_win', 0) or 0, 3),
            'd': round(rec.get('poisson_draw', 0) or 0, 3),
            'l': round(rec.get('poisson_loss', 0) or 0, 3),
        },
        'final_prob': {'w': round(fw, 3), 'd': round(fd, 3), 'l': round(fl, 3)},
        'fusion_prob': {'w': round(rw, 3), 'd': round(rd, 3), 'l': round(rl, 3)},
        'result': result_label,
        'score': score,
        'ev_direction': ev_dir,
        'ev_hit': ev_hit,
        'prob_direction': prob_dir,
        'prob_hit': prob_hit,
        'fusion_direction': fusion_dir,
        'ev': {'w': round(ew, 4), 'd': round(ed, 4), 'l': round(el, 4)},
        'kelly': {
            'w': round(rec.get('kelly_win', 0) or 0, 4),
            'd': round(rec.get('kelly_draw', 0) or 0, 4),
            'l': round(rec.get('kelly_loss', 0) or 0, 4),
        },
        'pinnacle': {'w': pin_w, 'd': pin_d, 'l': pin_l},
        'hkjc': {'w': hkjc_w, 'd': hkjc_d, 'l': hkjc_l},
        'risk_level': rec.get('risk_level', '') or '',
        'stars': stars,
        'confidence_index': round(ci, 2),
        'reference_score': rec.get('reference_score', '') or '',
        'cold_risk': rec.get('cold_risk', '') or '',
        'source': rec.get('source', 'jingcai'),
        'odds_source': rec.get('odds_source', 'had'),
        'home_lambda': round(rec.get('home_lambda', 0) or 0, 3),
        'away_lambda': round(rec.get('away_lambda', 0) or 0, 3),
        'home_ranking': rec.get('home_ranking', 0) or 0,
        'away_ranking': rec.get('away_ranking', 0) or 0,
    }


def align_and_merge(date_str: str, db_path: str = None) -> Dict:
    """对齐合并：DB + BSD + OM → processed"""
    if not db_path:
        db_path = DB_PATH

    print(f"🔗 对齐合并: {date_str}")

    # 加载三源
    bsd = load_raw_bsd(date_str)
    om = load_raw_oddsmagnet(date_str)
    predictions = load_db_predictions(db_path, date_str)

    # 也加载次日BSD（补欧洲晚场）
    next_date = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    bsd_next = load_raw_bsd(next_date)

    merged = []
    for rec in predictions:
        # 匹配赛果
        bsd_result = match_bsd_result(rec['home_team'], rec['away_team'], bsd)
        if not bsd_result and bsd_next:
            # 次日缓存补晚场
            bsd_result = match_bsd_result(rec['home_team'], rec['away_team'], bsd_next)

        # 匹配赔率
        om_match = match_om_odds(rec['home_team'], rec['away_team'], om)

        merged.append(merge_prediction(rec, bsd_result, om_match))

    # 统计
    with_result = sum(1 for r in merged if r['result'])
    ev_hits = sum(1 for r in merged if r['ev_hit'])
    prob_hits = sum(1 for r in merged if r['prob_hit'])

    output = {
        'date': date_str,
        'merge_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'records': merged,
        'stats': {
            'total': len(merged),
            'with_result': with_result,
            'ev_hits': ev_hits,
            'prob_hits': prob_hits,
            'ev_rate': round(ev_hits / with_result * 100, 1) if with_result else 0,
            'prob_rate': round(prob_hits / with_result * 100, 1) if with_result else 0,
        }
    }

    os.makedirs(PROCESSED_DIR, exist_ok=True)
    out_path = os.path.join(PROCESSED_DIR, f"{date_str.replace('-','')}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ {date_str}: {len(merged)}场, {with_result}有赛果, EV={output['stats']['ev_rate']}% → {out_path}")
    return output


def align_all(db_path: str = None, max_days: int = 999) -> Dict:
    """合并所有日期"""
    if not db_path:
        db_path = DB_PATH
    if not os.path.exists(db_path):
        print(f"[ERROR] DB not found: {db_path}")
        return {}

    # 获取所有有数据的日期
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d') if max_days < 999 else '2000-01-01'
    cur.execute("SELECT DISTINCT date FROM poisson_predictions WHERE date >= ? ORDER BY date", (cutoff,))
    dates = [r[0] for r in cur.fetchall()]
    conn.close()

    all_by_date = {}
    for d in dates:
        result = align_and_merge(d, db_path)
        all_by_date[d] = result

    return all_by_date


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', type=str, default=None, help='单日 YYYY-MM-DD')
    p.add_argument('--all', action='store_true', help='合并所有日期')
    p.add_argument('--db', type=str, default=None, help='数据库路径')
    args = p.parse_args()

    if args.all:
        align_all(args.db)
    elif args.date:
        align_and_merge(args.date, args.db)
    else:
        # 默认昨天
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        align_and_merge(yesterday, args.db)
