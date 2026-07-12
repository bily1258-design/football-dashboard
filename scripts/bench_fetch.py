#!/usr/bin/env python3
"""Benchmark: wanchang.php + concurrent ouzhi fetching"""
import time, re, json, urllib.request, concurrent.futures

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
            'score': re.sub(r'<[^>]+>', '', cells[4]).strip(),
            'away': re.sub(r'\[?\d+\]?', '', re.sub(r'<[^>]+>', '', cells[5])).strip(),
        }
        matches.append(match)
    return matches

def fetch_ouzhi_score(fid):
    url = f'https://odds.500.com/fenxi/ouzhi-{fid}.shtml'
    html = fetch_page(url)
    m = re.search(r'odds_hd_bf[^>]*><strong>([^<]+)</strong>', html)
    if m:
        score = m.group(1).replace(':', '-')
        return fid, score
    return fid, None

# --- Benchmark ---
print("=== Step 1: Fetch wanchang.php (one request) ===")
t0 = time.time()
matches = get_matches_from_wanchang('2026-07-04')
t1 = time.time()
print(f"  Time: {t1-t0:.2f}s")
print(f"  Matches: {len(matches)}")

# Stats
today_matches = [m for m in matches if m['time'].startswith('07-04')]
dash_scores = [m for m in today_matches if m['score'] == '-']
actual_scores = [m for m in today_matches if m['score'] != '-']
print(f"  Today (07-04): {len(today_matches)}")
print(f"  Dash scores: {len(dash_scores)}")
print(f"  Non-dash: {len(actual_scores)}")
if actual_scores:
    print(f"  Sample: {[(m['home'], m['score'], m['away']) for m in actual_scores[:5]]}")

# Filter matches where time has passed (potential candidates for score checking)
candidates = [m for m in today_matches if m['score'] == '-']
print(f"\n  Candidates needing score check: {len(candidates)}")

# --- Step 2: Batch fetch scores from ouzhi pages ---
print(f"\n=== Step 2: Batch fetch scores (first 15 candidates) ===")
test_candidates = candidates[:15]
t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results = list(executor.map(fetch_ouzhi_score, [m['fid'] for m in test_candidates]))
t1 = time.time()
print(f"  Time: {t1-t0:.2f}s for {len(test_candidates)} matches")
found = [r for r in results if r[1] is not None]
print(f"  Scores found: {len(found)}/{len(test_candidates)}")
for fid, score in found[:5]:
    match = next(m for m in test_candidates if m['fid'] == fid)
    print(f"    {match['home']} vs {match['away']}: {score}")

# Projection for all candidates
total_candidates = len(candidates)
est_time = (t1-t0) * total_candidates / len(test_candidates) if test_candidates else 0
print(f"\n=== Projection ===")
print(f"  Total candidates: {total_candidates}")
print(f"  Estimated time (10 workers): {est_time:.1f}s")
print(f"  With 5 workers: {est_time * 2:.1f}s")
print(f"  With 20 workers: {est_time / 2:.1f}s")
