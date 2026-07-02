#!/usr/bin/env python3
"""
extract_fids_from_live.py v3 — 从500.com页面提取fid+赛果，匹配DB比赛

数据源:
  1. 2h1.php         — 当天赛事（含完场比分+即时+未开赛）
  2. weekfixture.php — 未来2天赛事（未开赛）

v3 改进:
  - 同时抓取2h1.php和weekfixture.php，覆盖当天+未来2天
  - 提取完场比分(status=4)写入actual_outcome
  - weekfixture.php用id="aXXX"提取fid（无fid属性）
  - 比分提取: class="red">N - N<

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
    """标准化队名用于匹配"""
    return name.replace(' ', '').replace('&amp;', '&').replace('（', '(').replace('）', ')')


def is_fuzzy_safe(short_name, long_name):
    """模糊匹配安全检查"""
    if len(short_name) < 3:
        return False
    if len(long_name) == 0:
        return False
    return len(short_name) / len(long_name) >= 0.5


def fuzzy_match(name_a, name_b):
    """安全模糊匹配"""
    if name_a == name_b:
        return True
    if name_a in name_b:
        return is_fuzzy_safe(name_a, name_b)
    if name_b in name_a:
        return is_fuzzy_safe(name_b, name_a)
    return False


def match_teams(db_home_norm, db_away_norm, page_home_norm, page_away_norm):
    """匹配两队名，返回匹配类型或None"""
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
    """获取页面原始字节"""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def extract_from_2h1(raw):
    """从2h1.php提取比赛 (fid, league, home, away, status, home_score, away_score)
    
    结构: <tr id="aXXXXX" status="N" gy="league,home,away" ... fid="XXXXX">
    status=0 未开赛, status=4 完场
    完场比分: <td align="center" class="red">0 - 1</td>
    """
    matches = []
    pattern = rb'id="a(\d+)"\s+status="([^"]*)"\s+gy="([^"]*)"'
    rows = re.findall(pattern, raw)

    for aid_bytes, status_bytes, gy_bytes in rows:
        fid = aid_bytes.decode()
        status = status_bytes.decode()
        gy = gy_bytes.decode('gbk', errors='replace')
        parts = gy.split(',')
        if len(parts) < 3:
            continue
        league = parts[0].strip()
        home = parts[1].strip()
        away = parts[2].strip()

        home_score = None
        away_score = None
        if status == '4':
            start = raw.find(f'id="a{fid}"'.encode())
            if start >= 0:
                end = raw.find(b'</tr>', start)
                if end < 0:
                    end = start + 3000
                chunk = raw[start:end]
                # 比分在 class="red" 的td中: class="red">0 - 1<
                score_match = re.search(rb'class="red">(\d+)\s*-\s*(\d+)<', chunk)
                if score_match:
                    home_score = int(score_match.group(1))
                    away_score = int(score_match.group(2))

        matches.append({
            'fid': fid, 'league': league, 'home': home, 'away': away,
            'status': status, 'home_score': home_score, 'away_score': away_score,
            'source': '2h1'
        })
    return matches


def extract_from_weekfixture(raw):
    """从weekfixture.php提取比赛 (fid, league, home, away)
    
    结构: <tr id="aXXXXX" gy="league,home,away" ...>
    注意: weekfixture没有fid属性，id="aXXXXX"的数字就是fid
    """
    matches = []
    pattern = rb'id="a(\d+)"\s+gy="([^"]*)"'
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
        matches.append({
            'fid': fid, 'league': league, 'home': home, 'away': away,
            'status': '0', 'home_score': None, 'away_score': None,
            'source': 'weekfixture'
        })
    return matches


def main():
    parser = argparse.ArgumentParser(description='从500.com页面提取fid更新DB v3')
    parser.add_argument('--db', default='data/football.db', help='数据库路径')
    parser.add_argument('--date', default=None, help='目标日期，默认按页面当天')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--dry-run', action='store_true', help='只匹配不写入DB')
    args = parser.parse_args()

    all_matches = []

    print("获取 2h1.php (当天赛事)...")
    try:
        raw_2h1 = fetch_page_raw('https://live.500.com/2h1.php')
        matches_2h1 = extract_from_2h1(raw_2h1)
        finished = [m for m in matches_2h1 if m['status'] == '4']
        print(f"  当天: {len(matches_2h1)}场 (完场{len(finished)}场, 未开赛{len(matches_2h1)-len(finished)}场)")
        all_matches.extend(matches_2h1)
    except Exception as e:
        print(f"  ❌ 2h1.php 失败: {e}")

    print("获取 weekfixture.php (未来2天赛事)...")
    try:
        raw_week = fetch_page_raw('https://live.500.com/weekfixture.php')
        matches_week = extract_from_weekfixture(raw_week)
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

    date_filter = args.date

    # 查缺fid的比赛
    if date_filter:
        cur.execute(
            "SELECT id, home_team, away_team, kickoff_time, fid_500 "
            "FROM poisson_predictions WHERE date=? AND (fid_500 IS NULL OR fid_500='' OR fid_500=0)",
            (date_filter,)
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
    score_updated = 0

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

    # 补赛果：从2h1.php的完场比分回填actual_outcome
    finished_matches = [m for m in all_matches if m['status'] == '4' and m['home_score'] is not None]
    if finished_matches:
        print(f"\n赛果回填: {len(finished_matches)}场完场比赛")
        for fm in finished_matches:
            fid = fm['fid']
            hs = fm['home_score']
            gs = fm['away_score']
            if hs > gs:
                outcome = f"主胜 {hs}-{gs}"
            elif hs == gs:
                outcome = f"平局 {hs}-{gs}"
            else:
                outcome = f"客胜 {hs}-{gs}"

            if not args.dry_run:
                cur.execute(
                    "UPDATE poisson_predictions SET actual_outcome=? WHERE fid_500=? AND (actual_outcome IS NULL OR actual_outcome='')",
                    (outcome, fid)
                )
                if cur.rowcount > 0:
                    score_updated += 1
                    if args.verbose:
                        print(f"  ⚽ 赛果: {fm['home']} {hs}-{gs} {fm['away']} ({outcome})")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\n=== fid补全 ===")
    print(f"匹配 {fid_matched}/{len(db_rows)} 场")
    print(f"  精确: {stats['exact']}  别名: {stats['alias']}  模糊: {stats['fuzzy']}")
    print(f"  互换精确: {stats['swap_exact']}  互换别名: {stats['swap_alias']}  互换模糊: {stats['swap_fuzzy']}")
    print(f"  未匹配: {stats['miss']}")
    print(f"=== 赛果回填 ===")
    print(f"更新 {score_updated} 场赛果")
    if args.dry_run:
        print("(dry-run模式，未写入DB)")


if __name__ == '__main__':
    main()
