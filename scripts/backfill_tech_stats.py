#!/usr/bin/env python3
"""
补抓前 N 场比赛的技术统计（控球/射门/射正）到 DB
流程：
  1. 从 poisson_predictions 取最近 N 场（默认1000）
  2. 先从 results.json 和 match_analysis 已有的数据直接填充
  3. 其余用 sid 去 titan007 detail 页并发抓取
  4. 存入 match_tech_stats 表
"""
import json, sqlite3, os, sys, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')
RESULTS_PATH = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data', 'results.json')

# ---- 工具函数 ----

def _n(v, default=0.0):
    try:
        return float(v) if v is not None and v != '' else default
    except:
        return default

def safe_fetch(url, headers=None, retries=3, timeout=15, delay=0.2):
    """带重试的请求，默认延迟防反爬"""
    import urllib.request
    for i in range(retries):
        try:
            time.sleep(delay)  # 延迟防反爬
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            if i < retries - 1:
                time.sleep(1)
            else:
                return None

def _extract_tech_from_html(html):
    """从 live.titan007.com 的 HTML 中解析技术统计

    新版格式: <div class='data'><span class='red'>HOME</span><span>LABEL</span><span >AWAY</span></div>
    旧版格式: <td width=...>HOME</td><td width=...>LABEL</td><td width=...>AWAY</td>
    """
    stats = {}

    # ---- 新版 (div.data > span) ----
    # 射门
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>射门</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['shots'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}

    # 射正
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>射正</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['shots_on_target'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}

    # 控球率
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)%</span><span>控球率</span><span[^>]*>([^<]+)%</span></div>", html)
    if m:
        stats['possession'] = {'home': float(m.group(1)), 'away': float(m.group(2))}

    # 角球
    m = re.search(r"<div class='data'><span[^>]*>([^<]+)</span><span>角球</span><span[^>]*>([^<]+)</span></div>", html)
    if m:
        stats['corners'] = {'home': float(m.group(1).replace('%', '')), 'away': float(m.group(2).replace('%', ''))}

    # ---- 旧版 (td 表格布局) ----
    if not stats:
        # 收集所有td行: 值-标签-值三元组
        td_pairs = re.findall(
            r"<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>",
            html, re.DOTALL
        )
        for home_val, label, away_val in td_pairs:
            label = label.strip()
            home_val = home_val.strip().rstrip('%')
            away_val = away_val.strip().rstrip('%')
            try:
                hv = float(home_val)
                av = float(away_val)
            except ValueError:
                continue
            if label == '射门':
                stats['shots'] = {'home': hv, 'away': av}
            elif label == '射正':
                stats['shots_on_target'] = {'home': hv, 'away': av}
            elif label == '控球率':
                stats['possession'] = {'home': hv, 'away': av}
            elif label == '角球':
                stats['corners'] = {'home': hv, 'away': av}

    return stats if stats else None

def fetch_tech_stats_by_sid(sid):
    """从 live.titan007.com 详情页抓取技术统计，返回解析后的 dict"""
    url = f'https://live.titan007.com/detail/{sid}cn.htm'
    headers = {
        'Referer': 'https://live.titan007.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cookie': 'UseCookie=yes; sport=1',
    }
    html = safe_fetch(url, headers=headers, delay=0.15)
    if not html:
        return None

    # 先检查页面是否包含技术统计区域（快速排除空页面）
    if '>射门<' not in html or '>控球率<' not in html:
        return None

    return _extract_tech_from_html(html)

def parse_tech_from_match_analysis(stats_json_str):
    """从 match_analysis 的 tech_stats JSON 解析出需要的字段"""
    if not stats_json_str or stats_json_str == '{}':
        return {}
    try:
        d = json.loads(stats_json_str)
        return {
            'possession': _n(d.get('控球率')),
            'shots': _n(d.get('射门')),
            'shots_on_target': _n(d.get('射正')),
        }
    except:
        return {}


# ---- 主流程 ----

def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS match_tech_stats (
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            date TEXT NOT NULL,
            home_possession REAL DEFAULT 0,
            away_possession REAL DEFAULT 0,
            home_shots REAL DEFAULT 0,
            away_shots REAL DEFAULT 0,
            home_shots_on_target REAL DEFAULT 0,
            away_shots_on_target REAL DEFAULT 0,
            home_corners REAL DEFAULT 0,
            away_corners REAL DEFAULT 0,
            sid INTEGER,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (home_team, away_team, date)
        )
    """)
    conn.commit()

def backfill_from_results(conn):
    """从 results.json 导入技术统计"""
    if not os.path.exists(RESULTS_PATH):
        print('  results.json 不存在，跳过')
        return 0

    with open(RESULTS_PATH) as f:
        data = json.load(f)

    count = 0
    for m in data['matches']:
        hp = _n(m.get('home_possession'))
        ap = _n(m.get('away_possession'))
        hs = _n(m.get('home_shots'))
        ash = _n(m.get('away_shots'))
        hst = _n(m.get('home_shots_on_target'))
        ast = _n(m.get('away_shots_on_target'))

        # 角球（如果在results.json中有）
        hc = _n(m.get('home_corners'))
        ac = _n(m.get('away_corners'))
        if any([hp, ap, hs, ash, hst, ast, hc, ac]):
            conn.execute("""
                INSERT OR REPLACE INTO match_tech_stats
                    (home_team, away_team, date, home_possession, away_possession,
                     home_shots, away_shots, home_shots_on_target, away_shots_on_target,
                     home_corners, away_corners)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (m['home_team'], m['away_team'], m['date'],
                  hp, ap, hs, ash, hst, ast, hc, ac))
            count += 1

    conn.commit()
    print(f'  从 results.json 导入: {count} 场')
    return count

def backfill_from_match_analysis(conn):
    """从 match_analysis 表的 tech_stats 解析并导入"""
    rows = conn.execute("""
        SELECT ma.home_team, ma.away_team, substr(ma.match_time,1,10),
               ma.home_tech_stats, ma.away_tech_stats
        FROM match_analysis ma
        WHERE ma.home_tech_stats IS NOT NULL AND ma.home_tech_stats != ''
          AND ma.home_tech_stats != '{}'
          AND ma.away_tech_stats IS NOT NULL AND ma.away_tech_stats != ''
          AND ma.away_tech_stats != '{}'
    """).fetchall()

    count = 0
    for home, away, date, hts, ats in rows:
        home_d = parse_tech_from_match_analysis(hts)
        away_d = parse_tech_from_match_analysis(ats)

        hp = home_d.get('possession', 0)
        ap = away_d.get('possession', 0)
        hs = home_d.get('shots', 0)
        ash = away_d.get('shots', 0)
        hst = home_d.get('shots_on_target', 0)
        ast = away_d.get('shots_on_target', 0)

        if any([hp, ap, hs, ash, hst, ast]):
            conn.execute("""
                INSERT OR REPLACE INTO match_tech_stats
                    (home_team, away_team, date, home_possession, away_possession,
                     home_shots, away_shots, home_shots_on_target, away_shots_on_target)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (home, away, date, hp, ap, hs, ash, hst, ast))
            count += 1

    conn.commit()
    print(f'  从 match_analysis 导入: {count} 场')
    return count

def fetch_and_store_batch(conn, matches, max_workers=15):
    """并发抓取技术统计"""
    def fetch_one(match):
        home, away, date, sid = match
        tech = fetch_tech_stats_by_sid(sid)
        if tech:
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

    success = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(fetch_one, m): m for m in matches}
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

    conn.commit()
    return success, fail

def main():
    import argparse
    parser = argparse.ArgumentParser(description='补抓技术统计到DB')
    parser.add_argument('--limit', type=int, default=1000,
                       help='补抓最近的 N 场 (default: 1000)')
    parser.add_argument('--workers', type=int, default=5,
                       help='并发爬取线程数 (default: 5)')
    parser.add_argument('--skip-fetch', action='store_true',
                       help='跳过爬取，仅从已有数据源导入')
    parser.add_argument('--force-fetch', action='store_true',
                       help='强制爬取所有匹配（即使已有数据）')
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f'错误: DB 不存在 {DB_PATH}')
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    # 1. 从已有数据源导入
    print('=== 第一步：从已有数据源导入 ===')
    from_results = backfill_from_results(conn)
    from_ma = backfill_from_match_analysis(conn)
    print(f'  已有数据合计: {from_results + from_ma} 场\n')

    if args.skip_fetch:
        print('跳过爬取。完成。')
        conn.close()
        return

    # 2. 获取需要爬取的 poisson_predictions match
    print('=== 第二步：计算需爬取的匹配 ===')
    if args.force_fetch:
        # 全部重爬
        rows = conn.execute("""
            SELECT DISTINCT p.home_team, p.away_team, p.date, p.match_id
            FROM poisson_predictions p
            WHERE p.home_team IS NOT NULL AND p.match_id IS NOT NULL AND p.match_id != ''
            ORDER BY p.date DESC LIMIT ?
        """, (args.limit,)).fetchall()
        to_fetch = [(h, a, d, int(mid)) for h, a, d, mid in rows]
        print(f'  需爬取: {len(to_fetch)} 场 (全部重刷)')
    else:
        # 只爬还没有数据的
        rows = conn.execute("""
            SELECT DISTINCT p.home_team, p.away_team, p.date, p.match_id
            FROM poisson_predictions p
            WHERE p.home_team IS NOT NULL AND p.match_id IS NOT NULL AND p.match_id != ''
            ORDER BY p.date DESC LIMIT ?
        """, (args.limit,)).fetchall()

        to_fetch = []
        already = 0
        for home, away, date, mid in rows:
            existing = conn.execute("""
                SELECT 1 FROM match_tech_stats
                WHERE home_team=? AND away_team=? AND date=?
            """, (home, away, date)).fetchone()
            if existing:
                already += 1
                continue
            to_fetch.append((home, away, date, int(mid)))

        print(f'  已有数据: {already} 场, 需爬取: {len(to_fetch)} 场')

    if not to_fetch:
        print('  无需爬取。完成。')
        conn.close()
        return

    # 3. 并发爬取
    print(f'\n=== 第三步：并发爬取 ({args.workers} 线程) ===')
    t0 = time.time()
    success, fail = fetch_and_store_batch(conn, to_fetch, max_workers=args.workers)
    elapsed = time.time() - t0
    print(f'  成功: {success} 场, 失败: {fail} 场, 耗时: {elapsed:.1f}s')
    print(f'  平均: {elapsed/max(len(to_fetch),1):.2f}s/场')

    # 4. 统计
    total_in_db = conn.execute('SELECT COUNT(*) FROM match_tech_stats').fetchone()[0]
    print(f'\n=== 完成 ===')
    print(f'match_tech_stats 总量: {total_in_db} 场')

    conn.close()

if __name__ == '__main__':
    main()
