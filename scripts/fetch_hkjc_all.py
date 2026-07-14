#!/usr/bin/env python3
"""fetch_hkjc_all.py — 从500.com获取所有有香港马会(HKJC)赔率的比赛

思路:
1. 从 live.500.com/2h1.php 页面提取所有比赛fid
2. 逐个检查是否有HKJC赔率(cid=122)
3. 有则获取比赛详情(联赛/队伍/时间/比分)和平博赔率
4. 输出到 data/matches_hkjc_{日期}.json

用法:
  python3 scripts/fetch_hkjc_all.py                                    # 今天
  python3 scripts/fetch_hkjc_all.py --date 2026-07-14
  python3 scripts/fetch_hkjc_all.py --date 2026-07-14 --merge          # 合并到已有数据
"""
import re, json, os, argparse, urllib.request, time, sys
from datetime import datetime, date, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
DOCS_DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "docs", "data")

API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'X-Requested-With': 'XMLHttpRequest',
}


def fetch_url(url, encoding='gbk', timeout=15):
    """通用抓取"""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, errors='replace')


def fetch_json(url, retries=2, referer=None):
    """请求500.com JSON接口"""
    for attempt in range(retries + 1):
        try:
            headers = dict(API_HEADERS)
            if referer:
                headers['Referer'] = referer
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='replace').strip()
                if raw.startswith('(') and raw.endswith(')'):
                    raw = raw[1:-1]
                return json.loads(raw) if raw else None
        except Exception:
            if attempt == retries:
                return None
            time.sleep(1)
    return None


def fetch_1x2_odds(fid, cid):
    """获取指定公司的1X2开盘和最新赔率"""
    url = 'https://odds.500.com/fenxi/json/ouzhi.php?fid=%s&cid=%s&type=europe' % (fid, cid)
    referer = 'https://odds.500.com/fenxi/ouzhi-%s.shtml' % fid
    data = fetch_json(url, referer=referer)
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    try:
        opening = data[-1]
        latest = data[0]
        return {
            'open': {'w': float(opening[0]), 'd': float(opening[1]), 'l': float(opening[2])},
            'latest': {'w': float(latest[0]), 'd': float(latest[1]), 'l': float(latest[2])},
        }
    except (IndexError, ValueError, TypeError):
        return None


def extract_match_details(fid):
    """从 detail.php 提取比赛详情"""
    try:
        html = fetch_url('https://live.500.com/detail.php?fid=%s' % fid)
    except Exception:
        return None

    # 从<title>提取队伍名
    title_m = re.search(r'<title>([^<]+)</title>', html)
    if not title_m:
        return None
    title = title_m.group(1)

    # 拆分队伍名: "XXXVSYYY足球比赛直播..."
    vs_m = re.search(r'^(.+?)VS(.+?)(?:足球|\d)', title)
    if not vs_m:
        return None
    home_team = vs_m.group(1).strip()
    away_raw = vs_m.group(2).strip()
    away_team = re.split(r'[足球比赛直播在线_\-]', away_raw)[0].strip()

    # 从页面body找联赛
    league_prefixes = [
        '瑞典超', '挪超', '芬超', '巴甲', '爱超', '韩K联', '韩K2联',
        '日职', '日乙', 'J联赛', 'K联赛', 'K2联赛',
        '英超', '西甲', '意甲', '德甲', '法甲', '欧冠', '欧联', '欧协',
        '澳超', '美职', '荷甲', '比甲', '土超', '丹超', '瑞超',
        '罗甲', '捷甲', '俄超', '乌超', '希超', '奥甲', '瑞士超',
        '巴西乙', '苏超', '德乙', '西乙', '意乙', '法乙', '英冠',
        '阿甲', '墨超', '哥伦甲', '葡超', '荷乙', '波兰超', '冰岛超',
        '中超', '中甲', '沙特联', '泰超',
    ]
    league_name = ''
    for lp in league_prefixes:
        if lp in html:
            league_name = lp
            break

    # 比赛时间
    time_m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', html)
    match_time = time_m.group(1) if time_m else ''

    return {
        'home_team': home_team,
        'away_team': away_team,
        'event': league_name,
        'match_time': match_time,
        'score': '',
    }


def get_fids_from_2h1():
    """从2h1.php页面提取所有fid"""
    html = fetch_url('https://live.500.com/2h1.php')
    # 使用r"..."避免内部引号问题
    fids = list(set(re.findall(r"fid[=_\"'/]?(\d{7,8})", html)))
    return sorted(fids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--delay', type=float, default=0.3, help='请求间隔(秒)')
    parser.add_argument('--max', type=int, default=0, help='最多处理N场比赛(0=全部)')
    parser.add_argument('--merge', action='store_true', help='合并到已有数据')
    parser.add_argument('--save', default='', help='输出文件名(不含日期)')
    args = parser.parse_args()

    date_str = args.date
    print('[INFO] 从2h1.php获取全量fid...')
    all_fids = get_fids_from_2h1()
    print('[INFO] 共 %d 个fid' % len(all_fids))

    if args.max > 0:
        all_fids = all_fids[:args.max]

    # 逐个检查HKJC赔率
    hkjc_matches = []
    checked = 0

    for fid in all_fids:
        checked += 1
        sys.stdout.write('\r  [%d/%d] fid=%s... ' % (checked, len(all_fids), fid))
        sys.stdout.flush()

        h = fetch_1x2_odds(fid, 122)
        if not h:
            print('HKJC无')
            continue

        print('HKJC有! 开盘%s/%s/%s 最新%s/%s/%s' % (
            h['open']['w'], h['open']['d'], h['open']['l'],
            h['latest']['w'], h['latest']['d'], h['latest']['l']))

        # 有HKJC赔率，获取比赛详情
        details = extract_match_details(fid)
        if not details:
            print('  \u26a0 无法获取详情，跳过')
            continue

        # 获取平博赔率
        p = fetch_1x2_odds(fid, 1055)

        match = {
            'fid': fid,
            'date': date_str,
            'match_time': details.get('match_time', ''),
            'event': details.get('event', ''),
            'home_team': details.get('home_team', ''),
            'away_team': details.get('away_team', ''),
            'score': details.get('score', ''),
            'status': '',
            'source': 'hkjc',
        }

        # HKJC赔率
        match['odds_hkjc_open_win'] = h['open']['w']
        match['odds_hkjc_open_draw'] = h['open']['d']
        match['odds_hkjc_open_loss'] = h['open']['l']
        match['odds_hkjc_win'] = h['latest']['w']
        match['odds_hkjc_draw'] = h['latest']['d']
        match['odds_hkjc_loss'] = h['latest']['l']

        # 平博赔率
        if p:
            match['odds_pinnacle_open_win'] = p['open']['w']
            match['odds_pinnacle_open_draw'] = p['open']['d']
            match['odds_pinnacle_open_loss'] = p['open']['l']
            match['odds_pinnacle_win'] = p['latest']['w']
            match['odds_pinnacle_draw'] = p['latest']['d']
            match['odds_pinnacle_loss'] = p['latest']['l']

        hkjc_matches.append(match)
        print('  \u2713 %s %s vs %s' % (
            details.get('event', '?'),
            details.get('home_team', '?'),
            details.get('away_team', '?')))

        if args.delay > 0:
            time.sleep(args.delay)

    print('\n[INFO] 有HKJC赔率的比赛: %d/%d' % (len(hkjc_matches), len(all_fids)))

    # 输出
    out = {
        'date': date_str,
        'fetched_at': datetime.now().isoformat(),
        'total': len(hkjc_matches),
        'source': 'all(2h1.php)',
        'matches': hkjc_matches,
    }

    basename = args.save or ('matches_hkjc_' + date_str.replace('-', ''))
    fpath = os.path.join(DATA_DIR, '%s.json' % basename)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('[OK] %d 场 \u2192 %s' % (len(hkjc_matches), fpath))

    # 如果--merge，合并到results.json
    if args.merge:
        results_path = os.path.join(DOCS_DATA_DIR, 'results.json')
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            existing_matches = existing.get('matches', [])
            existing_obj = existing
        else:
            existing_matches = []
            existing_obj = {'matches': [], 'generated_at': '', 'total_matches': 0, 'date_range': '', 'daily_stats': []}

        existing_fids = {m.get('fid') for m in existing_matches}
        new_count = 0
        for m in hkjc_matches:
            if m['fid'] not in existing_fids:
                existing_matches.append(m)
                new_count += 1

        existing_obj['matches'] = existing_matches
        existing_obj['total_matches'] = len(existing_matches)
        existing_obj['generated_at'] = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

        with open(results_path, 'w', encoding='utf-8') as f:
            json.dump(existing_obj, f, ensure_ascii=False, indent=2)
        print('[MERGE] 新增 %d 场 → docs/data/results.json (共 %d 场)' % (new_count, len(existing_matches)))


if __name__ == '__main__':
    main()
