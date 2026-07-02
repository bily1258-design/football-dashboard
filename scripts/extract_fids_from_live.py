#!/usr/bin/env python3
"""
extract_fids_from_live.py v4 — 从500.com页面提取fid，匹配DB比赛

数据源:
  1. wanchang.php     — 完场页（最近所有已结束赛事，fid最全最稳）
  2. live.500.com/    — 首页（当前/即将开赛赛事，与 weekfixture 互补）
  3. weekfixture.php  — 未来2天赛事（未开赛+进行中）

v4 改进:
  - 去掉2h1.php（比赛结束即消失，不持久）
  - 新增wanchang.php（完场页，已结束比赛fid长期保留）
  - 赛果回填仍由review.py + fetch_results_cache.py负责

用法: python scripts/extract_fids_from_live.py --db data/football.db [--date 2026-07-02] [-v] [--dry-run]
"""
import argparse
import re
import sqlite3
import urllib.request
import sys

# 已知队名对应表（页面名 → DB名）
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
    return name.replace(' ', '').replace('&amp;', '&').replace('（', '(').replace('）', ')')


def is_fuzzy_safe(short_name, long_name):
    if len(short_name) < 3:
        return False
    if len(long_name) == 0:
        return False
    return len(short_name) / len(long_name) >= 0.5


def fuzzy_match(name_a, name_b):
    if name_a == name_b:
        return True
    if name_a in name_b:
        return is_fuzzy_safe(name_a, name_b)
    if name_b in name_a:
        return is_fuzzy_safe(name_b, name_a)
    return False


def match_teams(db_home_norm, db_away_norm, page_home_norm, page_away_norm):
    if page_home_norm == db_home_norm and page_away_norm == db_away_norm:
        return 'exact'
    if page_home_norm == db_away_norm and page_away_norm == db_home_norm:
        return 'swap_exact'
    ph_aliased = TEAM_ALIASES.get(page_home_norm, page_home_norm)
    pa_aliased = TEAM_ALIASES.get(page_away_norm, page_away_norm)
    if ph_aliased == db_home_norm and pa_aliased == db_away_norm:
        return 'alias'
    if ph_aliased == db_away_norm and pa_aliased == db_home_norm:
        return 'swap_alias'
    if fuzzy_match(db_home_norm, page_home_norm) and fuzzy_match(db_away_norm, page_away_norm):
        return 'fuzzy'
    if fuzzy_match(db_home_norm, page_away_norm) and fuzzy_match(db_away_norm, page_home_norm):
        return 'swap_fuzzy'
    return None


def fetch_page_raw(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def extract_fid_rows(raw, source):
    """通用提取: <tr id="aXXXXX" gy="league,home,away"> → fid列表

    wanchang.php 和 weekfixture.php 结构一致，都用 id="aXXX" + gy="..."
    """
    matches = []
    pattern = rb'id="a(\d+)"[^>]*gy="([^"]*)"'
    rows = re.findall(pattern, raw)
    for aid_bytes, gy_bytes in rows:
        fid = aid_bytes.decode()
        gy = gy_bytes.decode('gbk', errors='replace')
        parts = gy.split(',')
        if len(parts) < 3:
            continue
        matches.append({
            'fid': fid, 'league': parts[0].strip(),
            'home': parts[1].strip(), 'away': parts[2].strip(),
            'source': source
        })
    return matches


def main():
    parser = argparse.ArgumentParser(description='从500.com页面提取fid更新DB v4')
    parser.add_argument('--db', default='data/football.db', help='数据库路径')
    parser.add_argument('--date', default=None, help='目标日期，默认所有缺fid')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--dry-run', action='store_true', help='只匹配不写入DB')
    args = parser.parse_args()

    all_matches = []

    # 1. wanchang.php — 最近所有完场（含今日+昨日+更早，一次请求覆盖）
    print("获取 wanchang.php (最近完场)...")
    try:
        raw = fetch_page_raw('https://live.500.com/wanchang.php')
        rows = extract_fid_rows(raw, 'wanchang')
        print(f"  完场: {len(rows)}场")
        all_matches.extend(rows)
    except Exception as e:
        print(f"  ❌ wanchang.php 失败: {e}")

    # 2. live.500.com 首页 — 当前/即将开赛赛事（与 weekfixture 互补）
    print("获取 live.500.com 首页...")
    try:
        raw_home = fetch_page_raw('https://live.500.com/')
        matches_home = extract_fid_rows(raw_home, 'homepage')
        print(f"  首页: {len(matches_home)}场")
        all_matches.extend(matches_home)
    except Exception as e:
        print(f"  ❌ 首页 失败: {e}")

    # 3. weekfixture.php — 未来2天赛事
    print("获取 weekfixture.php (未来2天赛事)...")
    try:
        raw_week = fetch_page_raw('https://live.500.com/weekfixture.php')
        matches_week = extract_fid_rows(raw_week, 'weekfixture')
        print(f"  未来2天: {len(matches_week)}场")
        all_matches.extend(matches_week)
    except Exception as e:
        print(f"  ❌ weekfixture.php 失败: {e}")

    print(f"合计: {len(all_matches)}场比赛")

    # 去重（按fid）
    seen_fids = set()
    unique_matches = []
    for m in all_matches:
        if m['fid'] not in seen_fids:
            seen_fids.add(m['fid'])
            unique_matches.append(m)
    print(f"去重后: {len(unique_matches)}场")

    # 连接DB
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # 查缺fid的比赛
    if args.date:
        cur.execute(
            "SELECT id, home_team, away_team, kickoff_time, fid_500 "
            "FROM poisson_predictions WHERE date=? AND (fid_500 IS NULL OR fid_500='' OR fid_500=0)",
            (args.date,)
        )
    else:
        cur.execute(
            "SELECT id, home_team, away_team, kickoff_time, fid_500 "
            "FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0"
        )
    db_rows = cur.fetchall()
    print(f"DB中缺fid的比赛: {len(db_rows)}场")

    # 匹配fid
    stats = {'exact': 0, 'alias': 0, 'fuzzy': 0, 'swap_exact': 0, 'swap_alias': 0, 'swap_fuzzy': 0, 'miss': 0}
    fid_matched = 0

    for match_id, db_home, db_away, kickoff, fid_500 in db_rows:
        db_home_norm = normalize(db_home)
        db_away_norm = normalize(db_away)

        best_match = None
        match_type = None
        for pm in unique_matches:
            page_home_norm = normalize(pm['home'])
            page_away_norm = normalize(pm['away'])
            result = match_teams(db_home_norm, db_away_norm, page_home_norm, page_away_norm)
            if result:
                best_match = pm
                match_type = result
                break

        if best_match:
            fid = best_match['fid']
            if not args.dry_run:
                cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (fid, match_id))
            fid_matched += 1
            stats[match_type] += 1
            if args.verbose:
                tag = match_type.replace('swap_', '⇄') if match_type.startswith('swap') else match_type
                print(f"  ✅ [{tag}] ID={match_id}: {db_home} vs {db_away} -> fid={fid} ({best_match['league']}: {best_match['home']} vs {best_match['away']}) [{best_match['source']}]")
        else:
            stats['miss'] += 1
            if args.verbose:
                print(f"  ❌ ID={match_id}: {db_home} vs {db_away} -> 页面未匹配")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\n=== fid补全 ===")
    print(f"匹配 {fid_matched}/{len(db_rows)} 场")
    print(f"  精确: {stats['exact']}  别名: {stats['alias']}  模糊: {stats['fuzzy']}")
    print(f"  互换精确: {stats['swap_exact']}  互换别名: {stats['swap_alias']}  互换模糊: {stats['swap_fuzzy']}")
    print(f"  未匹配: {stats['miss']}")
    if args.dry_run:
        print("(dry-run模式，未写入DB)")


if __name__ == '__main__':
    main()
