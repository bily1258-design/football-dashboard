#!/usr/bin/env python3
"""并行+titan007 odds更新 — 3 worker, 0.3s间隔"""
import sys, time, json, re, urllib.request, concurrent.futures
sys.path.insert(0, 'scripts')
from titan007_utils import sid_to_oddsid

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

RATE_SLEEP = 0.3  # 并发间间隔

def fetch_single(sid):
    """返回 (sid, latest_dict, opening_dict, err)"""
    oddsid = sid_to_oddsid(sid)
    url = f'https://op1.titan007.com/OddsHistory.aspx?id={oddsid}&sid={sid}&cid=432&l=1'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        data = urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        return (sid, None, None, str(e))
    
    try:
        text = data.decode('gbk')
    except:
        text = data.decode('utf-8', errors='replace')
    
    if '频率' in text or '限制' in text or len(data) < 200:
        return (sid, None, None, f'受限({len(data)}b)')
    
    rows = re.findall(
        r'<tr\s+align=center\s+bgcolor=#FFFFFF>.*?</tr>',
        text, re.DOTALL | re.IGNORECASE
    )
    if not rows:
        return (sid, None, None, '无赔率行')
    
    def parse_row(row):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        try:
            return [float(re.sub(r'<[^>]+>', '', td).strip()) for td in tds[:3]]
        except:
            return None
    
    all_parsed = [parse_row(r) for r in rows]
    all_parsed = [p for p in all_parsed if p]
    if not all_parsed:
        return (sid, None, None, '解析失败')
    
    latest = {'win': all_parsed[0][0], 'draw': all_parsed[0][1], 'loss': all_parsed[0][2]}
    opening = {'win': all_parsed[-1][0], 'draw': all_parsed[-1][1], 'loss': all_parsed[-1][2]}
    return (sid, latest, opening, None)

def main():
    data_file = 'docs/data/results.json'
    with open(data_file) as f:
        db = json.load(f)
    
    # 收集所有英文sid（未开场）
    targets = []
    for idx, m in enumerate(db['matches']):
        fid = m.get('fid', '')
        if fid.isdigit() and len(fid) >= 7 and int(fid) >= 2000000 and m.get('score', '') == '':
            if m.get('home_team') and m.get('away_team'):
                targets.append((idx, m))
    
    sids = ['/'.join([m['home_team'], m['away_team']]) for idx, m in targets]
    total_sids = len(list(dict.fromkeys([m['fid'] for idx, m in targets])))
    print(f'待抓取: {len(targets)} 场 ({total_sids}个唯一sid)')
    
    unique_sids = list(dict.fromkeys([m['fid'] for idx, m in targets]))
    
    updated = 0
    errors = 0
    start = time.time()
    
    # 分组并发: 每次3个
    for i in range(0, len(unique_sids), 3):
        batch = unique_sids[i:i+3]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            fut_map = {executor.submit(fetch_single, sid): sid for sid in batch}
            for fut in concurrent.futures.as_completed(fut_map):
                sid, latest, opening, err = fut.result()
                if err:
                    print(f'  ❌ {sid}: {err}')
                    errors += 1
                else:
                    # 更新所有匹配这个sid的条目
                    for idx, m in targets:
                        if m['fid'] == sid:
                            m['odds_win'] = latest['win']
                            m['odds_draw'] = latest['draw']
                            m['odds_loss'] = latest['loss']
                            m['open_win_pin'] = opening['win']
                            m['open_draw_pin'] = opening['draw']
                            m['open_loss_pin'] = opening['loss']
                            m['comparison'] = {
                                'open': [opening['win'], opening['draw'], opening['loss']],
                                'current': [latest['win'], latest['draw'], latest['loss']],
                                'div_pct': [
                                    round((latest['win'] - opening['win']) / opening['win'] * 100, 1),
                                    round((latest['draw'] - opening['draw']) / opening['draw'] * 100, 1),
                                    round((latest['loss'] - opening['loss']) / opening['loss'] * 100, 1),
                                ]
                            }
                            m['hkjc_comparison'] = dict(m['comparison'])
                            total_pct = (1/latest['win'] + 1/latest['draw'] + 1/latest['loss']) * 100
                            m['margin'] = round(total_pct - 100, 2) / 100
                    print(f'  ✅ {sid} {latest["win"]}/{latest["draw"]}/{latest["loss"]}')
                    updated += 1
        
        if i + 3 < len(unique_sids):
            time.sleep(RATE_SLEEP)
    
    elapsed = time.time() - start
    db['odds_fetched_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    
    with open(data_file, 'w') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    print(f'\n=== 完成: {updated}成功, {errors}失败, 耗时{elapsed:.1f}s ===')

if __name__ == '__main__':
    main()
