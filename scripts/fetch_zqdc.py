#!/usr/bin/env python3
"""fetch_zqdc.py — 从titan007获取北单/竞彩比赛数据

替代：原500.com版(已废弃)

数据流:
1. 从 titan007 CommonInterface type=2 获取当日所有比赛
2. 获取平博赔率(cid=432) + HKJC赔率(cid=177)
3. 输出到 data/matches_{日期}.json

用法:
  python3 scripts/fetch_zqdc.py                                    # 今天
  python3 scripts/fetch_zqdc.py --date 2026-07-12                 # 指定日期
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --no-pinnacle   # 跳过平博
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --backfill      # 比分回填

输出: data/matches_{YYYYMMDD}.json
"""
import re, json, os, argparse, urllib.request, time, sys
from datetime import datetime, date, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")

sys.path.insert(0, SCRIPT_DIR)
from titan007_utils import get_match_list, get_odds_history, fetch_url

# titan007 cid映射
CID_PINNACLE = 432   # 平博
CID_HKJC = 177       # 香港马会


def fetch_all_matches(date_str, max_matches=0, delay=0.3, workers=3):
    """从titan007获取当日所有比赛，带赔率"""
    all_matches = get_match_list(date_str)
    print(f'[INFO] 共 {len(all_matches)} 场比赛')
    
    if max_matches > 0:
        all_matches = all_matches[:max_matches]
    
    return all_matches


def fetch_odds(m, delay=0.3, workers=3):
    """并发获取所有比赛的平博+HKJC赔率"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    total = len(m)
    enriched = []
    
    def process_one(match):
        sid = match['sid']
        match_out = {
            'fid': sid,
            'date': match.get('date', ''),
            'match_time': match.get('match_time', f"{match.get('date', '')} 00:00"),
            'event': match.get('event', match.get('league', '')),
            'home_team': match.get('home_team', ''),
            'away_team': match.get('away_team', ''),
            'score': '',
            'status': '',
            'source': 'beidan',
            'home_rank': 0,
            'away_rank': 0,
        }
        
        # 平博赔率
        p = get_odds_history(sid, CID_PINNACLE)
        if p:
            match_out['odds_pinnacle_open_win'] = p['open']['win']
            match_out['odds_pinnacle_open_draw'] = p['open']['draw']
            match_out['odds_pinnacle_open_loss'] = p['open']['loss']
            match_out['odds_pinnacle_win'] = p['latest']['win']
            match_out['odds_pinnacle_draw'] = p['latest']['draw']
            match_out['odds_pinnacle_loss'] = p['latest']['loss']
            match_out['odds_pinnacle_changes'] = p.get('changes', 1)
            match_out['odds_pinnacle_company'] = 'Pinnacle'
        
        # HKJC赔率
        h = get_odds_history(sid, CID_HKJC)
        if h:
            match_out['odds_hkjc_open_win'] = h['open']['win']
            match_out['odds_hkjc_open_draw'] = h['open']['draw']
            match_out['odds_hkjc_open_loss'] = h['open']['loss']
            match_out['odds_hkjc_win'] = h['latest']['win']
            match_out['odds_hkjc_draw'] = h['latest']['draw']
            match_out['odds_hkjc_loss'] = h['latest']['loss']
            match_out['odds_hkjc_changes'] = h.get('changes', 1)
            match_out['odds_hkjc_company'] = 'HKJC'
        
        return match_out
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        fut_map = {executor.submit(process_one, m): m for m in m}
        done = 0
        for fut in as_completed(fut_map):
            done += 1
            match_out = fut.result()
            enriched.append(match_out)
            print(f'  [{done}/{total}] ✓ {match_out["event"]} {match_out["home_team"]} vs {match_out["away_team"]}  '
                  f'平博: {match_out.get("odds_pinnacle_open_win","-")}/{match_out.get("odds_pinnacle_open_draw","-")}/{match_out.get("odds_pinnacle_open_loss","-")}  '
                  f'HKJC: {match_out.get("odds_hkjc_open_win","-")}/{match_out.get("odds_hkjc_open_draw","-")}/{match_out.get("odds_hkjc_open_loss","-")}')
            if delay > 0:
                time.sleep(delay / workers)
    
    # 按比赛时间排序
    enriched.sort(key=lambda x: x['match_time'])
    return enriched


def get_score_from_titan007(sid):
    """从titan007分析页提取比分 (homeScoreStr, guestScoreStr, totalScoreStr)"""
    import re
    try:
        url = f'https://zq.titan007.com/Analysis/{sid}.htm'
        html = fetch_url(url, timeout=10)
        home_m = re.search(r'var\s+homeScoreStr\s*=\s*\["(\d+)"\]', html)
        guest_m = re.search(r'var\s+guestScoreStr\s*=\s*\["(\d+)"\]', html)
        if home_m and guest_m:
            return f'{home_m.group(1)}-{guest_m.group(1)}'
        return None
    except Exception as e:
        return None


def get_scores_from_over_page(date_str):
    """从titan007 Over_日期.htm 页面获取当天所有完场比分
    
    返回: { (home_team, away_team): score_str } 字典
    """
    import re, urllib.request
    try:
        url = f'https://bf.titan007.com/football/Over_{date_str.replace("-", "")}.htm'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        html = raw.decode('gb2312', errors='replace')
        
        scores = {}
        rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL|re.IGNORECASE)
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL|re.IGNORECASE)
            if len(tds) >= 6:
                tds_clean = []
                for td in tds:
                    c = re.sub(r'<[^>]+>', ' ', td).strip()
                    c = re.sub(r'\s+', ' ', c)
                    tds_clean.append(c)
                # 格式: 联赛 | 时间 | 状态 | 主队 | 比分 | 客队
                home = tds_clean[3].strip()
                score = tds_clean[4].strip()
                away = tds_clean[5].strip()
                # 格式如 "6 - 1", "0 - 0"等
                if re.match(r'^\d+\s*-\s*\d+$', score):
                    # 去掉排名(如 [1]) 对比
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
    """比分回填：新sid→titan007分析页，旧sid→500.com wanchang兜底"""
    if not os.path.exists(fpath):
        print(f'[WARN] {fpath} 不存在，跳过回填')
        return
    
    with open(fpath, encoding='utf-8') as f:
        existing = json.load(f)
    existing_matches = {m['fid']: m for m in existing.get('matches', [])}
    updated = 0
    
    # 1. titan007 Over页完整比分表 — 所有sid通用，按队名匹配
    unscored = [(fid, m) for fid, m in existing_matches.items()
                if not m.get('score') and m.get('match_time')]
    if unscored:
        scores = get_scores_from_over_page(date_str)
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
                else:
                    # 模糊匹配: 看主客互换
                    if (away, home) in scores:
                        m['score'] = scores[(away, home)]
                        over_ok += 1
                        updated += 1
            if over_ok:
                print(f'[BACKFILL] titan007 Over页 → {over_ok}/{len(unscored)} 场')
            else:
                print(f'[BACKFILL] titan007 Over页 → 未匹配到比分')
        else:
            print(f'[BACKFILL] titan007 Over页 → 页面无完场数据')
    
    # 2. wanchang.php兜底（500.com，补Over页漏掉的旧sid）
    wc_unscored = [(fid, m) for fid, m in existing_matches.items()
                   if not fid.startswith('29') and not m.get('score') and m.get('match_time')]
    if wc_unscored:
        print(f'[BACKFILL] wanchang.php兜底: {len(wc_unscored)} 场待补…')
        try:
            wc_html = fetch_url('https://live.500.com/wanchang.php')
            wc_scores = {}
            for pk_div in re.finditer(
                r'<div\s+class="pk">.*?<a[^>]*fid=(\d+)[^>]*>(\d+)</a>\s*<span>-</span>\s*<a[^>]*fid=\1[^>]*>(\d+)</a>',
                wc_html, re.DOTALL | re.IGNORECASE):
                wc_scores[pk_div.group(1)] = f'{pk_div.group(2)}-{pk_div.group(3)}'
            wc_updated = 0
            for fid, m in wc_unscored:
                if fid in wc_scores:
                    m['score'] = wc_scores[fid]
                    wc_updated += 1
                    updated += 1
            print(f'[BACKFILL] wanchang.php → {wc_updated}/{len(wc_unscored)} 场')
        except Exception as e:
            print(f'[BACKFILL] wanchang.php抓取失败: {e}')
    
    if updated:
        existing['matches'] = list(existing_matches.values())
        existing['fetched_at'] = datetime.now().isoformat()
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f'[BACKFILL] {fpath} → {updated} 场比分已更新')
    else:
        print(f'[BACKFILL] {fpath} → 无变化')


def main():
    parser = argparse.ArgumentParser(description='从titan007获取北单/竞彩比赛数据')
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--max', type=int, default=0, help='最多处理N场(0=全部)')
    parser.add_argument('--delay', type=float, default=0.3)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--no-pinnacle', action='store_true', help='跳过平博抓取')
    parser.add_argument('--no-hkjc', action='store_true', help='跳过香港马会抓取')
    parser.add_argument('--backfill', action='store_true', help='比分回填')
    parser.add_argument('--save', default='')
    args = parser.parse_args()
    
    date_str = args.date
    basename = args.save or ('matches_' + date_str.replace('-', ''))
    fpath = os.path.join(DATA_DIR, f'{basename}.json')
    
    if args.backfill:
        do_backfill(fpath, date_str)
        return
    
    # 1. 获取比赛列表
    print(f'[INFO] 从titan007获取 {date_str} 比赛...')
    all_matches = fetch_all_matches(date_str, args.max, args.delay, args.parallel)
    
    if not all_matches:
        print(f'[WARN] {date_str} 无比赛数据')
        return
    
    # 给match补上date字段
    for m in all_matches:
        m['date'] = date_str
    
    # 2. 获取赔率（合并平博+HKJC到一次请求）
    print('[INFO] 获取赔率...')
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
