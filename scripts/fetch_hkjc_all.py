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


def parse_all_from_2h1(html):
    """从2h1.php页面提取所有比赛信息（整页解析，无需逐场调detail.php）
    返回 {fid: {home_team, away_team, event, match_time, score, status}}
    """
    matches = {}
    # <tr id="aFID" ... gy="联赛,主队,客队" ...> ... </tr>
    for tr_m in re.finditer(r'<tr\s+id="a(\d+)"[^>]*status="(\d+)"[^>]*gy="([^"]*)"[^>]*>.*?</tr>', html, re.DOTALL):
        fid = tr_m.group(1)
        status = tr_m.group(2)
        gy = tr_m.group(3)
        row = tr_m.group(0)

        parts = gy.split(',')
        event = parts[0] if len(parts) >= 1 else ''
        home = parts[1] if len(parts) >= 2 else ''
        away = parts[2] if len(parts) >= 3 else ''

        # 时间: <td align="center">07-15&nbsp;14:00</td>
        tm_m = re.search(r'<td[^>]*align="center"[^>]*>(\d{2}-\d{2}\s*&nbsp;\s*\d{2}:\d{2})</td>', row)
        match_time = ''
        if tm_m:
            raw = tm_m.group(1).replace('&nbsp;', ' ')
            year = str(datetime.now().year)
            match_time = f'{year}-{raw}'  # YYYY-MM-DD HH:MM

        # 比分: <div class="pk">...clt1>N<...clt3>M<...
        score = ''
        pk_m = re.search(r'<div class="pk">.*?clt1[^>]*>(\d+)</a><span>-</span><a[^>]*clt3[^>]*>(\d+)</a>', row)
        if pk_m:
            score = f'{pk_m.group(1)}-{pk_m.group(2)}'

        matches[fid] = {
            'home_team': home or '',
            'away_team': away or '',
            'event': event or '',
            'match_time': match_time,
            'score': score or '',
            'status': status,
        }

    return matches


def get_fids_from_2h1():
    """从2h1.php页面提取所有fid"""
    html = fetch_url('https://live.500.com/2h1.php')
    # 使用r"..."避免内部引号问题
    fids = list(set(re.findall(r"fid[=_\\\"'/]?(\\d{7,8})", html)))
    return sorted(fids)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--delay', type=float, default=0.3, help='请求间隔(秒)')
    parser.add_argument('--max', type=int, default=0, help='最多处理N场比赛(0=全部)')
    parser.add_argument('--merge', action='store_true', help='合并到已有数据')
    parser.add_argument('--save', default='', help='输出文件名(不含日期)')
    parser.add_argument('--backfill', action='store_true', help='比分回填：只更新比分，保留已有赔率')
    args = parser.parse_args()

    date_str = args.date
    fpath = os.path.join(DATA_DIR, 'matches_hkjc_%s.json' % date_str.replace('-', ''))

    # ===== 比分回填模式 =====
    if args.backfill:
        if not os.path.exists(fpath):
            print('[WARN] %s 不存在，跳过回填' % fpath)
            return
        with open(fpath) as f:
            existing = json.load(f)
        existing_matches = {m['fid']: m for m in existing['matches']}

        # 重新抓取2h1.php解析比分
        print('[BACKFILL] 重新抓取2h1.php获取最新比分...')
        html = fetch_url('https://live.500.com/2h1.php')
        updated = 0
        # 解析每个 <tr id="aFID"...> 块的比分
        for tr_m in re.finditer(r'<tr[^>]*id="a(\d+)"[^>]*>.*?</tr>', html, re.DOTALL):
            fid = tr_m.group(1)
            row = tr_m.group(0)
            if fid not in existing_matches:
                continue
            # 提取比分: <div class="pk">...clt1...N...clt3...M...
            pk_m = re.search(r'<div class="pk">.*?clt1[^>]*>(\d+)</a><span>-</span><a[^>]*clt3[^>]*>(\d+)</a>', row)
            if pk_m:
                new_scr = '%s-%s' % (pk_m.group(1), pk_m.group(2))
                old_scr = existing_matches[fid].get('score', '')
                if new_scr != old_scr:
                    existing_matches[fid]['score'] = new_scr
                    updated += 1

        if updated:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print('[BACKFILL] %s → %d 场比分已更新' % (fpath, updated))
        else:
            print('[BACKFILL] %s → 无变化' % fpath)
        return

    # ===== 正常抓取模式 =====
    print('[INFO] 从2h1.php获取全量fid...')
    html = fetch_url('https://live.500.com/2h1.php')

    # 整页解析比赛信息（一次解析，用完所有数据）
    all_match_info = parse_all_from_2h1(html)
    all_fids = sorted(all_match_info.keys())
    print('[INFO] 共 %d 场（来自2h1整页）' % len(all_fids))

    if args.max > 0:
        all_fids = all_fids[:args.max]

    # 逐个检查HKJC赔率
    hkjc_matches = []
    checked = 0

    for fid in all_fids:
        checked += 1
        info = all_match_info[fid]
        sys.stdout.write('  [%d/%d] fid=%s %s vs %s... ' % (checked, len(all_fids), fid, info['home_team'], info['away_team']))
        sys.stdout.flush()

        h = fetch_1x2_odds(fid, 122)
        if not h:
            print('无')
            continue

        print('有! 开盘%s/%s/%s 最新%s/%s/%s' % (
            h['open']['w'], h['open']['d'], h['open']['l'],
            h['latest']['w'], h['latest']['d'], h['latest']['l']))

        # 从整页解析数据中获取比赛详情（无需调detail.php）
        match = {
            'fid': fid,
            'date': date_str,
            'match_time': info.get('match_time', ''),
            'event': info.get('event', ''),
            'home_team': info.get('home_team', ''),
            'away_team': info.get('away_team', ''),
            'score': info.get('score', ''),
            'status': info.get('status', ''),
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
        p = fetch_1x2_odds(fid, 1055)
        if p:
            match['odds_pinnacle_open_win'] = p['open']['w']
            match['odds_pinnacle_open_draw'] = p['open']['d']
            match['odds_pinnacle_open_loss'] = p['open']['l']
            match['odds_pinnacle_win'] = p['latest']['w']
            match['odds_pinnacle_draw'] = p['latest']['d']
            match['odds_pinnacle_loss'] = p['latest']['l']

        hkjc_matches.append(match)
        print('  ✓ %s %s vs %s' % (
            info.get('event', '?'),
            info.get('home_team', '?'),
            info.get('away_team', '?')))

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

        existing_keys = {(m.get('home_team',''), m.get('away_team',''), m.get('date','')) for m in existing_matches}
        new_count = 0
        for m in hkjc_matches:
            key = (m.get('home_team',''), m.get('away_team',''), m.get('date',''))
            if key not in existing_keys:
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
