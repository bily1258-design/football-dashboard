#!/usr/bin/env python3
"""
定向补爬 — 指定球队，搜它们的完场数据补技统
用法: python3 scripts/targeted_backfill.py --teams "FC首尔,蔚山HD,浦项制铁" [--days 90] [--workers 15]
"""
import sqlite3, os, sys, re, time, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')

def safe_fetch(url, headers=None, retries=3, timeout=15, delay=0.2):
    import urllib.request
    for i in range(retries):
        try:
            time.sleep(delay)
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            if i < retries - 1:
                time.sleep(1)
            else:
                return None

def _extract_tech_from_html(html):
    stats = {}
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>射门</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['shots'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>射正</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['shots_on_target'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)%</span><span>控球率</span><span[^>]*>([^<]+)%</span></div>", html)
    if m:
        stats['possession'] = {'home': float(m.group(1)), 'away': float(m.group(2))}
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>角球</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['corners'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}
    return stats if stats else None

def find_team_matches(conn, team_names, days_back=90):
    """在 poisson_predictions 和 match_tech_stats 中找到涉及指定球队的比赛"""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    matches = []

    # 从 poisson_predictions 找
    for team in team_names:
        rows = conn.execute("""
            SELECT DISTINCT p.home_team, p.away_team, p.date, p.match_id
            FROM poisson_predictions p
            WHERE (p.home_team LIKE ? OR p.away_team LIKE ?)
              AND p.date >= ?
              AND p.match_id IS NOT NULL AND p.match_id != ''
            ORDER BY p.date DESC
        """, (f'%{team}%', f'%{team}%', cutoff)).fetchall()
        for h, a, d, mid in rows:
            matches.append((h, a, d, int(mid)))

    # 从 match_tech_stats 找已有数据的
    existing_sids = set()
    if matches:
        sids = tuple(set(m[3] for m in matches))
        # sqlite IN with only one element
        if len(sids) == 1:
            pass  # will check later
        existing = conn.execute("""
            SELECT DISTINCT sid FROM match_tech_stats
            WHERE sid IN ({})
        """.format(','.join(str(s) for s in sids))).fetchall()
        existing_sids = set(r[0] for r in existing)

    # 过滤：去重 + 去掉已有数据的
    seen = set()
    to_fetch = []
    already = 0
    for h, a, d, sid in matches:
        key = (h, a, d)
        if key in seen:
            continue
        seen.add(key)
        if sid in existing_sids:
            already += 1
        else:
            to_fetch.append((h, a, d, sid))

    return to_fetch, already

def fetch_one(match):
    home, away, date, sid = match
    url = f'https://live.titan007.com/detail/{sid}cn.htm'
    headers = {
        'Referer': 'https://live.titan007.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cookie': 'UseCookie=yes; sport=1',
    }
    html = safe_fetch(url, headers=headers, delay=0.15)
    if not html:
        return None
    if '>射门<' not in html or '>控球率<' not in html:
        return None
    tech = _extract_tech_from_html(html)
    if not tech:
        return None
    hp = tech.get('possession', {}).get('home', 0)
    ap = tech.get('possession', {}).get('away', 0)
    hs = tech.get('shots', {}).get('home', 0)
    ash = tech.get('shots', {}).get('away', 0)
    hst = tech.get('shots_on_target', {}).get('home', 0)
    ast = tech.get('shots_on_target', {}).get('away', 0)
    hc = tech.get('corners', {}).get('home', 0)
    ac = tech.get('corners', {}).get('away', 0)
    if any([hp, ap, hs, ash, hst, ast, hc, ac]):
        return (home, away, date, hp, ap, hs, ash, hst, ast, hc, ac, sid)
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description='定向补爬球队技统')
    parser.add_argument('--teams', type=str, required=True,
                       help='球队名称，逗号分隔，如 "FC首尔,蔚山HD"')
    parser.add_argument('--days', type=int, default=90,
                       help='往前搜多少天 (default: 90)')
    parser.add_argument('--workers', type=int, default=15,
                       help='并发数 (default: 15)')
    args = parser.parse_args()

    teams = [t.strip() for t in args.teams.split(',') if t.strip()]
    if not teams:
        print('错误: 未指定球队')
        sys.exit(1)

    print(f'定向补爬: {", ".join(teams)}')
    print(f'往前 {args.days} 天, {args.workers} 线程并发')

    conn = sqlite3.connect(DB_PATH)
    to_fetch, already = find_team_matches(conn, teams, days_back=args.days)

    print(f'已有数据: {already} 场, 需爬取: {len(to_fetch)} 场')
    if to_fetch:
        # 显示将要爬取的比赛
        for h, a, d, sid in to_fetch[:20]:
            print(f'  {d} {h:16s} vs {a:16s} sid={sid}')
        if len(to_fetch) > 20:
            print(f'  ... 还有 {len(to_fetch)-20} 场')

    if not to_fetch:
        print('无需爬取。完成。')
        conn.close()
        return

    # 并发爬取
    print(f'\n开始爬取 {len(to_fetch)} 场...')
    t0 = time.time()
    success = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fut_map = {pool.submit(fetch_one, m): m for m in to_fetch}
        for fut in as_completed(fut_map):
            result = fut.result()
            if result:
                conn.execute("""
                    INSERT OR REPLACE INTO match_tech_stats
                        (home_team, away_team, date, home_possession, away_possession,
                         home_shots, away_shots, home_shots_on_target, away_shots_on_target,
                         home_corners, away_corners, sid)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, result)
                success += 1
            else:
                fail += 1
            if (success + fail) % 20 == 0:
                print(f'  进度: {success+fail}/{len(to_fetch)} (成功 {success}, 失败 {fail})')

    conn.commit()
    elapsed = time.time() - t0
    print(f'\n完成: 成功 {success} 场, 失败 {fail} 场, 耗时 {elapsed:.1f}s')
    conn.close()

if __name__ == '__main__':
    main()
