#!/usr/bin/env python3
"""
Analyze same-direction diff thresholds.
Probabilities are stored as decimals (0-1 scale), need ×100.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

cursor = conn.cursor()

# Count total with both data
cursor.execute("SELECT COUNT(*) FROM poisson_predictions WHERE fusion_win IS NOT NULL AND fusion_loss IS NOT NULL AND poisson_win IS NOT NULL AND poisson_loss IS NOT NULL")
total_all = cursor.fetchone()[0]
print(f"Total matches with both data: {total_all}")

# Count where at least one row has actual_outcome not null
cursor.execute("SELECT COUNT(*) FROM poisson_predictions WHERE actual_outcome IS NOT NULL AND fusion_win IS NOT NULL AND poisson_win IS NOT NULL")
total_result = cursor.fetchone()[0]
print(f"Matches with result: {total_result}")

# Get all matches with both data and actual result
cursor.execute("""
    SELECT * FROM poisson_predictions 
    WHERE fusion_win IS NOT NULL AND fusion_draw IS NOT NULL AND fusion_loss IS NOT NULL
      AND poisson_win IS NOT NULL AND poisson_draw IS NOT NULL AND poisson_loss IS NOT NULL
      AND actual_outcome IS NOT NULL
""")
rows = cursor.fetchall()
print(f"Matches with both data AND result: {len(rows)}")

# Process
records = []
for row in rows:
    poisson_probs = {'主': row['poisson_win'] * 100, '客': row['poisson_loss'] * 100, '平': row['poisson_draw'] * 100}
    fusion_probs = {'主': row['fusion_win'] * 100, '客': row['fusion_loss'] * 100, '平': row['fusion_draw'] * 100}
    
    poisson_dir = max(poisson_probs, key=poisson_probs.get)
    fusion_dir = max(fusion_probs, key=fusion_probs.get)
    fusion_max = fusion_probs[fusion_dir]
    poisson_same = poisson_probs[fusion_dir]
    diff = round(fusion_max - poisson_same, 1)
    
    outcome_cn = row['actual_outcome']
    outcome_dir = None
    if outcome_cn.startswith('主胜'):
        outcome_dir = '主'
    elif outcome_cn.startswith('客胜'):
        outcome_dir = '客'
    elif outcome_cn.startswith('平局'):
        outcome_dir = '平'
    
    records.append({
        'poisson_dir': poisson_dir,
        'fusion_dir': fusion_dir,
        'same_dir': poisson_dir == fusion_dir,
        'fusion_max': fusion_max,
        'poisson_same': poisson_same,
        'diff': diff,
        'outcome_dir': outcome_dir,
        'hit': (fusion_dir == outcome_dir) if (poisson_dir == fusion_dir and outcome_dir) else None
    })

same_dir = [r for r in records if r['same_dir'] and r['hit'] is not None]
print(f"Same-direction matches with outcome: {len(same_dir)}")

# Show some sample diff values
diff_vals = sorted([r['diff'] for r in same_dir], reverse=True)
print(f"Diff range: {diff_vals[0]:.1f}% (max) to {diff_vals[-1]:.1f}% (min)")
print(f"Top 20 diffs: {diff_vals[:20]}")
print()

print("=" * 65)
print("  差值阈值   场次   命中   命中率")
print("=" * 65)
for thresh in range(0, 51):
    filtered = [r for r in same_dir if r['diff'] >= thresh]
    total = len(filtered)
    if total == 0:
        continue
    hits = sum(1 for r in filtered if r['hit'])
    rate = hits / total * 100
    marker = " ★" if rate >= 75 else ""
    if thresh % 2 == 0 or rate >= 70:
        print(f"  ≥{thresh:>2}%    {total:>4}场   {hits:>3}场   {rate:>5.1f}%{marker}")

print()
print("=" * 65)
print("  目标命中率 → 所需最低差值门槛")
print("=" * 65)
for target in [60, 62, 65, 68, 70, 72, 75, 78, 80, 82, 85, 88, 90]:
    found = False
    for thresh in range(0, 61):
        filtered = [r for r in same_dir if r['diff'] >= thresh]
        total = len(filtered)
        if total == 0:
            continue
        hits = sum(1 for r in filtered if r['hit'])
        rate = hits / total * 100
        if rate >= target:
            print(f"  目标≥{target}%  →  差值≥{thresh}%:  {total}场  {hits}场命中  实际{rate:.1f}%")
            found = True
            break
    if not found:
        print(f"  目标≥{target}%  →  无解（所有数据都无法达到）")

conn.close()
