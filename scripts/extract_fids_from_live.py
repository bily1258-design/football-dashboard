#!/usr/bin/env python3
"""
extract_fids_from_live.py v6 — 从500.com页面提取fid，按联赛过滤，匹配DB比赛

v6 改用精简数据源：竞彩页(?e=date) + 北单页(zqdc.php)
  替换旧的 wanchang.php + 首页 + weekfixture.php 三个源

数据源:
  1. live.500.com/?e={date}  — 竞彩当日及未来2天赛事（精炼、不杂）
  2. live.500.com/zqdc.php   — 北单当期赛事（芬甲、挪甲、瑞典超等）

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
import time
from datetime import datetime, timedelta

# 不再按联赛白名单过滤，固定日期页面直接取全部比赛

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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://live.500.com/',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def extract_fid_rows(raw, source):
    """提取 <tr id=\"aXXXXX\" ...> 中的 fid/联赛/队名/开赛时间

    结构示例:
      <tr id=\"a1427672\" gy=\"欧冠,克拉克斯,比森阿泰尔\" yy=\"欧冠,拉克斯域克,阿特比森\" ...>
        <td>...</td><td>联赛</td><td>轮次</td><td align=\"center\">07-08 02:45</td>...
    """
    matches = []
    # 两段式：先找所有 tr 块（不含捕获组，findall 返回完整匹配文本），再逐块解析
    tr_pattern = rb'<tr[^>]*id="a\d+"[^>]*>.*?</tr>'
    tr_blocks = re.findall(tr_pattern, raw, re.DOTALL)
    for tr_text in tr_blocks:

        # fid = tr id 中的数字
        fid_m = re.search(rb'id="a(\d+)"', tr_text)
        if not fid_m:
            continue
        fid = fid_m.group(1).decode()

        # gy = 联赛,主队,客队（中文简称）
        gy_m = re.search(rb'gy="([^"]*)"', tr_text)
        if not gy_m:
            continue
        gy = gy_m.group(1).decode('gbk', errors='replace')
        parts = gy.split(',')
        if len(parts) < 3:
            continue

        # yy = 备选队名（500.com原始/音译名）
        yy = ''
        yy_m = re.search(rb'yy="([^"]*)"', tr_text)
        if yy_m:
            yy = yy_m.group(1).decode('gbk', errors='replace')

        # 时间：第4个 <td align="center">MM-DD HH:MM</td>
        time_val = ''
        time_m = re.search(
            rb'<td[^>]*align="center"[^>]*>(\d{2}-\d{2}\s+\d{2}:\d{2})</td>',
            tr_text
        )
        if time_m:
            time_val = time_m.group(1).decode()

        matches.append({
            'fid': fid,
            'league': parts[0].strip(),
            'home': parts[1].strip(),
            'away': parts[2].strip(),
            'yy_home': yy.split(',')[1].strip() if yy and len(yy.split(',')) > 1 else '',
            'yy_away': yy.split(',')[2].strip() if yy and len(yy.split(',')) > 2 else '',
            'time': time_val,
            'source': source
        })
    return matches


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
    today = datetime.now().strftime('%Y-%m-%d')

    # 1. 竞彩页 ?e={date} — 只抓今明2天（竞彩页面有限流，不宜多抓）
    if args.date:
        # --date 模式：只抓指定日期
        dates_to_fetch = [args.date]
    else:
        # 默认抓今天 + 明天
        base = datetime.now()
        dates_to_fetch = [base.strftime('%Y-%m-%d'),
                          (base + timedelta(days=1)).strftime('%Y-%m-%d')]

    for date_str in dates_to_fetch:
        url = f'https://live.500.com/?e={date_str}'
        try:
            raw = fetch_page_raw(url)
            rows = extract_fid_rows(raw, 'jingcai')
            for r in rows:
                r['date'] = date_str
            print(f"  ?e={date_str}: {len(rows)}场")
            all_matches.extend(rows)
        except Exception as e:
            print(f"  ⚠ ?e={date_str}: {e}")
        # 防限流延时
        time.sleep(1.5)

    # 2. 北单页 zqdc.php — 当期北单赛事
    print("获取 zqdc.php (北单当期)...")
    try:
        raw_bd = fetch_page_raw('https://live.500.com/zqdc.php')
        rows_bd = extract_fid_rows(raw_bd, 'beidan')
        print(f"  北单: {len(rows_bd)}场")
        all_matches.extend(rows_bd)
    except Exception as e:
        print(f"  ❌ zqdc.php 失败: {e}")

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
        # 保存 jingcai 和 beidan 来源中未匹配且是未来日期的比赛
        now = datetime.now().strftime('%Y-%m-%d')
        unmatched = [m for m in unique_matches
                     if m['fid'] not in matched_fids
                     and m.get('date', now) >= now]
        print(f"\n=== 保存未来比赛fid ===")
        print(f"过滤后未匹配的未来比赛: {len(unmatched)}场")

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
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (m['fid'], m['league'], m['home'], m['away'], date or '', kickoff_time, m['source']))

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
