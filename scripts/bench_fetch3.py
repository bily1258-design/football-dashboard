#!/usr/bin/env python3
"""Find and check all likely-finished matches from wanchang"""
import time, re, json, urllib.request
from datetime import datetime

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

# Fetch wanchang
print("Fetching wanchang...")
t0 = time.time()
matches = get_matches_from_wanchang('2026-07-04')
print(f"  {len(matches)} matches in {time.time()-t0:.2f}s")

# Show time distribution
days = {}
times_set = set()
for m in matches:
    day = m['time'][:5]  # MM-DD
    days[day] = days.get(day, 0) + 1
    times_set.add(m['time'][6:11] if len(m['time']) > 5 else m['time'])

print(f"\nDate distribution: {json.dumps(dict(sorted(days.items())), ensure_ascii=False)}")

# Today's matches sorted by time
today = [m for m in matches if m['time'].startswith('07-04')]
today.sort(key=lambda m: m['time'])

print(f"\nToday's time slots (07-04):")
slots = {}
for m in today:
    h = m['time'][6:11]  # HH:MM
    slots[h] = slots.get(h, 0) + 1
for h, c in sorted(slots.items()):
    print(f"  {h}: {c} matches")

# Now check all matches that kicked off 2+ hours ago (likely finished)
beijing_now = datetime.now().strftime('%H:%M')  # server is UTC
print(f"\nCurrent time: {beijing_now} (Beijing)")
likely_completed = [m for m in today if m['time'][6:11] < '15:00']  # matches before 15:00
print(f"Matches before 15:00 Beijing: {len(likely_completed)}")
print(f"Matches before 14:00 Beijing: {len([m for m in today if m['time'][6:11] < '14:00'])}")

# Check top 30 by time
check = [m for m in today if m['time'][6:11] < beijing_now][:30]
print(f"\nChecking {len(check)} likely-completed matches...")
t0 = time.time()
found = []
for i, m in enumerate(check):
    fid, score = fetch_ouzhi_score(m['fid'])
    if score:
        found.append((m, score))
        print(f"  {i+1}/{len(check)}: {m['home']} vs {m['away']} ({m['time']}) = {score}")
    time.sleep(0.25)  # rate limit

t1 = time.time()
print(f"\nSummary:")
print(f"  Checked: {len(check)}")
print(f"  Found scores: {len(found)}")
print(f"  Time: {t1-t0:.2f}s")
print(f"  Avg: {(t1-t0)/len(check):.2f}s/match")
