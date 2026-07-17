#!/usr/bin/env python3
"""使用5s间隔更新赔率（HKJC=cid=432, 平博=cid=177）"""
import sys, time, json, re, urllib.request
sys.path.insert(0, 'scripts')
from titan007_utils import sid_to_oddsid

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

CID_HKJC = '432'
CID_PINNACLE = '177'

def fetch_odds_page(sid, cid):
    oddsid = sid_to_oddsid(sid)
    url = f'https://op1.titan007.com/OddsHistory.aspx?id={oddsid}&sid={sid}&cid={cid}&l=1'
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=15).read()
    try:
        text = raw.decode('gbk')
    except:
        text = raw.decode('utf-8', errors='replace')
    if '限制' in text or len(raw) < 100:
        return None
    return text

def parse_all_odds(text):
    """返回 (latest_odds, opening_odds) 或 None"""
    rows = re.findall(
        r'<tr\s+align=center\s+bgcolor=#FFFFFF>.*?</tr>',
        text, re.DOTALL | re.IGNORECASE
    )
    if not rows:
        return None
    result = []
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(tds) < 3:
            continue
        try:
            vals = [float(re.sub(r'<[^>]+>', '', td).strip()) for td in tds[:3]]
            result.append(vals)
        except:
            continue
    if not result:
        return None
    latest = {'win': result[0][0], 'draw': result[0][1], 'loss': result[0][2]}
    opening = {'win': result[-1][0], 'draw': result[-1][1], 'loss': result[-1][2]}
    return latest, opening

def extract_company_name(text):
    m = re.search(r"<font color='red'>(.*?)</font>", text)
    return m.group(1) if m else ''

MATCHES = [
    ("1358420", "2908636", "Nashville SC", "Atlanta United"),
    ("1440668", "2910657", "Bahia", "Chapecoense SC"),
    ("1442475", "2910814", "Mirassol", "Botafogo SP"),
    ("1442462", "2912205", "Sampaio Correa", "Ferroviario"),
    ("1442456", "2912209", "Criciuma", "CRB"),
    ("1416699", "2910812", "Ponte Preta", "Amazonas"),
    ("1442471", "2912213", "Ituano", "Athletic Club MG"),
    ("1358655", "2910810", "Sport Recife", "Goias"),
    ("1442472", "2912211", "Vila Nova", "Nautico"),
    ("1358380", "2912222", "Chapecoense SC (Res)", "Figueirense"),
]

def main():
    data_file = 'data/matches_hkjc_20260718.json'
    with open(data_file) as f:
        db = json.load(f)
    
    matches_by_fid = {m['fid']: m for m in db['matches']}
    
    hkjc_ok = 0
    pin_ok = 0
    missed = 0
    req_count = 0
    
    for fid, sid, home, away in MATCHES:
        match = matches_by_fid.get(fid)
        if not match:
            print(f'  ⏭️ {home} vs {away}: 不在DB')
            missed += 1
            continue
        
        # ===== HKJC =====
        req_count += 1
        if req_count > 1:
            time.sleep(5)
        text = fetch_odds_page(sid, CID_HKJC)
        if text is None:
            print(f'  ❌ {home} vs {away}: HKJC被限')
            missed += 1
            continue
        
        parsed = parse_all_odds(text)
        if parsed is None:
            print(f'  ❌ {home} vs {away}: HKJC解析失败')
            missed += 1
            continue
        
        latest, opening = parsed
        company = extract_company_name(text)
        match['odds_hkjc_win'] = latest['win']
        match['odds_hkjc_draw'] = latest['draw']
        match['odds_hkjc_loss'] = latest['loss']
        match['odds_hkjc_open_win'] = opening['win']
        match['odds_hkjc_open_draw'] = opening['draw']
        match['odds_hkjc_open_loss'] = opening['loss']
        match['odds_hkjc_company'] = company or 'HKJC'
        match['odds_hkjc_changes'] = len(parsed) if isinstance(parsed, tuple) and parsed else match.get('odds_hkjc_changes', 0)
        hkjc_msg = f"HKJC={latest['win']}/{latest['draw']}/{latest['loss']}"
        
        # ===== 平博 =====
        req_count += 1
        time.sleep(5)
        text2 = fetch_odds_page(sid, CID_PINNACLE)
        if text2 is not None:
            parsed2 = parse_all_odds(text2)
            if parsed2:
                latest2, opening2 = parsed2
                company2 = extract_company_name(text2)
                match['odds_pinnacle_win'] = latest2['win']
                match['odds_pinnacle_draw'] = latest2['draw']
                match['odds_pinnacle_loss'] = latest2['loss']
                match['odds_pinnacle_open_win'] = opening2['win']
                match['odds_pinnacle_open_draw'] = opening2['draw']
                match['odds_pinnacle_open_loss'] = opening2['loss']
                match['odds_pinnacle_company'] = company2 or 'Pinnacle'
                match['odds_pinnacle_changes'] = len(parsed2) if parsed2 else match.get('odds_pinnacle_changes', 0)
                pin_ok += 1
                print(f'  ✅ {home} vs {away}: {hkjc_msg} 平博={latest2["win"]}/{latest2["draw"]}/{latest2["loss"]}')
            else:
                print(f'  ⚠️ {home} vs {away}: {hkjc_msg} 平博=解析失败')
        else:
            print(f'  ⚠️ {home} vs {away}: {hkjc_msg} 平博=被限')
        
        hkjc_ok += 1
    
    db['fetched_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with open(data_file, 'w') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    print(f'\n=== 完成：HKJC={hkjc_ok}, 平博={pin_ok}, 跳过={missed} (共{req_count}次请求) ===')

if __name__ == '__main__':
    main()
