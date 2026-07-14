import urllib.request, json, re

fid = '1362695'

# Get all CIDs from HTML
url = f'https://odds.500.com/fenxi/ouzhi-{fid}.shtml'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=15)
html = resp.read().decode('gbk', errors='replace')

cids_found = sorted(set(re.findall(r'cid[=\\":\'s](\d+)', html, re.I)))
print(f'All CIDs from HTML ({len(cids_found)})')

# Try with and without type parameter
print(f'\nTrying WITHOUT type parameter:')
for cid_str in cids_found:
    cid = int(cid_str)
    url2 = f'https://odds.500.com/fenxi/json/ouzhi.php?fid={fid}&cid={cid}'
    req2 = urllib.request.Request(url2, headers={
        'User-Agent': 'Mozilla/5.0',
    })
    try:
        resp2 = urllib.request.urlopen(req2, timeout=5)
        raw = resp2.read().decode('utf-8', errors='replace').strip()
        if raw and raw not in ('null','[]'):
            print(f'  cid={cid:5s}: HAS DATA (no type param)')
    except:
        pass

# Also try the alternative endpoint format
print(f'\nTrying odds_json.php endpoint:')
for cid_str in cids_found:
    cid = int(cid_str)
    url2 = f'https://odds.500.com/fenxi/json/odds_json.php?fid={fid}&cid={cid}'
    req2 = urllib.request.Request(url2, headers={
        'User-Agent': 'Mozilla/5.0',
    })
    try:
        resp2 = urllib.request.urlopen(req2, timeout=5)
        raw = resp2.read().decode('utf-8', errors='replace').strip()
        if raw and raw not in ('null','[]'):
            print(f'  cid={cid:5s}: HAS DATA (odds_json.php)')
    except:
        pass
