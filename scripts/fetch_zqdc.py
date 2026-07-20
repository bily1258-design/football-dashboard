#!/usr/bin/env python3
"""fetch_zqdc.py — 从titan007获取北单/竞彩比赛数据 (v2, 2026-07-19)

数据流（新管道，替换已失效的CommonInterface+op1）:
1. bfdata_ut.js → 解析比赛列表，过滤北单(f[59]!="")
2. 1x2d.titan007.com/{sid}.js → 获取全公司赔率(含HKJC+Pinnacle)
3. 输出到 data/matches_{日期}.json

用法:
  python3 scripts/fetch_zqdc.py                                    # 今天
  python3 scripts/fetch_zqdc.py --date 2026-07-12                 # 指定日期
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --backfill      # 比分回填
  python3 scripts/fetch_zqdc.py --max 5                            # 只取前5场(测试)

输出: data/matches_{YYYYMMDD}.json
"""
import re, json, os, argparse, urllib.request, time, sys
from datetime import datetime, date, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
sys.path.insert(0, SCRIPT_DIR)

# ─────── 1x2d 赔率接口（从titan007_utils导入） ───────
from titan007_utils import fetch_1x2d_odds, f_float


# ─────── bfdata_ut.js ───────

def fetch_bfdata():
    """获取并解析bfdata_ut.js，返回所有比赛的字段数组
    
    返回: [{sid, league, hometeam, awayteam, time, display_time, fields_list}]
    """
    ts = int(time.time() * 1000)
    url = f'https://livestatic.titan007.com/vbsxml/bfdata_ut.js?r=007{ts}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36',
        'Referer': 'https://live.titan007.com/',
        'Cookie': 'user=""; undefined=""',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
    except Exception as e:
        print(f'[WARN] bfdata_ut.js 抓取失败: {e}')
        return []

    if len(raw) < 100:
        print(f'[WARN] bfdata_ut.js 返回空数据({len(raw)} bytes)')
        return []

    pattern = rb"A\[(\d+)\]=\"([^\"]*?)\"\.split\('\^'\);"
    matches = []
    for m in re.finditer(pattern, raw):
        try:
            val_bytes = m.group(2)
            val = val_bytes.decode('utf-8')
            fields = val.split('^')
            if len(fields) < 3:
                continue
            matches.append({
                'sid': fields[0],
                'league': re.sub(r'<[^>]+>', '', fields[2]),
                'hometeam': re.sub(r'<[^>]+>', '', fields[5]) if len(fields) > 5 else '',
                'awayteam': re.sub(r'<[^>]+>', '', fields[8]) if len(fields) > 8 else '',
                'time': fields[12] if len(fields) > 12 else '',
                'display_time': fields[11] if len(fields) > 11 else '',
                'fields': fields,
            })
        except (UnicodeDecodeError, IndexError):
            pass

    return matches


def filter_beidan(matches):
    """从比赛列表中过滤出北单(f[59]!="")"""
    result = []
    for m in matches:
        f = m['fields']
        if len(f) > 59 and f[59].strip():
            result.append(m)
    return result


def filter_jingzu(matches):
    """从比赛列表中过滤出竞足(f[58]!="")"""
    result = []
    for m in matches:
        f = m['fields']
        if len(f) > 58 and f[58].strip():
            result.append(m)
    return result


def parse_time(fields):
    """从fields解析比赛时间"""
    try:
        if len(fields) > 12 and fields[12]:
            parts = fields[12].split(',')
            if len(parts) >= 5:
                y, mo, d, h, mi = parts[:5]
                return f'{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{int(mi):02d}'
    except (ValueError, IndexError):
        pass
    if len(fields) > 11 and fields[11]:
        return f'0000-00-00 {fields[11]}'
    return ''


def _fix_month(match_time, date_str):
    """bfdata_ut.js的f[12]月份有时错位(6月但实际是7月), 纠正日期部分"""
    if not match_time or not date_str or len(match_time) < 16:
        return match_time
    # 只替换日期部分(前10字符), 保留时间部分
    if match_time[:10] != date_str:
        return date_str + match_time[10:]
    return match_time


# ─────── 主逻辑 ───────

def fetch_all_matches(date_str, max_matches=0):
    """从bfdata_ut.js获取北单比赛列表"""
    all_matches = fetch_bfdata()
    if not all_matches:
        print(f'[WARN] bfdata_ut.js 返回空列表')
        return []

    # 过滤北单
    beidan = filter_beidan(all_matches)
    jingzu = filter_jingzu(all_matches)

    print(f'[INFO] bfdata_ut.js → 总{len(all_matches)}场, '
          f'北单{len(beidan)}场, 竞足{len(jingzu)}场')

    # 过滤非当天比赛
    date_compact = date_str.replace('-', '')
    filtered = []
    for m in beidan:
        t = m.get('time', '')  # yyyy,mm,dd,hh,mm,ss
        if t:
            t_parts = t.split(',')
            if len(t_parts) >= 3:
                md = f'{t_parts[0]}{int(t_parts[1]):02d}{int(t_parts[2]):02d}'
                if md == date_compact:
                    filtered.append(m)

    # 如果当天没有匹配的（7/19凌晨的比赛是7/18晚的），放宽到所有北单
    if not filtered and beidan:
        print(f'[INFO] 当天({date_compact})无匹配北单, 取全部{len(beidan)}场')
        filtered = beidan

    if max_matches > 0:
        filtered = filtered[:max_matches]

    # 转成标准格式
    result = []
    for m in filtered:
        f = m['fields']
        # 从bfdata_ut.js提取比分: f[13]=状态(-1=完场, 3=结束/其他), f[14]=主队进球, f[15]=客队进球
        score = ''
        if len(f) > 15:
            try:
                status = int(f[13])
                hs = int(f[14])
                aas = int(f[15])
                if status in (-1, 3):  # 完场
                    score = f"{hs}-{aas}"
            except (ValueError, IndexError):
                pass
        result.append({
            'sid': m['sid'],
            'league': m['league'],
            'home_team': m['hometeam'],
            'away_team': m['awayteam'],
            'display_time': m['display_time'],
            'match_time': _fix_month(parse_time(f), date_str),
            'date': date_str,
            'score': score,
        })

    return result


def fetch_odds(matches, delay=0.3, workers=3):
    """并发获取所有比赛的1x2d赔率"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(matches)
    enriched = []

    def process_one(match):
        sid = match['sid']
        match_out = {
            'fid': sid,
            'date': match.get('date', ''),
            'match_time': match.get('match_time', ''),
            'display_time': match.get('display_time', ''),
            'event': match.get('league', ''),
            'home_team': match.get('home_team', ''),
            'away_team': match.get('away_team', ''),
            'score': match.get('score', ''),
            'status': '',
            'source': 'beidan',
            'home_rank': 0,
            'away_rank': 0,
        }

        odds = fetch_1x2d_odds(sid)
        if odds:
            # Pinnacle (cid=177)
            p = odds.get('177')
            if p:
                match_out['odds_pinnacle_open_win'] = p['init_w']
                match_out['odds_pinnacle_open_draw'] = p['init_d']
                match_out['odds_pinnacle_open_loss'] = p['init_l']
                match_out['odds_pinnacle_win'] = p['curr_w']
                match_out['odds_pinnacle_draw'] = p['curr_d']
                match_out['odds_pinnacle_loss'] = p['curr_l']
                match_out['odds_pinnacle_changes'] = 0
                match_out['odds_pinnacle_company'] = 'Pinnacle'

            # HKJC (cid=432)
            h = odds.get('432')
            if h:
                match_out['odds_hkjc_open_win'] = h['init_w']
                match_out['odds_hkjc_open_draw'] = h['init_d']
                match_out['odds_hkjc_open_loss'] = h['init_l']
                match_out['odds_hkjc_win'] = h['curr_w']
                match_out['odds_hkjc_draw'] = h['curr_d']
                match_out['odds_hkjc_loss'] = h['curr_l']
                match_out['odds_hkjc_changes'] = 0
                match_out['odds_hkjc_company'] = 'HKJC'

        return match_out

    with ThreadPoolExecutor(max_workers=workers) as executor:
        fut_map = {executor.submit(process_one, m): m for m in matches}
        done = 0
        for fut in as_completed(fut_map):
            done += 1
            match_out = fut.result()
            enriched.append(match_out)
            ph = match_out.get('odds_pinnacle_win', '-')
            pd = match_out.get('odds_pinnacle_draw', '-')
            pl = match_out.get('odds_pinnacle_loss', '-')
            hh = match_out.get('odds_hkjc_win', '-')
            hd = match_out.get('odds_hkjc_draw', '-')
            hl = match_out.get('odds_hkjc_loss', '-')
            print(f'  [{done}/{total}] ✓ {match_out["event"]} {match_out["home_team"]} vs {match_out["away_team"]}  '
                  f'Pinnacle: {ph}/{pd}/{pl}  HKJC: {hh}/{hd}/{hl}')
            if delay > 0:
                time.sleep(delay / workers)

    # 按比赛时间排序
    enriched.sort(key=lambda x: x['match_time'])
    return enriched


# ─────── 比分回填 ───────

def get_scores_from_over_page(date_str):
    """从titan007 Over_日期.htm 获取当天所有完场比分
    
    返回: { (home_team, away_team): score_str }
    """
    try:
        url = f'https://bf.titan007.com/football/Over_{date_str.replace("-", "")}.htm'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
        })
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        html = raw.decode('gb2312', errors='replace')

        scores = {}
        rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(tds) >= 6:
                tds_clean = []
                for td in tds:
                    c = re.sub(r'<[^>]+>', ' ', td).strip()
                    c = re.sub(r'\s+', ' ', c)
                    tds_clean.append(c)
                home = tds_clean[3].strip()
                score = tds_clean[4].strip()
                away = tds_clean[5].strip()
                if re.match(r'^\d+\s*-\s*\d+$', score):
                    home_clean = re.sub(r'\s*\[[^\]]*\]', '', home).strip()
                    away_clean = re.sub(r'\s*\[[^\]]*\]', '', away).strip()
                    home_clean = re.sub(r'\([^)]*\)', '', home_clean).strip()
                    away_clean = re.sub(r'\([^)]*\)', '', away_clean).strip()
                    scores[(home_clean, away_clean)] = score
        return scores
    except Exception as e:
        print(f'[WARN] Over页面抓取失败: {e}')
        return {}


def do_backfill(fpath, date_str):
    """比分回填：titan007 Over页"""
    if not os.path.exists(fpath):
        print(f'[WARN] {fpath} 不存在，跳过回填')
        return

    with open(fpath, encoding='utf-8') as f:
        existing = json.load(f)
    existing_matches = {m['fid']: m for m in existing.get('matches', [])}
    updated = 0

    unscored = [(fid, m) for fid, m in existing_matches.items()
                if not m.get('score') and m.get('match_time')]
    if unscored:
        scores = get_scores_from_over_page(date_str)

        # 如果主Over页无数据，尝试从比赛match_time提取日期（因源数据中时间可能跨月）
        if not scores and unscored:
            alt_dates = sorted(set(
                m['match_time'][:10].replace('-', '')
                for _, m in unscored
                if m.get('match_time', '').startswith('20')
            ))
            for alt_date in alt_dates:
                alt_date_fmt = f'{alt_date[:4]}-{alt_date[4:6]}-{alt_date[6:]}'
                if alt_date_fmt == date_str:
                    continue  # 已尝试过
                alt_scores = get_scores_from_over_page(alt_date)
                if alt_scores:
                    scores = alt_scores
                    print(f'[BACKFILL] 从备选日期 {alt_date} 获取 {len(scores)} 场比分')
                    break

        if scores:
            over_ok = 0
            for fid, m in unscored:
                home = m.get('home_team', '').strip()
                away = m.get('away_team', '').strip()
                key = (home, away)
                if key in scores:
                    m['score'] = scores[key]
                    over_ok += 1
                    updated += 1
                elif (away, home) in scores:
                    m['score'] = scores[(away, home)]
                    over_ok += 1
                    updated += 1
            print(f'[BACKFILL] titan007 Over页 → {over_ok}/{len(unscored)} 场')
        else:
            print(f'[BACKFILL] titan007 Over页 → 页面无完场数据')

    if updated:
        existing['matches'] = list(existing_matches.values())
        existing['fetched_at'] = datetime.now().isoformat()
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f'[BACKFILL] {fpath} → {updated} 场比分已更新')
    else:
        print(f'[BACKFILL] {fpath} → 无变化')


# ─────── 入口 ───────

def main():
    parser = argparse.ArgumentParser(description='从titan007获取北单/竞彩比赛数据')
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--max', type=int, default=0, help='最多处理N场(0=全部)')
    parser.add_argument('--delay', type=float, default=0.3)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--backfill', action='store_true', help='比分回填')
    parser.add_argument('--save', default='')
    args = parser.parse_args()

    date_str = args.date
    basename = args.save or ('matches_' + date_str.replace('-', ''))
    fpath = os.path.join(DATA_DIR, f'{basename}.json')

    if args.backfill:
        do_backfill(fpath, date_str)
        return

    # 1. 获取北单比赛列表
    print(f'[INFO] 从bfdata_ut.js获取 {date_str} 北单比赛...')
    all_matches = fetch_all_matches(date_str, args.max)

    if not all_matches:
        print(f'[WARN] {date_str} 无北单比赛数据')
        return

    # 2. 获取赔率
    print(f'[INFO] 获取 {len(all_matches)} 场北单赔率 (1x2d)...')
    enriched = fetch_odds(all_matches, args.delay, args.parallel)

    # 3. 输出
    out = {
        'date': date_str,
        'fetched_at': datetime.now().isoformat(),
        'total': len(enriched),
        'source': 'titan007',
        'matches': enriched,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'[OK] {len(enriched)} 场 → {fpath}')


if __name__ == '__main__':
    main()
