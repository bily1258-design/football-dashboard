#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LGBM vs M: 都用最大概率方向 (argmax), 冲突时结果跟谁?"""
import json, re
from collections import defaultdict

with open('docs/data/results.json', encoding='utf-8') as f:
    matches = json.load(f)['matches']

def parse_score(s):
    if not s: return None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else None

def argmax3(w, d, l):
    mx = max(w, d, l)
    return ['home', 'draw', 'away'][0 if mx == w else (1 if mx == d else 2)]

CN = {'home': '主胜', 'draw': '平局', 'away': '客胜'}
rows = []
for m in matches:
    st = parse_score(m.get('score'))
    if st is None: continue
    h, a = st
    actual = 'home' if h > a else ('draw' if h == a else 'away')
    mw, md, ml = m.get('model_win', 0) or 0, m.get('model_draw', 0) or 0, m.get('model_loss', 0) or 0
    lw, ld, ll = m.get('lgbm_win', 0) or 0, m.get('lgbm_draw', 0) or 0, m.get('lgbm_loss', 0) or 0
    if mw + md + ml == 0 or lw + ld + ll == 0: continue
    rows.append({'m': m, 'actual': actual,
                 'mp': argmax3(mw, md, ml), 'mp_prob': max(mw, md, ml),
                 'lp': argmax3(lw, ld, ll), 'lp_prob': max(lw, ld, ll),
                 'month': (m.get('match_time') or '')[:7]})

print(f'有效场次: {len(rows)}')
agree = [r for r in rows if r['mp'] == r['lp']]
conflict = [r for r in rows if r['mp'] != r['lp']]
print(f'方向一致: {len(agree)} ({len(agree)/len(rows)*100:.1f}%)')
print(f'方向冲突: {len(conflict)} ({len(conflict)/len(rows)*100:.1f}%)')

def hit(rs):
    mh = sum(1 for r in rs if r['actual'] == r['mp'])
    lh = sum(1 for r in rs if r['actual'] == r['lp'])
    return mh, lh

print('\n=== 全部场次 (最大概率方向) ===')
mh, lh = hit(rows)
print(f'  M(最大概率): {mh:4d}场 {mh/len(rows)*100:.1f}%')
print(f'  LGBM:         {lh:4d}场 {lh/len(rows)*100:.1f}%')

print('\n=== 方向冲突场次: 结果跟谁? ===')
n = len(conflict)
mh, lh = hit(conflict)
print(f'  → 跟M:     {mh:4d}场 {mh/n*100:.1f}%')
print(f'  → 跟LGBM:  {lh:4d}场 {lh/n*100:.1f}%')

print('\n=== 冲突按月稳定性 ===')
for month in sorted(set(r['month'] for r in conflict)):
    b = [r for r in conflict if r['month'] == month]
    mh2, lh2 = hit(b)
    print(f'  {month}: {len(b):3d}场  跟M {mh2/len(b)*100:5.1f}%  跟LGBM {lh2/len(b)*100:5.1f}%')

print('\n=== 冲突时 M最大概率值分档 ===')
for lo, hi in [(0.25,0.35),(0.35,0.45),(0.45,0.55),(0.55,1.01)]:
    b = [r for r in conflict if lo <= r['mp_prob'] < hi]
    if not b: continue
    mh2, lh2 = hit(b)
    print(f'  M概率[{lo:.0%},{hi:.0%}): {len(b):3d}场  跟M {mh2/len(b)*100:5.1f}%  跟LGBM {lh2/len(b)*100:5.1f}%')

print('\n=== 冲突明细 (方向组合) ===')
comb = defaultdict(int)
for r in conflict: comb[(r['mp'], r['lp'])] += 1
for (mp, lp), c in sorted(comb.items(), key=lambda x: -x[1]):
    b = [r for r in conflict if r['mp'] == mp and r['lp'] == lp]
    mh2, lh2 = hit(b)
    print(f'  M={CN[mp]} vs LGBM={CN[lp]}: {c:3d}场  跟M {mh2:3d}({mh2/c*100:4.1f}%)  跟LGBM {lh2:3d}({lh2/c*100:4.1f}%)')
