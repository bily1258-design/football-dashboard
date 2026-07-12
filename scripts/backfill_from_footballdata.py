#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_from_footballdata.py — 从 football-data.co.uk CSV 批量补跑历史预测+赛果

从 football-data.co.uk 下载历史赔率CSV，用与 predict_from_odds.py 相同的泊松算法
生成预测，并同时回填赛果。用于快速积累"预测+结果"配对样本，支撑概率校准。

用法:
  python scripts/backfill_from_footballdata.py --db data/football.db
  python scripts/backfill_from_footballdata.py --db data/football.db --season 2425
  python scripts/backfill_from_footballdata.py --db data/football.db --season 2324 --dry-run
  python scripts/backfill_from_footballdata.py --db data/football.db --season 2425 --league E0,SP1,D1
"""

import csv
import math
import os
import sys
import sqlite3
import argparse
import urllib.request
import io
from datetime import datetime

# --- 泊松算法（与 predict_from_odds.py 一致）---
BASE_TOTAL_GOALS = 2.4
HOME_ADV = 0.15
SKILL_FACTOR = 0.6
POISSON_WEIGHT = 0.5
IMPLIED_WEIGHT = 0.5
LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0


def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_match_probs(lam_h, lam_a, max_goals=10):
    pw = pd = pl = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(lam_h, h) * poisson_pmf(lam_a, a)
            if h > a:
                pw += p
            elif h == a:
                pd += p
            else:
                pl += p
    return pw, pd, pl


def estimate_lambdas(imp_w, imp_d, imp_l):
    diff = imp_w - imp_l
    ratio_h = (1 + diff * SKILL_FACTOR) / 2
    lam_h = max(LAMBDA_MIN, min(LAMBDA_MAX,
                BASE_TOTAL_GOALS * ratio_h * (1 + HOME_ADV)))
    lam_a = max(LAMBDA_MIN, min(LAMBDA_MAX,
                BASE_TOTAL_GOALS * (1 - ratio_h)))
    return lam_h, lam_a


def implied_from_odds(odds_w, odds_d, odds_l):
    if not odds_w or not odds_d or not odds_l:
        return None, None, None
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None
    raw_w = 1.0 / odds_w
    raw_d = 1.0 / odds_d
    raw_l = 1.0 / odds_l
    total = raw_w + raw_d + raw_l
    return raw_w / total, raw_d / total, raw_l / total


# --- 联赛映射 ---
LEAGUE_MAP = {
    'E0': '英超', 'E1': '英冠', 'E2': '英甲', 'E3': '英乙', 'EC': '英非联',
    'SP1': '西甲', 'SP2': '西乙',
    'D1': '德甲', 'D2': '德乙',
    'I1': '意甲', 'I2': '意乙',
    'F1': '法甲', 'F2': '法乙',
    'N1': '荷甲',
    'B1': '比甲',
    'P1': '葡超',
    'T1': '土超',
    'G1': '希腊超',
}

DEFAULT_LEAGUES = list(LEAGUE_MAP.keys())

# 赔率列优先级：Pinnacle收盘 > Pinnacle > 平均 > B365
ODDS_COLUMNS = [
    ('PSCH', 'PSCD', 'PSCA', 'pinnacle_close'),
    ('PSH', 'PSD', 'PSA', 'pinnacle'),
    ('AvgH', 'AvgD', 'AvgA', 'avg'),
    ('B365H', 'B365D', 'B365A', 'b365'),
]


def fetch_csv(season, league):
    url = f'https://www.football-data.co.uk/mmz4281/{season}/{league}.csv'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': 'text/csv',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = raw.decode('utf-8-sig', errors='replace')
            return text
    except Exception as e:
        print(f'  ❌ 下载失败 {season}/{league}: {e}')
        return None


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def extract_odds(row):
    for col_w, col_d, col_a, source in ODDS_COLUMNS:
        try:
            w = float(row.get(col_w, '') or 0)
            d = float(row.get(col_d, '') or 0)
            a = float(row.get(col_a, '') or 0)
            if w > 0 and d > 0 and a > 0:
                return w, d, a, source
        except (ValueError, TypeError):
            continue
    return None, None, None, None


def get_db_columns(db_path):
    """获取DB表的实际列名"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("PRAGMA table_info(poisson_predictions)")
    cols = {row[1] for row in c.fetchall()}
    conn.close()
    return cols


def process_match(row, league_code, db_cols):
    """处理单场比赛：赔率→预测+赛果，只填DB已有的列"""
    date_raw = row.get('Date', '').strip()
    if not date_raw:
        return None
    try:
        dt = datetime.strptime(date_raw, '%d/%m/%Y')
        date_str = dt.strftime('%Y-%m-%d')
    except ValueError:
        return None

    home = row.get('HomeTeam', '').strip()
    away = row.get('AwayTeam', '').strip()
    if not home or not away:
        return None

    # 赛果
    fthg = row.get('FTHG', '').strip()
    ftag = row.get('FTAG', '').strip()
    ftr = row.get('FTR', '').strip()
    has_result = fthg and ftag and ftr in ('H', 'D', 'A')

    score = ''
    outcome = ''
    if has_result:
        score = f'{fthg}-{ftag}'
        outcome_map = {'H': '主胜', 'D': '平局', 'A': '客胜'}
        outcome = f'{outcome_map[ftr]} {score}'

    # 赔率
    odds_w, odds_d, odds_a, odds_source = extract_odds(row)
    if odds_w is None:
        return None

    # 隐含概率
    p_w, p_d, p_l = implied_from_odds(odds_w, odds_d, odds_a)
    if p_w is None:
        return None

    s = p_w + p_d + p_l
    p_w, p_d, p_l = p_w / s, p_d / s, p_l / s

    # 泊松
    lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l)
    pp_w, pp_d, pp_l = poisson_match_probs(lam_h, lam_a)

    # final
    f_w = POISSON_WEIGHT * pp_w + IMPLIED_WEIGHT * p_w
    f_d = POISSON_WEIGHT * pp_d + IMPLIED_WEIGHT * p_d
    f_l = POISSON_WEIGHT * pp_l + IMPLIED_WEIGHT * p_l
    s2 = f_w + f_d + f_l
    f_w, f_d, f_l = f_w / s2, f_d / s2, f_l / s2

    if f_w >= f_d and f_w >= f_l:
        prediction = '主胜'
        pred_prob = f_w
    elif f_l >= f_d:
        prediction = '客胜'
        pred_prob = f_l
    else:
        prediction = '平局'
        pred_prob = f_d

    best_dir = prediction

    time_str = row.get('Time', '').strip()
    kickoff_time = f'{date_str} {time_str}' if time_str else f'{date_str} 00:00'

    league_cn = LEAGUE_MAP.get(league_code, league_code)

    # 构建record，只包含DB已有的列
    all_fields = {
        'date': date_str,
        'kickoff_time': kickoff_time,
        'league': league_cn,
        'home_team': home,
        'away_team': away,
        'prediction': prediction,
        'prediction_prob': round(pred_prob, 3),
        'odds_win': odds_w,
        'odds_draw': odds_d,
        'odds_loss': odds_a,
        'poisson_win': round(pp_w, 3),
        'poisson_draw': round(pp_d, 3),
        'poisson_loss': round(pp_l, 3),
        'final_win': round(f_w, 3),
        'final_draw': round(f_d, 3),
        'final_loss': round(f_l, 3),
        'implied_prob_w': round(p_w, 3),
        'implied_prob_d': round(p_d, 3),
        'implied_prob_l': round(p_l, 3),
        'home_lambda': round(lam_h, 4),
        'away_lambda': round(lam_a, 4),
        'pinnacle_close_w': float(row.get('PSCH', '') or 0),
        'pinnacle_close_d': float(row.get('PSCD', '') or 0),
        'pinnacle_close_l': float(row.get('PSCA', '') or 0),
        'pinnacle_margin': 0,
        'hkjc_close_w': 0,
        'hkjc_close_d': 0,
        'hkjc_close_l': 0,
        'avg_odds_close_w': float(row.get('AvgH', '') or 0),
        'avg_odds_close_d': float(row.get('AvgD', '') or 0),
        'avg_odds_close_l': float(row.get('AvgA', '') or 0),
        'avg_margin': 0,
        'source': 'footballdata',
        'odds_source': odds_source,
        'ah_handicap': 0,
        'ah_home_water': 0,
        'ah_away_water': 0,
        'ah_source': '',
        'actual_outcome': outcome,
        'best_direction_cn': best_dir,
    }

    # 只保留DB已有的列
    record = {k: v for k, v in all_fields.items() if k in db_cols}
    return record


def insert_rows(rows, db_path, db_cols, dry_run=False):
    """UPSERT到DB"""
    if not rows:
        return 0, 0, 0

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    inserted = 0
    updated_outcome = 0
    skipped = 0

    for r in rows:
        c.execute("""
            SELECT id, actual_outcome FROM poisson_predictions
            WHERE date = ? AND home_team = ? AND away_team = ?
        """, (r['date'], r['home_team'], r['away_team']))
        existing = c.fetchone()

        if existing:
            ex_id, ex_outcome = existing
            if (not ex_outcome or ex_outcome == '') and r.get('actual_outcome'):
                if not dry_run:
                    c.execute("""
                        UPDATE poisson_predictions SET actual_outcome = ?
                        WHERE id = ?
                    """, (r['actual_outcome'], ex_id))
                updated_outcome += 1
            skipped += 1
        else:
            if not dry_run:
                cols = list(r.keys())
                vals = [r[k] for k in cols]
                placeholders = ','.join(['?' for _ in cols])
                col_names = ','.join(cols)
                c.execute(
                    f"INSERT INTO poisson_predictions ({col_names}) VALUES ({placeholders})",
                    vals
                )
            inserted += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return inserted, updated_outcome, skipped


def main():
    parser = argparse.ArgumentParser(
        description="从 football-data.co.uk 批量补跑历史预测+赛果")
    parser.add_argument('--db', required=True, help='数据库路径')
    parser.add_argument('--season', default='2425',
                        help='赛季代码，如2425/2324/2223（默认2425）')
    parser.add_argument('--league', default='',
                        help='联赛代码，逗号分隔，如E0,SP1,D1（默认全部主要联赛）')
    parser.add_argument('--dry-run', action='store_true', help='只显示不写入')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详情')
    args = parser.parse_args()

    leagues = args.league.split(',') if args.league else DEFAULT_LEAGUES
    season = args.season

    # 获取DB列名（兼容不同schema）
    db_cols = get_db_columns(args.db)
    if not db_cols:
        print('❌ DB中找不到 poisson_predictions 表')
        return

    print(f'📊 补跑赛季 {season}，联赛: {", ".join(leagues)}')
    if args.dry_run:
        print('  [dry-run 模式]')

    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    total_no_odds = 0

    for league_code in leagues:
        league_cn = LEAGUE_MAP.get(league_code, league_code)
        text = fetch_csv(season, league_code)
        if not text:
            continue

        rows = parse_csv(text)
        if not rows:
            print(f'  {league_code} {league_cn}: ⚠️ CSV为空')
            continue

        processed = []
        no_odds = 0
        for row in rows:
            result = process_match(row, league_code, db_cols)
            if result is None:
                no_odds += 1
                continue
            processed.append(result)

        inserted, updated, skipped = insert_rows(processed, args.db, db_cols, args.dry_run)
        total_inserted += inserted
        total_updated += updated
        total_skipped += skipped
        total_no_odds += no_odds

        tag = ' [dry]' if args.dry_run else ''
        print(f'  {league_code} {league_cn}: {len(rows)}场CSV, '
              f'新插入{tag} {inserted}条, 补赛果 {updated}条, '
              f'已存在 {skipped}条, 无赔率 {no_odds}条')

        if args.verbose and processed:
            for r in processed[:5]:
                actual = r.get('actual_outcome', '未开奖')
                pred = r.get('prediction', '?')
                prob = r.get('prediction_prob', 0)
                print(f'    {r["date"]} {r["home_team"]} vs {r["away_team"]} '
                      f'→ {pred}({prob:.1%}) {actual}')
            if len(processed) > 5:
                print(f'    ... 还有 {len(processed)-5} 条')

    print(f'\n🎉 赛季{season}完成: 新插入 {total_inserted}条, '
          f'补赛果 {total_updated}条, 已存在 {total_skipped}条, '
          f'无赔率 {total_no_odds}条')

    # 统计
    conn = sqlite3.connect(args.db)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM poisson_predictions")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM poisson_predictions WHERE actual_outcome IS NOT NULL AND actual_outcome != ''")
    has_result = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM poisson_predictions WHERE source = 'footballdata'")
    fd_count = c.fetchone()[0]
    conn.close()

    print(f'\n📈 DB总览: {total}条预测, {has_result}条已开奖, '
          f'{fd_count}条来自footballdata')


if __name__ == '__main__':
    main()
