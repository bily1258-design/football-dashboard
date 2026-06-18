#!/usr/bin/env python3
"""calc_lambda.py — 对 DB 里 home_lambda=0 的记录，用赔率反算 lambda

逻辑与 predict_from_odds.py 的 estimate_lambdas 一致：
- 优先用 implied_prob（来自百家/市场隐含概率）
- 次选用百家平均收盘赔率反推 implied
- 用 implied 反推 λ_home/λ_away
- UPDATE 到 DB

用法：
  python calc_lambda.py --db data/football.db
  python calc_lambda.py --db data/football.db --date 2026-06-18
"""

import os
import sys
import math
import sqlite3
import argparse

# === 算法常量（与 predict_from_odds.py 一致）===
BASE_TOTAL_GOALS = 2.4
HOME_ADV = 0.15
SKILL_FACTOR = 0.6
LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0


def estimate_lambdas(imp_w, imp_d, imp_l):
    """从 implied 概率反推 λ_home / λ_away"""
    base = BASE_TOTAL_GOALS / 2  # 1.2
    denom = max(imp_w + imp_l, 0.01)
    share_h = imp_w / denom
    skill_adj = SKILL_FACTOR * (share_h - 0.5)
    lam_h = base + HOME_ADV + skill_adj
    lam_a = base - HOME_ADV - skill_adj
    lam_h = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_h))
    lam_a = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_a))
    return round(lam_h, 3), round(lam_a, 3)


def odds_to_implied(odds_w, odds_d, odds_l):
    """从赔率反算隐含概率（去抽水）"""
    if not odds_w or not odds_d or not odds_l or odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None
    raw_w = 1.0 / odds_w
    raw_d = 1.0 / odds_d
    raw_l = 1.0 / odds_l
    total = raw_w + raw_d + raw_l
    return raw_w / total, raw_d / total, raw_l / total


def main():
    parser = argparse.ArgumentParser(description='补算 DB 中 lambda=0 记录的泊松参数')
    parser.add_argument('--db', type=str, default=None, help='数据库路径')
    parser.add_argument('--date', type=str, default=None, help='只处理指定日期')
    parser.add_argument('--all', action='store_true', help='处理所有 lambda=0 的记录（不限日期）')
    args = parser.parse_args()

    REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = args.db or os.environ.get('FOOTBALL_DB_PATH',
        os.path.join(REPO_DIR, 'data', 'football.db'))

    if not os.path.exists(db_path):
        print(f'[ERROR] DB 不存在: {db_path}')
        return 1

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 查询 lambda=0 的记录
    if args.date:
        cur.execute("""
            SELECT id, home_team, away_team, implied_prob_w, implied_prob_d, implied_prob_l,
                   avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                   odds_win, odds_draw, odds_loss, source
            FROM poisson_predictions
            WHERE date = ? AND (home_lambda = 0 OR home_lambda IS NULL)
        """, (args.date,))
    elif args.all:
        cur.execute("""
            SELECT id, home_team, away_team, implied_prob_w, implied_prob_d, implied_prob_l,
                   avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                   odds_win, odds_draw, odds_loss, source
            FROM poisson_predictions
            WHERE home_lambda = 0 OR home_lambda IS NULL
        """)
    else:
        # 默认：只处理最近 7 天
        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        cur.execute("""
            SELECT id, home_team, away_team, implied_prob_w, implied_prob_d, implied_prob_l,
                   avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                   odds_win, odds_draw, odds_loss, source
            FROM poisson_predictions
            WHERE date >= ? AND (home_lambda = 0 OR home_lambda IS NULL)
        """, (week_ago,))

    rows = cur.fetchall()
    print(f'📊 需要补算 lambda 的记录: {len(rows)} 条')

    updated = 0
    skipped = 0
    for row in rows:
        rid, home, away, imp_w, imp_d, imp_l, avg_w, avg_d, avg_l, odds_w, odds_d, odds_l, source = row

        # 优先级：implied_prob > 百家收盘赔率 > 竞彩赔率
        lam_h, lam_a = None, None

        # 1. 尝试用 implied_prob
        if imp_w and imp_d and imp_l and imp_w > 0 and imp_d > 0 and imp_l > 0:
            s = imp_w + imp_d + imp_l
            lam_h, lam_a = estimate_lambdas(imp_w / s, imp_d / s, imp_l / s)

        # 2. 尝试用百家平均收盘赔率
        if lam_h is None and avg_w and avg_d and avg_l and avg_w > 1 and avg_d > 1 and avg_l > 1:
            p_w, p_d, p_l = odds_to_implied(avg_w, avg_d, avg_l)
            if p_w and p_d and p_l:
                lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l)

        # 3. 尝试用竞彩赔率
        if lam_h is None and odds_w and odds_d and odds_l and odds_w > 1 and odds_d > 1 and odds_l > 1:
            p_w, p_d, p_l = odds_to_implied(odds_w, odds_d, odds_l)
            if p_w and p_d and p_l:
                lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l)

        if lam_h is not None and lam_a is not None:
            # 同时用 lambda 重算泊松概率
            from math import factorial
            def poisson_pmf(lam, k):
                if lam <= 0: return 1.0 if k == 0 else 0.0
                return (lam ** k) * math.exp(-lam) / factorial(k)

            p_h = [poisson_pmf(lam_h, k) for k in range(11)]
            p_a = [poisson_pmf(lam_a, k) for k in range(11)]
            p_w = p_d = p_l = 0.0
            for k in range(11):
                for j in range(11):
                    p = p_h[k] * p_a[j]
                    if k > j: p_w += p
                    elif k == j: p_d += p
                    else: p_l += p

            cur.execute("""
                UPDATE poisson_predictions
                SET home_lambda = ?, away_lambda = ?,
                    poisson_win = ?, poisson_draw = ?, poisson_loss = ?,
                    had_lambda_h = ?, had_lambda_a = ?
                WHERE id = ?
            """, (lam_h, lam_a,
                  round(p_w, 3), round(p_d, 3), round(p_l, 3),
                  lam_h, lam_a,
                  rid))
            updated += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f'✅ 补算完成: {updated} 条更新, {skipped} 条无赔率跳过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
