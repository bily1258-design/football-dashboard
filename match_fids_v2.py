#!/usr/bin/env python3
"""For each unmatched match, fetch the exact 500.com page and find the team"""
import urllib.request, re, sqlite3, json, time

# Load matched fids to skip
with open('matched_fids.json') as f:
    matched_list = json.load(f)
matched_mids = {m[0] for m in matched_list}

db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT id, date, home_team, away_team, league FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' ORDER BY date, id")
missing = [r for r in cur.fetchall() if r[0] not in matched_mids]

def fetch_wanchang(date_str):
    url = f"https://live.500.com/wanchang.php?e={date_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    chunks = []
    while True:
        try:
            chunk = resp.read(65536)
            if not chunk: break
            chunks.append(chunk)
        except: break
    data = b"".join(chunks).decode("gb2312", errors="ignore")
    return re.findall(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]+)"', data)

# Group by date
from collections import defaultdict
by_date = defaultdict(list)
for row in missing:
    by_date[row[1][:10]].append(row)

print(f"Unmatched matches: {len(missing)} across {len(by_date)} dates")

import sys
new_matches = []  # (id, match_id, fid)

# For each date with unmatched matches, fetch the page
for date_str, rows in sorted(by_date.items()):
    try:
        matches_500 = fetch_wanchang(date_str)
    except Exception as e:
        print(f"[{date_str}] Fetch error: {e}")
        continue
    
    # Index: normalized team name -> (fid, league, home, away)
    # For each 500 match, store
    for fid, gy in matches_500:
        parts = gy.split(",")
        if len(parts) >= 3:
            pass  # just need the list
    
    for row in rows:
        mid, dt, home, away, league = row
        
        best_fid = None
        best_score = 0
        best_h500 = best_a500 = ""
        
        for fid, gy in matches_500:
            parts = gy.split(",")
            if len(parts) < 3: continue
            l500, h500, a500 = parts[0], parts[1], parts[2]
            
            # Check if any common chars
            home_common = len(set(home) & set(h500))
            away_common = len(set(away) & set(a500))
            home_common_r = home_common / max(len(set(home)), 1)
            away_common_r = away_common / max(len(set(away)), 1)
            
            # Cross check too
            home_common_cross = len(set(home) & set(a500))
            away_common_cross = len(set(away) & set(h500))
            
            score = max(home_common_r + away_common_r, home_common_cross + away_common_cross)
            
            if score > best_score:
                best_score = score
                best_fid = fid
                best_h500 = h500
                best_a500 = a500
        
        if best_fid and best_score >= 0.8:
            new_matches.append((mid, row[1], best_fid))
            print(f"  ✅ {date_str} {league}: {home} vs {away} -> fid={best_fid} (score={best_score:.2f}) [{best_h500} vs {best_a500}]")
        elif best_fid and best_score >= 0.5:
            print(f"  🤔 {date_str} {league}: {home} vs {away} -> fid={best_fid} (score={best_score:.2f}) [{best_h500} vs {best_a500}]")
        else:
            print(f"  ❌ {date_str} {league}: {home} vs {away} (best={best_score:.2f})")

# Save new matches
if new_matches:
    with open('matched_fids_v2.json', 'w') as f:
        json.dump(new_matches, f, ensure_ascii=False)
    print(f"\nNew matches: {len(new_matches)}")
