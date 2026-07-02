#!/usr/bin/env python3
"""Debug unmatched teams by checking what 500.com calls them"""
import urllib.request, re, sqlite3, json, sys

# Load all 500.com data
with open('500_data.json') as f:
    raw = json.load(f)

# Group by date: date -> [(home, away, fid)]
by_date = {}
for league_500, home_500, away_500, fid in raw:
    pass  # We don't have date in raw data

# Get missing matches
db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT id, date, home_team, away_team, league FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' ORDER BY date, id")
missing = cur.fetchall()
print(f"Total missing: {len(missing)}")

# For a subset of unmatched matches, check 500.com directly
# Pick the first 20 unique teams to investigate
team_names_found = set()
for row in missing:
    mid, dt, home, away, league = row
    for team in [home, away]:
        team_names_found.add(team)

print(f"Unique team names in missing matches: {len(team_names_found)}")
print("\n=== UNMATCHED TEAMS ===")

# For each missing match date, check what 500.com shows
for row in missing[:50]:
    mid, dt, home, away, league = row
    d = dt[:10]
    url = f"https://live.500.com/wanchang.php?e={d}"
    try:
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
        
        all_gy = re.findall(r'gy="([^"]+)"', data)
        
        # Find matches where team name partially matches
        candidates = []
        for gy in all_gy:
            parts = gy.split(",")
            if len(parts) >= 3:
                gy_text = "|".join(parts)
                if home[:2] in gy_text or away[:2] in gy_text:
                    candidates.append(gy)
        
        if candidates:
            print(f"\n[{d}] {league}: {home} vs {away}")
            for c in candidates[:3]:
                print(f"  500.com: {c}")
            # Also check what all unique team names are
            all_teams = set()
            for gy in all_gy:
                parts = gy.split(",")
                if len(parts) >= 3:
                    all_teams.add(parts[1])
                    all_teams.add(parts[2])
    except Exception as e:
        print(f"\n[{d}] {league}: {home} vs {away} -> ERROR: {e}")
