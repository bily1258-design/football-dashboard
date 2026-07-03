#!/usr/bin/env python3
"""
extract_fids_from_live.py v5 — 从500.com页面提取fid，按联赛过滤，匹配DB比赛

v5 新增:
  - LEAGUE_WHITELIST: 只保留竞彩/北单常见联赛（过滤友谊赛/低级别）
  - --save-future: 将过滤后的未来比赛fid直接插入poisson_predictions

数据源:
  1. wanchang.php     — 完场页（最近所有已结束赛事）
  2. live.500.com/    — 首页（当前/即将开赛赛事）
  3. weekfixture.php  — 未来2天赛事（未开赛+进行中）

用法:
  python scripts/extract_fids_from_live.py --db data/football.db [-v] [--dry-run]
  python scripts/extract_fids_from_live.py --db data/football.db --date 2026-07-03
  python scripts/extract_fids_from_live.py --db data/football.db --save-future [--dry-run]
"""

import argparse
import re
import sqlite3
import urllib.request
import sys
from datetime import datetime, timedelta

# ===== 竞彩/北单常见联赛白名单 =====
# 只保留用户确认的约66场，其余过滤掉
LEAGUE_WHITELIST = {
    # 国内
    '中超', '中甲',
    # 韩国
    'K1联赛', 'K2联赛',
    # 北欧（北单覆盖）
    '芬超', '芬甲', '冰岛超', '瑞典超', '挪甲', '爱甲',
    # 美洲
    '美冠', '巴乙', '厄甲',
    # 国家队赛事
    '世界杯',
}

# ===== 队名别名映射（页面名 → DB名） =====
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
    """通用提取: <tr id=\"aXXXXX\" gy=\"league,home,away\"> → fid列表

    wanchang.php 和 weekfixture.php 结构一致，都用 id=\"aXXX\" + gy=\"...\"
    """
    matches = []
    pattern = rb'id=\"a(\d+)\"[^>]*gy=\"([^\"]*)\"'
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


def extract_weekfixture_with_date(raw):
    """从 weekfixture.php 提取 fid + 联赛时间信息

    返回: [{'fid', 'league', 'home', 'away', 'source', 'date', 'time'}, ...]
    """
    text = raw.decode('gbk', errors='replace')

    # 先找日期标题行: "2026年07月04日 (星期六)"
    date_headers = list(re.finditer(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text))

    if not date_headers:
        # 回退到基础提取
        return extract_fid_rows(raw, 'weekfixture')

    matches = []
    # 对每个 fid 行，找到它前面的最近日期
    pattern = rb'id=\"a(\d+)\"[^>]*gy=\"([^\"]*)\"'
    rows = re.findall(pattern, raw)

    for aid_bytes, gy_bytes in rows:
        fid = aid_bytes.decode()
        gy = gy_bytes.decode('gbk', errors='replace')
        parts = gy.split(',')
        if len(parts) < 3:
            continue

        league = parts[0].strip()
        home = parts[1].strip()
        away = parts[2].strip()

        # 在HTML中找到这行的位置，往前找最近的日期
        row_html = f'id="a{fid}"'
        pos = text.find(row_html)
        match_date = None
        match_time = ''

        if pos >= 0:
            # 往前找最近的时间戳 <td align="center">07-04 10:00</td>
            before = text[max(0, pos - 500):pos]
            time_match = re.search(r'<td[^>]*>\s*(\d{1,2}-\d{1,2})\s+(\d{2}:\d{2})\s*</td>', before)
            if time_match:
                mm_dd = time_match.group(1)
                match_time = time_match.group(2)
            else:
                # 再从行内找
                after = text[pos:pos + 600]
                time_match = re.search(r'<td[^>]*>\s*(\d{1,2}-\d{1,2})\s+(\d{2}:\d{2})\s*</td>', after)
                if time_match:
                    mm_dd = time_match.group(1)
                    match_time = time_match.group(2)

            # 找最近的日期标题
            for i in range(len(date_headers) - 1, -1, -1):
                dh = date_headers[i]
                if dh.start() < pos:
                    year = dh.group(1)
                    month = dh.group(2).zfill(2)
                    day = dh.group(3).zfill(2)
                    match_date = f'{year}-{month}-{day}'
                    break

        if not match_date:
            # 保底：今天
            match_date = datetime.now().strftime('%Y-%m-%d')

        matches.append({
            'fid': fid,
            'league': league,
            'home': home,
            'away': away,
            'source': 'weekfixture',
            'date': match_date,
            'time': match_time,
        })

    return matches


def filter_by_league(matches, whitelist, verbose=False):
    """按联赛白名单过滤"""
    kept = []
    filtered = 0
    for m in matches:
        if m['league'] in whitelist:
            kept.append(m)
        else:
            filtered += 1
            if verbose:
                print(f"  🔇 过滤掉 [{m['league']}] {m['home']} vs {m['away']} (fid={m['fid']})")
    if verbose:
        print(f"联赛过滤: 保留 {len(kept)} 场, 过滤 {filtered} 场")
    return kept


def main():
    parser = argparse.ArgumentParser(description='从500.com页面提取fid更新DB v5')
    parser.add_argument('--db', default='data/football.db', help='数据库路径')
    parser.add_argument('--date', default=None, help='目标日期，默认所有缺fid')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--dry-run', action='store_true', help='只匹配不写入DB')
    parser.add_argument('--save-future', action='store_true',
                        help='将过滤后的未来比赛fid插入poisson_predictions')
    args = parser.parse_args()

    all_matches = []

    # 1. wanchang.php — 最近所有完场
    print("获取 wanchang.php (最近完场)...")
    try:
        raw = fetch_page_raw('https://live.500.com/wanchang.php')
        rows = extract_fid_rows(raw, 'wanchang')
        print(f"  完场: {len(rows)}场")
        all_matches.extend(rows)
    except Exception as e:
        print(f"  ❌ wanchang.php 失败: {e}")

    # 2. live.500.com 首页
    print("获取 live.500.com 首页...")
    try:
        raw_home = fetch_page_raw('https://live.500.com/')
        matches_home = extract_fid_rows(raw_home, 'homepage')
        print(f"  首页: {len(matches_home)}场")
        all_matches.extend(matches_home)
    except Exception as e:
        print(f"  ❌ 首页 失败: {e}")

    # 3. weekfixture.php — 未来2天赛事（带日期）
    print("获取 weekfixture.php (未来2天赛事)...")
    try:
        raw_week = fetch_page_raw('https://live.500.com/weekfixture.php')
        matches_week = extract_weekfixture_with_date(raw_week)
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

    # === 联赛过滤 ===
    original_count = len(unique_matches)
    unique_matches = filter_by_league(unique_matches, LEAGUE_WHITELIST, verbose=args.verbose)
    print(f"联赛过滤: {original_count} -> {len(unique_matches)}场 (去掉 {original_count - len(unique_matches)} 场)")

    if len(unique_matches) == 0:
        print("过滤后无比赛，退出")
        return

    # 连接DB
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # === 第一步：匹配已有DB记录并更新fid ===
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

    stats = {'exact': 0, 'alias': 0, 'fuzzy': 0,
             'swap_exact': 0, 'swap_alias': 0, 'swap_fuzzy': 0, 'miss': 0}
    fid_matched = 0
    matched_fids = set()

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
            matched_fids.add(fid)
            stats[match_type] += 1
            if args.verbose:
                tag = match_type.replace('swap_', '⇄') if match_type.startswith('swap') else match_type
                print(f"  ✅ [{tag}] ID={match_id}: {db_home} vs {db_away} -> fid={fid} ({best_match['league']}: {best_match['home']} vs {best_match['away']}) [{best_match['source']}]")
        else:
            stats['miss'] += 1
            if args.verbose:
                print(f"  ❌ ID={match_id}: {db_home} vs {db_away} -> 页面未匹配")

    if not args.dry_run and fid_matched > 0:
        conn.commit()

    print(f"\n=== fid补全（匹配DB已有记录）===")
    print(f"匹配 {fid_matched}/{len(db_rows)} 场")
    print(f"  精确: {stats['exact']}  别名: {stats['alias']}  模糊: {stats['fuzzy']}")
    print(f"  互换精确: {stats['swap_exact']}  互换别名: {stats['swap_alias']}  互换模糊: {stats['swap_fuzzy']}")
    print(f"  未匹配: {stats['miss']}")

    # === 第二步：保存未匹配的未来比赛fid ===
    if args.save_future:
        # 只保存 weekfixture 来源的比赛（有完整日期信息）
        unmatched = [m for m in unique_matches if m['fid'] not in matched_fids and m['source'] == 'weekfixture']
        print(f"\n=== 保存未来比赛fid ===")
        print(f"过滤后未匹配的fid: {len(unmatched)}场")

        saved = 0
        for m in unmatched:
            date = m.get('date', '')
            kickoff = m.get('time', '')

            # 构造完整kickoff_time
            if date and kickoff:
                kickoff_time = f"{date} {kickoff}:00"
            else:
                kickoff_time = ""

            # 检查是否已存在相同fid的记录
            cur.execute("SELECT id FROM poisson_predictions WHERE fid_500=?", (m['fid'],))
            if cur.fetchone():
                if args.verbose:
                    print(f"  ⏭ fid={m['fid']} 已存在，跳过")
                continue

            # 检查是否已存在相同球队+日期的记录
            if date:
                cur.execute(
                    "SELECT id FROM poisson_predictions WHERE date=? AND home_team=? AND away_team=?",
                    (date, m['home'], m['away'])
                )
                existing = cur.fetchone()
                if existing:
                    if not args.dry_run:
                        cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (m['fid'], existing[0]))
                    saved += 1
                    if args.verbose:
                        print(f"  ✅ 更新fid: ID={existing[0]} {m['home']} vs {m['away']} -> fid={m['fid']}")
                    continue

            # 插入新记录
            if not args.dry_run:
                cur.execute("""
                    INSERT INTO poisson_predictions
                        (fid_500, league, home_team, away_team, date, kickoff_time, source)
                    VALUES (?, ?, ?, ?, ?, ?, 'future_500')
                """, (m['fid'], m['league'], m['home'], m['away'], date or '', kickoff_time))

            saved += 1
            if args.verbose:
                print(f"  ✅ 新建: [{m['league']}] {m['home']} vs {m['away']} fid={m['fid']} @ {date} {kickoff}")

        if not args.dry_run:
            conn.commit()
        print(f"保存未来比赛fid: {saved}场")
        if args.dry_run:
            print("(dry-run模式，未写入DB)")

    conn.close()


if __name__ == '__main__':
    main()
