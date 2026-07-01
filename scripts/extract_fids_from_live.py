#!/usr/bin/env python3
"""
从 live.500.com/2h1.php 页面提取 fid，匹配到 DB 中的比赛。
适用于赛前/进行中比赛（未在完场页出现的比赛）。

用法: python scripts/extract_fids_from_live.py --db data/football.db [--date 2026-07-02]
"""
import argparse
import re
import sqlite3
import urllib.request
import sys

# 已知队名对应表（页面名 → DB名），因为页面和DB的队名可能有细微差异
TEAM_ALIASES = {
    '民主刚果': '刚果(金)',
    '刚果民主共和国': '刚果(金)',
}

def normalize(name):
    """标准化队名用于匹配"""
    return name.replace(' ', '').replace('&amp;', '&')

def fetch_page(url='https://live.500.com/2h1.php'):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    text = raw.decode('gbk')
    return text, raw

def extract_matches(text, raw):
    """从页面提取所有比赛 (fid, league, home, away)"""
    matches = []
    # gy 属性在 fid 之前，用从fid到gy的灵活搜索
    rows = re.findall(rb"fid=\"(\d+)\".*?gy=\"([^\"]*)\"", raw, re.DOTALL)
    for fid_bytes, gy_bytes in rows:
        fid = fid_bytes.decode()
        gy = gy_bytes.decode('gbk')
        parts = gy.split(',')
        if len(parts) >= 3:
            league = parts[0].strip()
            home = parts[1].strip()
            away = parts[2].strip()
            matches.append((fid, league, home, away))
    return matches

def load_db_matches(db_path, date):
    """从DB加载指定日期的比赛"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, home_team, away_team, kickoff_time, fid_500 "
        "FROM poisson_predictions WHERE date=? AND (fid_500 IS NULL OR fid_500='')",
        (date,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def update_fid(db_path, match_id, fid):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (fid, match_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected

def main():
    parser = argparse.ArgumentParser(description='从2h1.php页面提取fid更新DB')
    parser.add_argument('--db', default='data/football.db', help='数据库路径')
    parser.add_argument('--date', default=None, help='目标日期，默认按页面当天')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()

    print("获取 2h1.php 页面...")
    text, raw = fetch_page()
    page_matches = extract_matches(text, raw)
    print(f"页面共有 {len(page_matches)} 场比赛")

    date_filter = args.date

    if date_filter:
        db_rows = load_db_matches(args.db, date_filter)
    else:
        conn = sqlite3.connect(args.db)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, home_team, away_team, kickoff_time, fid_500 "
            "FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500=''"
        )
        db_rows = cur.fetchall()
        conn.close()

    print(f"DB中缺fid的比赛: {len(db_rows)}场")

    matched = 0
    for match_id, db_home, db_away, kickoff, fid_500 in db_rows:
        db_home_norm = normalize(db_home)
        db_away_norm = normalize(db_away)

        best_match = None
        for fid, league, page_home, page_away in page_matches:
            page_home_norm = normalize(page_home)
            page_away_norm = normalize(page_away)

            page_home_aliased = TEAM_ALIASES.get(page_home_norm, page_home_norm)
            page_away_aliased = TEAM_ALIASES.get(page_away_norm, page_away_norm)

            # 精确匹配或别名匹配
            if (page_home_norm == db_home_norm and page_away_norm == db_away_norm) or \
               (page_home_aliased == db_home_norm and page_away_aliased == db_away_norm) or \
               (page_home_norm == db_away_norm and page_away_norm == db_home_norm):
                best_match = (fid, league, page_home, page_away)
                break

            # 模糊匹配：队名包含关系
            if (db_home_norm in page_home_norm or page_home_norm in db_home_norm) and \
               (db_away_norm in page_away_norm or page_away_norm in db_away_norm):
                best_match = (fid, league, page_home, page_away)
                break

            if (db_home_norm in page_away_norm or page_away_norm in db_home_norm) and \
               (db_away_norm in page_home_norm or page_home_norm in db_away_norm):
                best_match = (fid, league, page_home, page_away)
                break

        if best_match:
            fid, league, ph, pa = best_match
            update_fid(args.db, match_id, fid)
            matched += 1
            if args.verbose:
                print(f"  ✅ ID={match_id}: {db_home} vs {db_away} -> fid={fid} ({league}: {ph} vs {pa})")
        else:
            if args.verbose:
                print(f"  ❌ ID={match_id}: {db_home} vs {db_away} -> 页面未匹配")

    print(f"\n完成! 更新了 {matched}/{len(db_rows)} 场比赛的fid_500")

if __name__ == '__main__':
    main()
