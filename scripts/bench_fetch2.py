#!/usr/bin/env python3
"""Benchmark: sequential ouzhi page fetching with rate limiting"""
import time, re, json, urllib.request

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

def fetch_page(url, encoding='gbk'):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    try:
        return raw.decode(encoding)
    except:
        return raw.decode('utf8', errors='replace')

def get_matches_from_wanchang(date_str):
    url = f'https://live.500.com/wanchang.php?e={date_str}'
    html = fetch_page(url)
    m = re.search(r'id="table_match"[^>]*>(.*?)</table>', html, re.DOTALL)
    content = m.group(1) if m else ''
    rows = re.findall(r'<tr[^>]*>.*?</tr>', content, re.DOTALL)
    matches = []
    for row in rows[1:]:
        fid_m = re.search(r'ouzhi-(\d+)\.shtml', row)
        if not fid_m:
            continue
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row)
        if len(cells) < 6:
            continue
        match = {
            'fid': fid_m.group(1),
            'league': re.sub(r'<[^>]+>', '', cells[0]).strip(),
            'time': re.sub(r'<[^>]+>', '', cells[2]).strip(),
            'home': re.sub(r'\[?\d+\]?', '', re.sub(r'<[^>]+>', '', cells[3])).strip(),
            'away': re.sub(r'\[?\d+\]?', '', re.sub(r'<[^>]+>', '', cells[5])).strip(),
        }
        matches.append(match)
    return matches

def fetch_ouzhi_score(fid):
    url = f'https://odds.500.com/fenxi/ouzhi-{fid}.shtml'
    html = fetch_page(url)
    m = re.search(r'odds_hd_bf[^>]*><strong>([^<]+)</strong>', html)
    if m:
        return fid, m.group(1).replace(':', '-')
    return fid, None

# Step 1: Fetch wanchang
print("=== Step 1: wanchang.php ===")
t0 = time.time()
matches = get_matches_from_wanchang('2026-07-04')
t1 = time.time()
print(f"  {len(matches)} matches in {t1-t0:.2f}s")

# Find matches that have likely finished (time like 07-04 HH:MM where HH:MM < now)
# But we just need to check any 5 completed matches for timing
print()
print("=== Step 2: Sequential ouzhi fetches (5 completed matches) ===")
known_fids = ['1359197', '1359196', '1362693', '1359194']  # known completed matches
t0 = time.time()
for fid in known_fids:
    fid, score = fetch_ouzhi_score(fid)
    print(f"  fid={fid}: score={score}")

t1 = time.time()
avg = (t1 - t0) / len(known_fids)
print(f"\n  Average: {avg:.2f}s per page")

# Now test 15 sequential from wanchang list to find scores
print()
print("=== Step 3: Scan 20 matches from wanchang for scores ===")
# Pick matches with early times (likely completed)
sorted_matches = sorted(matches, key=lambda m: m['time'])
early = [m for m in sorted_matches if m['time'].startswith('07-04 00:') or m['time'].startswith('07-04 01:') or m['time'].startswith('07-04 02:')]
print(f"  Early matches (00-02): {len(early)}")

t0 = time.time()
found_scores = 0
for i, m in enumerate(early[:20]):
    fid, score = fetch_ouzhi_score(m['fid'])
    if score:
        found_scores += 1
        print(f"  {i+1}. {m['home']} vs {m['away']} ({m['time']}): {score}")
    else:
        print(f"  {i+1}. {m['home']} vs {m['away']} ({m['time']}): no score yet")
    time.sleep(0.3)  # rate limit

t1 = time.time()
print(f"\n  Found: {found_scores}/20")
print(f"  Time: {t1-t0:.2f}s (with 0.3s delay)")

# Projection
print()
print("=== Projection for today ===")
# How many matches have likely finished?
# Count matches with time < now
from datetime import datetime
now = datetime.now()
beijing_now = f"07-04 {now.hour:02d}:{now.minute:02d}"
likely_completed = [m for m in sorted_matches 
                    if m['time'] < beijing_now and m['time'].startswith('07-04')]
print(f"  Likely completed (time < now): {len(likely_completed)}")
print(f"  Matches needing check: {len(likely_completed)}")
print(f"  Estimated time (0.3s delay): {len(likely_completed)*0.5:.0f}s = {len(likely_completed) * 0.5 / 60:.1f}min")
