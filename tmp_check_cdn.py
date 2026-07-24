import json, sys
# Read stdin as raw bytes, decode
raw = sys.stdin.buffer.read()
d = json.loads(raw.decode('utf-8'))
matches = d.get('matches', [])
ah = sum(1 for m in matches if m.get('ah_home_covers_prob') is not None)
print(f"场次: {len(matches)}  AH: {ah}")
for m in matches:
    if m.get('ah_home_covers_prob') is not None:
        print({k:m[k] for k in ['home_team','away_team','score','ah_home_covers_prob','ah_handicap'] if k in m})
        break
