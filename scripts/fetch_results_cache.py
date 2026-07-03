#!/usr/bin/env python3
"""
fetch_results_cache.py — 从500.com完场页抓赛果生成缓存（供review.py的backfill_from_500com使用）

数据源：500.com完场页（live.500.com/wanchang.php）
输出：data/cache/500com_results_{YYYYMMDD}.json

用法:
  python scripts/fetch_results_cache.py --date 2026-06-25
  python scripts/fetch_results_cache.py --date 2026-06-25 --backfill
"""

import os
import re
import sys
import json
import argparse
import urllib.request
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(REPO_DIR, 'data', 'cache')
DB_PATH = os.path.join(REPO_DIR, 'data', 'football.db')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def fetch_page(url, encoding='utf-8'):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode('gbk', errors='replace')
    except Exception as e:
        print(f'  ❌ 请求失败: {e}')
        return None


def parse_wanchang_html(html):
    """解析500.com完场页HTML"""
    results = []

    table_match = re.search(r'<table[^>]*id="table_match"[^>]*>(.*?)</table>', html, re.S)
    if not table_match:
        trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    else:
        trs = re.findall(r'<tr[^>]*>(.*?)</tr>', table_match.group(1), re.S)

    for tr in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 8:
            continue

        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]

        if clean_tds[0] in ('赛事', '场次'):
            continue

        # 新格式: 赛事|轮次|时间|主队|比分|客队|数据|直播|分析
        # 分数列有 '-' 表示未开始/进行中，有数字比分才是完场
        league = clean_tds[0]
        home_raw = clean_tds[3] if len(clean_tds) > 3 else ''
        score_raw = clean_tds[4] if len(clean_tds) > 4 else ''
        away_raw = clean_tds[5] if len(clean_tds) > 5 else ''

        # 比分如果是 '-' 说明没结束，跳过
        if score_raw.strip() == '-':
            continue

        # 清理队名
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        home = re.sub(r'^\[世\d+\]\d*', '', home).strip()
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        away = re.sub(r'\d*\[世\d+\]$', '', away).strip()

        # 提取比分
        h_score = a_score = None
        m = re.match(r'^(\d+).*?(\d+)$', score_raw.replace(' ', ''))
        if m:
            h_score = int(m.group(1))
            a_score = int(m.group(2))

        if h_score is not None and a_score is not None:
            results.append({
                'home': home,
                'away': away,
                'score': f'{h_score}-{a_score}',
                'home_score': h_score,
                'away_score': a_score,
                'outcome': '主胜' if h_score > a_score else ('平局' if h_score == a_score else '客胜'),
                'league': league,
                'kickoff': '',
            })

    return results


def fetch_500com_results(date_str: str) -> list:
    """从500.com完场页抓赛果"""
    url = f'https://live.500.com/wanchang.php?e={date_str}'
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    return parse_wanchang_html(html)


def save_cache(date_str: str, data: dict) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    base = date_str.replace('-', '')
    path = os.path.join(CACHE_DIR, f'500com_results_{base}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def backfill_db(date_str: str) -> int:
    """用缓存回填DB"""
    import sqlite3
    if not os.path.exists(DB_PATH):
        print(f"  ⚠️ DB不存在: {DB_PATH}")
        return 0

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

    sys.path.insert(0, SCRIPT_DIR)
    from review import team_match

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
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
    parser = argparse.ArgumentParser(description="从500.com抓赛果生成缓存")
    parser.add_argument('--date', required=True, help='日期 YYYY-MM-DD')
    parser.add_argument('--backfill', action='store_true', help='回填DB')
    args = parser.parse_args()

    date_str = args.date
    print(f"📥 抓取赛果(500.com): {date_str}")

    # 抓3天（前1天+当天+后1天）解决日期偏移
    all_results = []
    seen_keys = set()
    for offset in [-1, 0, 1]:
        d = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=offset)).strftime('%Y-%m-%d')
        print(f"  抓取500.com {d}...")
        results = fetch_500com_results(d)
        for r in results:
            key = (r.get('home', ''), r.get('away', ''))
            if key not in seen_keys:
                seen_keys.add(key)
                all_results.append(r)

    print(f"  合计: {len(all_results)} 条赛果（去重后）")

    if not all_results:
        print("  ⚠️ 无赛果数据")
        return

    # 保存为兼容格式（jingcai字段存所有结果，wanchang留空）
    data = {
        'date': date_str,
        'jingcai': all_results,
        'wanchang': [],
        'fetch_time': datetime.now().isoformat(),
        'source': '500com',
    }
    path = save_cache(date_str, data)
    print(f"  ✅ 缓存已保存: {os.path.basename(path)} ({len(all_results)} 场)")

    if args.backfill:
        count = backfill_db(date_str)
        print(f"  ✅ DB回填: {count} 条")


if __name__ == '__main__':
    main()
