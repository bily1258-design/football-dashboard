#!/usr/bin/env python3
"""predict_from_odds.py — 从 OM 赔率反推泊松预测，INSERT 到 DB

职责：
- 读 data/raw/oddsmagnet/{date}.json
- 对每场比赛：
  - 用 OM 百家平均 implied(去抽水) 作为市场预期
  - 用 implied 反推实力差距 → 估算 λ_home/λ_away
  - 用泊松分布算 P(主胜/平/客胜)
  - final = 0.7×poisson + 0.3×implied
  - prediction = max(final) 方向
- INSERT 到 poisson_predictions 表（按 date+home+away 去重）

依赖：raw/oddsmagnet/{date}.json + raw/bsd/{date}.json（可选，用于补 beidan）
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
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_OM = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")
RAW_BSD = os.path.join(REPO_DIR, "data", "raw", "bsd")
DB_PATH = os.path.join(REPO_DIR, "data", "football.db")

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
    """从 implied 反推 λ_home / λ_away

    思路：
    - implied_share_h = imp_w / (imp_w + imp_l)  # 主队在主客之争中的"市场实力份额"
    - 实力调整 = SKILL_FACTOR * (implied_share_h - 0.5)
    - 期望主客总进球 = BASE_TOTAL_GOALS
    - 主场加成 = HOME_ADV
    - λ_home = BASE/2 + HOME_ADV + 实力调整
    - λ_away = BASE/2 - HOME_ADV - 实力调整
    """
    base = BASE_TOTAL_GOALS / 2  # 1.2
    denom = max(imp_w + imp_l, 0.01)
    share_h = imp_w / denom
    skill_adj = SKILL_FACTOR * (share_h - 0.5)
    lam_h = base + HOME_ADV + skill_adj
    lam_a = base - HOME_ADV - skill_adj
    lam_h = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_h))
    lam_a = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_a))
    return round(lam_h, 3), round(lam_a, 3)


def parse_kickoff_date(kickoff_str, fetch_date):
    """'06-06 00:30' → '2026-06-06'，用抓取日补年份"""
    year = fetch_date[:4]
    if not kickoff_str or len(kickoff_str) < 5:
        return fetch_date, '00:00'
    mmdd = kickoff_str[:5]
    time_str = kickoff_str[6:].strip() if len(kickoff_str) > 6 else '00:00'
    if not time_str or ':' not in time_str:
        time_str = '00:00'
    return f'{year}-{mmdd}', time_str


def load_om_matches(date_str):
    """读 OM raw 数据"""
    path = os.path.join(RAW_OM, f'{date_str.replace("-", "")}.json')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('matches', {})


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


def build_predictions(om_matches, fetch_date):
    """从 OM matches 生成 INSERT 行"""
    rows = []
    for key, m in om_matches.items():
        info = m.get('info', {})
        odds = m.get('odds', {})
        avg = odds.get('avg', {}) or {}
        imp = avg.get('implied_prob', {}) or {}

        if not imp.get('w') or not imp.get('d') or not imp.get('l'):
            continue

        p_w, p_d, p_l = imp['w'], imp['d'], imp['l']
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
            'odds_win': avg.get('odds_w', 0) or 0,
            'odds_draw': avg.get('odds_d', 0) or 0,
            'odds_loss': avg.get('odds_l', 0) or 0,
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
            'avg_odds_close_w': avg.get('odds_w', 0) or 0,
            'avg_odds_close_d': avg.get('odds_d', 0) or 0,
            'avg_odds_close_l': avg.get('odds_l', 0) or 0,
            'avg_margin': avg.get('margin', 0) or 0,
            'source': 'om_only',
            'odds_source': 'avg',
            # 亚盘让球盘（OM数据通常无亚盘，后续由fetch_pinnacle_odds补充）
            'ah_handicap': 0,
            'ah_home_water': 0,
            'ah_away_water': 0,
            'ah_source': '',
        })
    return rows


def insert_predictions(rows, db_path):
    """INSERT 到 poisson_predictions（A1: 入库前清掉 om_only 旧记录 → 治本去重）"""
    if not rows:
        return 0, 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 确保亚盘字段存在
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT')]:
        try:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass  # 列已存在
    
    # A1: 入库前先清掉当天 om_only 旧记录（防止 OM 重跑入库造成 om_only 自身重复）
    dates_to_clean = sorted({r["date"] for r in rows})
    if dates_to_clean:
        placeholders = ",".join("?" * len(dates_to_clean))
        cur.execute(
            f"DELETE FROM poisson_predictions WHERE date IN ({placeholders}) AND source = 'om_only'",
            dates_to_clean
        )
        print(f"  [A1] 清掉 om_only 旧记录: {cur.rowcount} 条 (date: {', '.join(dates_to_clean)})")
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

    # 1) 读 OM
    matches = load_om_matches(args.date)
    print(f'📥 OM matches: {len(matches)} 场')
    if not matches:
        print('⚠️ 无 OM 数据，跳过')
        return 0

    # 2) 生成预测
    rows = build_predictions(matches, args.date)
    print(f'🧮 生成预测: {len(rows)} 场')

    # 3) INSERT
    inserted, skipped = insert_predictions(rows, db_path)
    print(f'✅ 新增: {inserted} 场')
    print(f'⏭️ 跳过（已存在）: {skipped} 场')

    return 0


if __name__ == '__main__':
    sys.exit(main())
