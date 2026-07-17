#!/usr/bin/env python3
"""用5s间隔更新results.json里新sid比赛的赔率"""
import sys, time, json, re, urllib.request
sys.path.insert(0, 'scripts')
from titan007_utils import sid_to_oddsid

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

CID_HKJC = '432'     # 已验证=香港马会
CID_PINNACLE = '177'  # 已验证=平博

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
    """返回 (latest, opening) 或 None"""
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

# targets: (sid, team_name) — 用sid直接查
TARGET_SIDS = [
    '2908636', '2910657', '2910814', '2912205', '2912209',
    '2910812', '2912213', '2910810', '2912211', '2912222',
    '2916521', '2916526', '2916522', '2916530', '2916531', '2916532', '2916536',
    '2908635', '2908634', '2908637',  # 其他MLS
    '2912449', '2912834', '2912450', '2915711', '2915717',
    '2920916', '2920917', '2920918', '2921893', '2921894',
]

def main():
    data_file = 'docs/data/results.json'
    with open(data_file) as f:
        db = json.load(f)
    
    # 找所有未开场且sid匹配的比赛
    to_update = []
    for i, m in enumerate(db['matches']):
        if m['fid'] in TARGET_SIDS and m.get('score', '') == '':
            to_update.append((i, m))
    
    print(f'待更新: {len(to_update)} 场')
    
    hkjc_ok = 0
    pin_ok = 0
    req_count = 0
    
    for idx, m in to_update:
        sid = m['fid']
        home = m['home_team']
        away = m['away_team']
        
        # ===== HKJC =====
        req_count += 1
        if req_count > 1:
            time.sleep(5)
        
        text = fetch_odds_page(sid, CID_HKJC)
        if text is None:
            print(f'  ❌ {home} vs {away}: HKJC被限')
            continue
        
        parsed = parse_all_odds(text)
        if parsed is None:
            print(f'  ❌ {home} vs {away}: HKJC解析失败')
            continue
        
        latest, opening = parsed
        company = extract_company_name(text)
        
        # 更新results.json字段
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
        m['odds_source'] = company or 'HKJC'
        
        # 更新margin
        total_pct = (1/latest['win'] + 1/latest['draw'] + 1/latest['loss']) * 100
        m['margin'] = round(total_pct - 100, 2) / 100
        
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
                if 'pin' not in hkjc_msg:  # 存到hkjc_comparison备用
                    m['hkjc_comparison'] = {
                        'open': [opening2['win'], opening2['draw'], opening2['loss']],
                        'current': [latest2['win'], latest2['draw'], latest2['loss']],
                        'div_pct': [
                            round((latest2['win'] - opening2['win']) / opening2['win'] * 100, 1),
                            round((latest2['draw'] - opening2['draw']) / opening2['draw'] * 100, 1),
                            round((latest2['loss'] - opening2['loss']) / opening2['loss'] * 100, 1),
                        ]
                    }
                pin_ok += 1
                print(f'  ✅ {home} vs {away}: {hkjc_msg} 平博={latest2["win"]}/{latest2["draw"]}/{latest2["loss"]}')
            else:
                print(f'  ⚠️ {home} vs {away}: {hkjc_msg} 平博=解析失败')
        else:
            print(f'  ⚠️ {home} vs {away}: {hkjc_msg} 平博=被限')
        
        hkjc_ok += 1
    
    # 保存
    db['fetched_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with open(data_file, 'w') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    print(f'\n=== 完成：HKJC={hkjc_ok}, 平博={pin_ok} (共{req_count}次请求, 约{req_count*5}s) ===')

if __name__ == '__main__':
    main()
