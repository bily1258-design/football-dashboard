#!/usr/bin/env python3
"""
Systematic analysis of confidence tiers using final_* (correct fusion data).
Explores multiple dimensions: diff thresholds, final_prob thresholds, direction agreement.
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT * FROM poisson_predictions 
    WHERE final_win IS NOT NULL AND final_draw IS NOT NULL AND final_loss IS NOT NULL
      AND poisson_win IS NOT NULL AND poisson_draw IS NOT NULL AND poisson_loss IS NOT NULL
      AND actual_outcome IS NOT NULL
""")
rows = cur.fetchall()

records = []
for row in rows:
    p = {'主':row['poisson_win']*100, '客':row['poisson_loss']*100, '平':row['poisson_draw']*100}
    f = {'主':row['final_win']*100, '客':row['final_loss']*100, '平':row['final_draw']*100}
    
    p_dir = max(p, key=p.get)
    f_dir = max(f, key=f.get)
    f_max = f[f_dir]
    p_max = p[p_dir]
    f_second = sorted(f.values(), reverse=True)[1]
    diff = round(f_max - p[f_dir], 1)  # final最高 - 泊松同方向
    
    oc = row['actual_outcome']
    o_dir = '主' if oc.startswith('主胜') else ('客' if oc.startswith('客胜') else '平')
    
    records.append({
        'same_dir': p_dir == f_dir,
        'p_dir': p_dir, 'f_dir': f_dir,
        'f_max': f_max, 'p_max': p_max, 'p_same': p[f_dir],
        'diff': diff,
        'f_lead': round(f_max - f_second, 1),  # final最高和次高的差距
        'o_dir': o_dir,
        'hit': 1 if f_dir == o_dir else 0,
        'home': row['home_team'], 'away': row['away_team']
    })

print(f"总场次: {len(records)}")
print()

# ============================================================
# 1. SAME-DIRECTION: diff threshold scan (detailed)
# ============================================================
print("=" * 65)
print("【一、同向场景：差值门槛详细扫描】")
print("=" * 65)

same = [r for r in records if r['same_dir']]
print(f"同向场次: {len(same)}")

print(f"\n{'差值≥':>6} {'场次':>6} {'命中':>6} {'命中率':>7} {'日均':>6} {'final最高平均':>12}")
print("-" * 45)
for th in range(-5, 31):
    filt = [r for r in same if r['diff'] >= th]
    if len(filt) < 3: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    avg_fmax = sum(r['f_max'] for r in filt)/len(filt)
    print(f" ≥{th:>2}%  {len(filt):>4}场 {hits:>4}场 {rate:>6.1f}% {len(filt)/43:>5.1f} {avg_fmax:>8.1f}%")

print()

# ============================================================
# 2. DIVERGENCE: final direction hit rate
# ============================================================
print("=" * 65)
print("【二、分歧场景：综合方向本身的命中率】")
print("=" * 65)
div = [r for r in records if not r['same_dir']]
print(f"分歧场次: {len(div)}")

div_hits = sum(r['hit'] for r in div)
print(f"分歧中final方向命中: {div_hits}/{len(div)} = {div_hits/len(div)*100:.1f}%")

# By diff in divergence (final - poisson same direction)
print(f"\n{'分歧+差值≥':>10} {'场次':>6} {'命中':>6} {'命中率':>7}")
print("-" * 35)
for th in range(0, 41):
    filt = [r for r in div if r['diff'] >= th]
    if len(filt) < 3: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    print(f" ≥{th:>2}%     {len(filt):>4}场 {hits:>4}场 {rate:>6.1f}%")

print()

# ============================================================
# 3. COMBINED: diff + final_prob threshold scan
# ============================================================
print("=" * 65)
print("【三、组合条件：差值+综合概率门槛】")
print("=" * 65)
print(f"\n{'final≥':>8} {'差值≥':>8} {'场次':>6} {'命中':>6} {'命中率':>7} {'日均':>6}")
print("-" * 50)

results = []
for f_th in [35, 38, 40, 42, 45, 48, 50, 52, 55, 58, 60]:
    for d_th in [0, 1, 2, 3, 5, 8, 10, 13, 15, 18]:
        # Check both same-direction and divergence
        if d_th > 0:
            filt = [r for r in records if r['f_max'] >= f_th and r['diff'] >= d_th and r['same_dir']]
        else:
            filt = [r for r in records if r['f_max'] >= f_th and r['diff'] >= d_th]
        
        if len(filt) < 10: continue
        hits = sum(r['hit'] for r in filt)
        rate = hits/len(filt)*100
        results.append((rate, len(filt), f_th, d_th, hits))

results.sort(key=lambda x: (-x[0], -x[1]))
for rate, total, f_th, d_th, hits in results[:25]:
    mark = " ★" if rate >= 72 else (" ◆" if rate >= 68 else "")
    print(f" ≥{f_th:>2}%    ≥{d_th:>2}%    {total:>4}场 {hits:>4}场 {rate:>6.1f}% {total/43:>5.1f}{mark}")

print()

# ============================================================
# 4. Pure final probability (no poisson comparison)
# ============================================================
print("=" * 65)
print("【四、只看综合概率(不做对比)】")
print("=" * 65)
print(f"\n{'final≥':>8} {'场次':>6} {'命中':>6} {'命中率':>7} {'日均':>6}")
print("-" * 40)
for th in range(30, 76):
    filt = [r for r in records if r['f_max'] >= th]
    if len(filt) < 5: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    print(f" ≥{th:>2}%  {len(filt):>4}场 {hits:>4}场 {rate:>6.1f}% {len(filt)/43:>5.1f}")

print()

# ============================================================
# 5. FINAL CONCLUSION: Best tier definitions
# ============================================================
print("=" * 65)
print("【五、推荐信心等级方案】")
print("=" * 65)

# Test various tier combinations
# Approach A: Pure final probability
print("\n--- 方案A：只看综合概率（最简方案）---")
for f_th in [70, 65, 62, 60, 58, 55, 52, 50, 48]:
    filt = [r for r in records if r['f_max'] >= f_th]
    if len(filt) < 5: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    print(f"  final≥{f_th}%: {len(filt)}场 {hits}命中 {rate:.1f}% ({len(filt)/43:.1f}/天)")

# Approach B: Same-direction + diff
print("\n--- 方案B：同向+差值（你发现的规律）---")
for d_th in [15, 13, 10, 9, 8, 5, 3, 2]:
    filt = [r for r in same if r['diff'] >= d_th]
    if len(filt) < 5: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    print(f"  同向+差值≥{d_th}%: {len(filt)}场 {hits}命中 {rate:.1f}% ({len(filt)/43:.1f}/天)")

# Approach C: ALL records (including divergence) + diff threshold
print("\n--- 方案C：全量+差值（包含分歧和同向）---")
for d_th in [15, 13, 10, 9, 8, 5, 3, 2, 1]:
    filt = [r for r in records if r['diff'] >= d_th]
    if len(filt) < 5: continue
    hits = sum(r['hit'] for r in filt)
    rate = hits/len(filt)*100
    print(f"  差值≥{d_th}%: {len(filt)}场 {hits}命中 {rate:.1f}% ({len(filt)/43:.1f}/天)")

conn.close()
