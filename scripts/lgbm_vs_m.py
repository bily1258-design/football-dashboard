#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LGBM vs M 方向冲突时, 最终比分落在哪个模型方向?"""
import json, re
from collections import defaultdict

with open('docs/data/results.json', encoding='utf-8') as f:
    matches = json.load(f)['matches']

def parse_score(s):
    if not s: return None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else None

rows = []
for m in matches:
    st = parse_score(m.get('score'))
    if st is None: continue
    h, a = st
    actual = 'home' if h > a else ('draw' if h == a else 'away')
    mp = m.get('model_prediction')
    lp = m.get('lgbm_prediction')
    tp = m.get('ts_prediction') or m.get('ts_direction')
    if not mp or not lp: continue
    rows.append({'m': m, 'actual': actual, 'mp': mp, 'lp': lp, 'tp': tp,
                 'month': (m.get('match_time') or '')[:7]})

print(f'有比分且双模型都有预测: {len(rows)}')
agree = [r for r in rows if r['mp'] == r['lp']]
conflict = [r for r in rows if r['mp'] != r['lp']]
print(f'方向一致: {len(agree)}  ({len(agree)/len(rows)*100:.1f}%)')
print(f'方向冲突: {len(conflict)}  ({len(conflict)/len(rows)*100:.1f}%)')

def hit_stats(rs, which):
    """which='mp'|'lp'|'both'|'neither': 实际结果落在哪"""
    hits = 0; win3 = defaultdict(int)
    for r in rs:
        if r['actual'] == r['mp']: win3['M'] += 1
        if r['actual'] == r['lp']: win3['LGBM'] += 1
    return win3

print('\n=== 全部场次: 实际结果归属 ===')
w = hit_stats(rows, 'both')
for k in ['M', 'LGBM']:
    print(f'  {k}: {w.get(k,0):4d}场  {w.get(k,0)/len(rows)*100:.1f}%')

print('\n=== 方向冲突场次: 结果落在哪? ===')
wc = hit_stats(conflict, 'both')
n = len(conflict)
print(f'  冲突场次: {n}')
print(f'  → 跟M:     {wc.get("M",0):4d}场  {wc.get("M",0)/n*100:.1f}%')
print(f'  → 跟LGBM:  {wc.get("LGBM",0):4d}场  {wc.get("LGBM",0)/n*100:.1f}%')
both_hit = sum(1 for r in conflict if r['actual'] == r['mp'] and r['actual'] == r['lp'])
print(f'  → 结果不在两者: {n - wc.get("M",0) - wc.get("LGBM",0) + both_hit}场 (含都落空)')

print('\n=== 冲突场次按月稳定性 ===')
for month in sorted(set(r['month'] for r in conflict)):
    b = [r for r in conflict if r['month'] == month]
    wb = hit_stats(b, 'both')
    mrate = wb.get('M',0)/len(b)*100
    lrate = wb.get('LGBM',0)/len(b)*100
    print(f'  {month}: {len(b):3d}场  跟M {mrate:5.1f}%  跟LGBM {lrate:5.1f}%')

print('\n=== 冲突明细 (方向组合分布) ===')
comb = defaultdict(int)
for r in conflict:
    comb[(r['mp'], r['lp'])] += 1
for (mp, lp), c in sorted(comb.items(), key=lambda x: -x[1]):
    b = [r for r in conflict if r['mp'] == mp and r['lp'] == lp]
    wb = hit_stats(b, 'both')
    print(f'  M={mp} vs LGBM={lp}: {c:3d}场  跟M {wb.get("M",0):3d}({wb.get("M",0)/c*100:4.1f}%)  跟LGBM {wb.get("LGBM",0):3d}({wb.get("LGBM",0)/c*100:4.1f}%)')

# 顺带: TS vs M 冲突
print('\n=== 顺带: TS vs M 方向冲突 ===')
tconflict = [r for r in rows if r.get('tp') and r['tp'] != r['mp']]
if tconflict:
    wt = hit_stats(tconflict, 'both')
    n2 = len(tconflict)
    print(f'  冲突: {n2}场  跟M {wt.get("M",0)/n2*100:.1f}%  跟TS {wt.get("LGBM",0)/n2*100:.1f}%')
