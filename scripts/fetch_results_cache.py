#!/usr/bin/env python3
"""
fetch_results_cache.py — 抓取赛果生成500.com格式缓存（供review.py的backfill_from_500com使用）

数据源：足彩网竞彩比分直播（同源队名，匹配率最高）
输出：data/cache/500com_results_{YYYYMMDD}.json

注意：足彩网按竞彩销售日分组，DB按kickoff日期分组，存在1天偏移。
      本脚本对每个目标日期抓前1天到后1天共3天的赛果页面，合并去重后保存。

用法:
  python scripts/fetch_results_cache.py --date 2026-06-25
  python scripts/fetch_results_cache.py --date 2026-06-25 --backfill
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(REPO_DIR, 'data', 'cache')
DB_PATH = os.path.join(REPO_DIR, 'data', 'football.db')


def fetch_zgzcw_results(date_str: str) -> list:
    """从足彩网抓赛果（竞彩+北单）"""
    from fetch_zgzcw_results import fetch_results, PAGE_JZ, PAGE_BD
    jz = fetch_results(date_str, PAGE_JZ)
    bd = fetch_results(date_str, PAGE_BD)
    return jz + bd


def to_500com_format(results: list, date_str: str) -> dict:
    """转成review.py的backfill_from_500com兼容格式"""
    jingcai = []
    for r in results:
        score = r.get('score', '')
        home_score, away_score = 0, 0
        m = re.match(r'(\d+)\s*[-:]\s*(\d+)', score)
        if m:
            home_score, away_score = int(m.group(1)), int(m.group(2))
        jingcai.append({
            'home': r.get('home', ''),
            'away': r.get('away', ''),
            'score': score,
            'home_score': home_score,
            'away_score': away_score,
            'outcome': r.get('outcome', '').split()[0] if r.get('outcome') else '',
            'kickoff': date_str[5:].replace('-', '-'),
        })
    return {
        'date': date_str,
        'jingcai': jingcai,
        'wanchang': [],
        'fetch_time': datetime.now().isoformat(),
        'source': 'zgzcw',
    }


def save_cache(date_str: str, data: dict) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    base = date_str.replace('-', '')
    path = os.path.join(CACHE_DIR, f'500com_results_{base}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def backfill_db(date_str: str) -> int:
    """用缓存回填DB（全局匹配，不限制日期）"""
    import sqlite3
    if not os.path.exists(DB_PATH):
        print(f"  ⚠️ DB不存在: {DB_PATH}")
        return 0

    # 加载目标日期±1的缓存
    all_results = []
    for offset in [-1, 0, 1]:
        d = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=offset)).strftime('%Y-%m-%d')
        base = d.replace('-', '')
        cache_file = os.path.join(CACHE_DIR, f'500com_results_{base}.json')
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for r in data.get('jingcai', []):
                all_results.append(r)
            for r in data.get('wanchang', []):
                all_results.append(r)

    if not all_results:
        print(f"  ⚠️ 无赛果缓存")
        return 0

    print(f"  赛果缓存: {len(all_results)} 条（含前后1天）")

    # 队名匹配回填（全局，不限日期）
    sys.path.insert(0, SCRIPT_DIR)
    from review import team_match

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # 只匹配目标日期附近3天的无赛果记录
    d_start = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    d_end = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    cursor.execute("""
        SELECT id, home_team, away_team, actual_outcome, date
        FROM poisson_predictions 
        WHERE date BETWEEN ? AND ?
    """, (d_start, d_end))

    db_records = [dict(r) for r in cursor.fetchall()]
    updated = 0
    for rec in db_records:
        if rec['actual_outcome'] and re.search(r'\d+-\d+', rec['actual_outcome']):
            continue
        for res in all_results:
            if team_match(rec['home_team'], res['home']) and team_match(rec['away_team'], res['away']):
                score = res.get('score', '')
                hs = res.get('home_score', 0)
                as_ = res.get('away_score', 0)
                outcome = '主胜' if hs > as_ else ('平局' if hs == as_ else '客胜')
                cursor.execute(
                    "UPDATE poisson_predictions SET actual_outcome = ? WHERE id = ?",
                    (f"{outcome} {score}", rec['id'])
                )
                updated += 1
                break
    conn.commit()
    conn.close()
    return updated


def main():
    parser = argparse.ArgumentParser(description="抓赛果生成500.com格式缓存")
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--backfill', action='store_true', help='回填DB')
    args = parser.parse_args()

    date_str = args.date
    print(f"📥 抓取赛果: {date_str}")

    # 抓3天（前1天+当天+后1天）解决日期偏移
    all_results = []
    seen_keys = set()
    for offset in [-1, 0, 1]:
        d = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=offset)).strftime('%Y-%m-%d')
        print(f"  抓取足彩网 {d}...")
        results = fetch_zgzcw_results(d)
        # 去重（同主客队）
        for r in results:
            key = (r.get('home', ''), r.get('away', ''))
            if key not in seen_keys:
                seen_keys.add(key)
                all_results.append(r)

    print(f"  合计: {len(all_results)} 条赛果（去重后）")

    if not all_results:
        print("  ⚠️ 无赛果数据")
        return

    data = to_500com_format(all_results, date_str)
    path = save_cache(date_str, data)
    print(f"  ✅ 缓存已保存: {os.path.basename(path)} ({len(data['jingcai'])} 场)")

    if args.backfill:
        count = backfill_db(date_str)
        print(f"  ✅ DB回填: {count} 条")


if __name__ == '__main__':
    main()
