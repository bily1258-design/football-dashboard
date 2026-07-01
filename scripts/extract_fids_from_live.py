#!/usr/bin/env python3
"""
从 live.500.com/2h1.php 页面提取 fid，匹配到 DB 中的比赛。
适用于赛前/进行中比赛（未在完场页出现的比赛）。

用法: python scripts/extract_fids_from_live.py --db data/football.db [--date 2026-07-02] [-v] [--dry-run]
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
    '曼联': '曼彻斯特联',
    '红魔': '曼彻斯特联',
    '热刺': '托特纳姆热刺',
    '阿森纳': '阿仙奴',
    '兵工厂': '阿仙奴',
    '巴黎': '巴黎圣日耳曼',
    'PSG': '巴黎圣日耳曼',
    '拜仁': '拜仁慕尼黑',
    '多特': '多特蒙德',
    '药厂': '勒沃库森',
    '狼堡': '沃尔夫斯堡',
    '门兴': '门兴格拉德巴赫',
    '弗赖堡': '弗莱堡',
    '圣保利': '汉堡圣保利',
    '霍芬海姆': '贺芬咸',
    '法兰克福': '法兰克福',
    '斯图加特': '斯图加特',
    '沙尔克04': '史浩克零四',
    '科隆': '科隆',
    '皇马': '皇家马德里',
    '巴萨': '巴塞罗那',
    '马竞': '马德里竞技',
    '塞维利亚': '西维尔',
    '比利亚雷亚尔': '维拉利尔',
    '瓦伦西亚': '巴伦西亚',
    '塞尔塔': '切爾達',
    '国米': '国际米兰',
    '尤文': '尤文图斯',
    '那不勒斯': '拿玻里',
    '罗马': '罗马',
    '拉齐奥': '拉素',
    '佛罗伦萨': '费伦天拿',
    'AC米兰': 'AC米兰',
    '都灵': '拖连奴',
    '卡利亚里': '卡利亚里',
    '乌迪内斯': '乌甸尼斯',
    '本菲卡': '宾菲加',
    '波尔图': '波图',
    '体育CP': '里斯本竞技',
    '阿贾克斯': '阿积士',
    '埃因霍温': 'PSV燕豪芬',
    '费耶诺德': '飞燕诺',
    '凯尔特人': '些路迪',
    '流浪者': '格拉斯哥流浪',
    '萨尔茨堡': '萨尔斯堡',
}

def normalize(name):
    """标准化队名用于匹配"""
    return name.replace(' ', '').replace('&amp;', '&').replace('（', '(').replace('）', ')')

def is_fuzzy_safe(short_name, long_name):
    """模糊匹配安全检查：短名至少3字符，且长度比>=0.5，避免短名误匹配"""
    if len(short_name) < 3:
        return False
    if len(long_name) == 0:
        return False
    ratio = len(short_name) / len(long_name)
    return ratio >= 0.5

def fuzzy_match(name_a, name_b):
    """安全模糊匹配：包含关系 + 长度比安全检查"""
    if name_a == name_b:
        return True
    if name_a in name_b:
        return is_fuzzy_safe(name_a, name_b)
    if name_b in name_a:
        return is_fuzzy_safe(name_b, name_a)
    return False

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

def match_teams(db_home_norm, db_away_norm, page_home_norm, page_away_norm):
    """
    匹配两队名，返回 'exact'/'alias'/'fuzzy'/'swap_exact'/'swap_alias'/'swap_fuzzy' 或 None
    """
    # 1. 精确匹配（同序）
    if page_home_norm == db_home_norm and page_away_norm == db_away_norm:
        return 'exact'
    # 2. 精确匹配（主客互换）
    if page_home_norm == db_away_norm and page_away_norm == db_home_norm:
        return 'swap_exact'
    # 3. 别名匹配（同序）
    ph_aliased = TEAM_ALIASES.get(page_home_norm, page_home_norm)
    pa_aliased = TEAM_ALIASES.get(page_away_norm, page_away_norm)
    if ph_aliased == db_home_norm and pa_aliased == db_away_norm:
        return 'alias'
    # 4. 别名匹配（主客互换）
    if ph_aliased == db_away_norm and pa_aliased == db_home_norm:
        return 'swap_alias'
    # 5. 模糊匹配（同序）- 带安全检查
    if fuzzy_match(db_home_norm, page_home_norm) and fuzzy_match(db_away_norm, page_away_norm):
        return 'fuzzy'
    # 6. 模糊匹配（主客互换）
    if fuzzy_match(db_home_norm, page_away_norm) and fuzzy_match(db_away_norm, page_home_norm):
        return 'swap_fuzzy'
    return None

def main():
    parser = argparse.ArgumentParser(description='从2h1.php页面提取fid更新DB')
    parser.add_argument('--db', default='data/football.db', help='数据库路径')
    parser.add_argument('--date', default=None, help='目标日期，默认按页面当天')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--dry-run', action='store_true', help='只匹配不写入DB')
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

    stats = {'exact': 0, 'alias': 0, 'fuzzy': 0, 'swap_exact': 0, 'swap_alias': 0, 'swap_fuzzy': 0, 'miss': 0}
    matched = 0

    for match_id, db_home, db_away, kickoff, fid_500 in db_rows:
        db_home_norm = normalize(db_home)
        db_away_norm = normalize(db_away)

        best_match = None
        match_type = None
        for fid, league, page_home, page_away in page_matches:
            page_home_norm = normalize(page_home)
            page_away_norm = normalize(page_away)

            result = match_teams(db_home_norm, db_away_norm, page_home_norm, page_away_norm)
            if result:
                best_match = (fid, league, page_home, page_away)
                match_type = result
                break

        if best_match:
            fid, league, ph, pa = best_match
            if not args.dry_run:
                update_fid(args.db, match_id, fid)
            matched += 1
            stats[match_type] += 1
            if args.verbose:
                tag = match_type.replace('swap_', '⇄') if match_type.startswith('swap') else match_type
                print(f"  ✅ [{tag}] ID={match_id}: {db_home} vs {db_away} -> fid={fid} ({league}: {ph} vs {pa})")
        else:
            stats['miss'] += 1
            if args.verbose:
                print(f"  ❌ ID={match_id}: {db_home} vs {db_away} -> 页面未匹配")

    print(f"\n完成! 匹配 {matched}/{len(db_rows)} 场")
    print(f"  精确: {stats['exact']}  别名: {stats['alias']}  模糊: {stats['fuzzy']}")
    print(f"  互换精确: {stats['swap_exact']}  互换别名: {stats['swap_alias']}  互换模糊: {stats['swap_fuzzy']}")
    print(f"  未匹配: {stats['miss']}")
    if args.dry_run:
        print("  (dry-run模式，未写入DB)")

if __name__ == '__main__':
    main()
