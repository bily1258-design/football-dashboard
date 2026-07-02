#!/usr/bin/env python3
"""For each unmatched DB team, search 500.com names for closest char match"""
import json, sqlite3

with open('500_data.json') as f:
    raw = json.load(f)

all_500_teams = list(set(h for _,h,_,_ in raw) | set(a for _,_,a,_ in raw))

db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT DISTINCT home_team FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' UNION SELECT DISTINCT away_team FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = ''")
db_teams = sorted(set(r[0] for r in cur.fetchall()))

print(f"500.com unique teams: {len(all_500_teams)}")
print(f"DB teams to match: {len(db_teams)}")
print()

for db_team in db_teams:
    db_chars = set(db_team.replace(' ', ''))
    
    # For each DB team, show top matches
    scored = []
    for t500 in all_500_teams:
        t500_chars = set(t500.replace(' ', ''))
        common = db_chars & t500_chars
        if len(common) < 2:
            continue
        min_len = min(len(db_chars), len(t500_chars))
        ratio = len(common) / min_len if min_len > 0 else 0
        
        # Bonus for containing each other
        bonus = 0
        if db_team in t500 or t500 in db_team:
            bonus = 0.3
        
        total = ratio + bonus
        if total >= 0.6:
            scored.append((total, t500))
    
    scored.sort(reverse=True)
    best = scored[:2]
    if best:
        print(f"  '{db_team}' -> ", end="")
        print(" | ".join(f"'{t}' ({s:.2f})" for s, t in best))
    else:
        print(f"  '{db_team}' -> (no match found)")
