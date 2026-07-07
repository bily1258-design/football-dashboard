#!/usr/bin/env python3
"""Re-analyze using final_* columns (the correct fusion data)"""
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT * FROM poisson_predictions 
    WHERE final_win IS NOT NULL AND final_draw IS NOT NULL AND final_loss IS NOT NULL
      AND poisson_win IS NOT NULL AND poisson_draw IS NOT NULL AND poisson_loss IS NOT NULL
""")
rows = cur.fetchall()
print(f"总场次(同时有泊松+final数据): {len(rows)}")

with_result = [r for r in rows if r['actual_outcome'] is not None]
print(f"有开奖结果: {len(with_result)}")

records = []
for row in with_result:
    p = {'主': row['poisson_win']*100, '客': row['poisson_loss']*100, '平': row['poisson_draw']*100}
    f = {'主': row['final_win']*100, '客': row['final_loss']*100, '平': row['final_draw']*100}
    
    p_dir = max(p, key=p.get)
    f_dir = max(f, key=f.get)
    f_max = f[f_dir]
    p_same = p[f_dir]
    diff = round(f_max - p_same, 1)
    
    oc = row['actual_outcome']
    o_dir = '主' if oc.startswith('主胜') else ('客' if oc.startswith('客胜') else ('平' if oc.startswith('平局') else None))
    
    if o_dir:
        records.append({
            'home': row['home_team'], 'away': row['away_team'],
            'p_dir': p_dir, 'f_dir': f_dir,
            'same': p_dir == f_dir,
            'f_max': f_max, 'p_same': p_same,
            'diff': diff, 'o_dir': o_dir,
            'hit': 1 if f_dir == o_dir else 0
        })

same = [r for r in records if r['same']]
print(f"同向且有结果: {len(same)}")

diffs = sorted([r['diff'] for r in same], reverse=True)
print(f"差值范围: {diffs[0]:.1f}% ~ {diffs[-1]:.1f}%")
print(f"最大20个差值: {diffs[:20]}")
print()

# Show all 7 matches for 2026-07-07
print("=" * 60)
print("  【2026-07-07 七场比赛验证】")
print("=" * 60)
for r in records:
    if '7/7/2026' in str(r) or True:  # We'll just check the date separately
        pass
# Actually let's print the 7 specific matches
print(f"{'比赛':<30} {'泊松方向':>8} {'综合方向':>8} {'同向':>4} {'差值':>6} {'结果':>8} {'命中':>4}")
print("-" * 75)
for r in records:
    if r['home'] in ['布鲁马波','赫根','葡萄牙','凯夫拉维克','博塔弗戈SP','维拉诺瓦','美国']:
        same_str = "是" if r['same'] else "否"
        hit_str = "✅" if r['hit'] else "❌"
        print(f"{r['home']+'vs'+r['away']:<30} {r['p_dir']:>8} {r['f_dir']:>8} {same_str:>4} {r['diff']:>+5.1f}% {r['o_dir']:>8} {hit_str:>4}")

print()

# Threshold analysis
print("=" * 60)
print("  【同向差值门槛扫描(final列正确数据)】")
print("=" * 60)
print(f"{'差值≥':>6} {'场次':>5} {'命中':>5} {'命中率':>7} {'日均':>5}")
print("-" * 35)
for th in range(-10, 41):
    filtered = [r for r in same if r['diff'] >= th]
    total = len(filtered)
    if total < 3:
        continue
    hits = sum(1 for r in filtered if r['hit'])
    rate = hits / total * 100
    mark = " ★" if rate >= 75 else ""
    if th % 1 == 0:
        print(f" ≥{th:>2}%  {total:>4}场  {hits:>4}场  {rate:>5.1f}%  {total/43:>4.1f}{mark}")

print()

print("=" * 60)
print("  【目标命中率→所需差值门槛】")
print("=" * 60)
for target in [60, 62, 65, 68, 70, 72, 75, 78, 80, 82, 85, 88, 90]:
    found = False
    for th in range(-10, 61):
        filtered = [r for r in same if r['diff'] >= th]
        total = len(filtered)
        if total == 0: continue
        hits = sum(1 for r in filtered if r['hit'])
        rate = hits / total * 100
        if rate >= target:
            print(f"  目标≥{target}%  →  差值≥{th}%:  {total}场  {hits}场命中  实际{rate:.1f}%")
            found = True
            break
    if not found:
        print(f"  目标≥{target}%  →  无解（达不到）")

conn.close()
