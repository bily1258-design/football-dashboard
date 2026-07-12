import json, sys

with open('docs/data/results.json') as f:
    d = json.load(f)
print(f'共 {d["total_matches"]} 场比赛')
print(f'日期范围: {d["date_range"]}')
if d["total_matches"] == 0:
    sys.exit(1)
