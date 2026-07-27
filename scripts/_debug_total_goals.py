"""临时调试：检查预计算循环是否真正执行"""
import json, sys
sys.path.insert(0, '.')
import team_similarity

# Check the code at lines 804-834 to see if pre-pass is running
# Manually test the specific matches
with open('docs/data/results.json') as f:
    data = json.load(f)

matches = data['matches']
# Find specific matches
targets = []
for m in matches:
    h, a = m.get('home_team',''), m.get('away_team','')
    if '全南天龙' in h or '忠南牙山' in h or '忠南牙山' in a:
        targets.append((h, a, m.get('date','')[:10], 'total_goals_top3' in m))

print("Target matches and their total_goals_top3 status:")
for h, a, d, has_tg in targets:
    print(f"  {h} vs {a} ({d}): {'HAS' if has_tg else 'MISSING'} total_goals_top3")

# Now run the full function
print("\n\nRunning team_similarity.run() ...")
result = team_similarity.run()
print(f"\n\nrun() returned: {result}")

# Check again
with open('docs/data/results.json') as f:
    data = json.load(f)

matches = data['matches']
print("\n\nAfter run():")
for m in matches:
    h, a = m.get('home_team',''), m.get('away_team','')
    if '全南天龙' in h or '忠南牙山' in h or '忠南牙山' in a:
        has_tg = 'total_goals_top3' in m
        val = m.get('total_goals_top3', 'N/A')
        print(f"  {h} vs {a} ({m.get('date','')[:10]}): {'HAS' if has_tg else 'MISSING'} → {str(val)[:60]}")
