#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二轮: 客胜子集特征扫描 + 平局反向 + 双特征组合"""
import json, re
from itertools import combinations

with open('docs/data/results.json', encoding='utf-8') as f:
    matches = json.load(f)['matches']

def parse_score(s):
    if not s: return None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else None

DIRS = {'home': ('主胜', 'odds_win'), 'draw': ('平局', 'odds_draw'), 'away': ('客胜', 'odds_loss')}
rows = []
for m in matches:
    st = parse_score(m.get('score'))
    if st is None: continue
    h, a = st
    actual = 'home' if h > a else ('draw' if h == a else 'away')
    pred = m.get('model_prediction') or m.get('prediction')
    if pred not in DIRS: continue
    odds = m.get(DIRS[pred][1])
    if not odds or float(odds) <= 1.0: continue
    rows.append({'m': m, 'actual': actual, 'pred': pred, 'odds': float(odds)})

def stats(rows_, title, min_n=15):
    if len(rows_) < min_n: return None
    wins = sum(1 for r in rows_ if r['pred'] == r['actual'])
    profit = sum((r['odds']-1) if r['pred'] == r['actual'] else -1 for r in rows_)
    return {'n': len(rows_), 'wins': wins, 'wr': wins/len(rows_)*100, 'profit': round(profit,2), 'title': title}

# ── 客胜子集: 单特征扫描 ──
away = [r for r in rows if r['pred'] == 'away']
print(f'=== 客胜子集基准 === {len(away)}场')
s = stats(away, 'away-all')
print(f"  {s['n']}场 胜率{s['wr']:.1f}% 利润{s['profit']:+.2f}")

FEATS = {
    'model_prediction_prob': (0.2, 0.8, 4),
    'importance_weight': (0, 2.0, 4),
    'margin': (0, 1.0, 5),
    'diff_lgb_poisson': (-1, 1, 4),
    'lgbm_entropy': (0, 2, 4),
    'home_form_gd': (-6, 6, 4),
    'away_form_gd': (-6, 6, 4),
    'home_form_pts': (-3, 12, 4),
    'away_form_pts': (-3, 12, 4),
    'odds_loss': (1.5, 6.0, 5),
    'lgbm_confidence': (0, 1, 5),
    'raw_entropy': (0, 2, 4),
}
print('\n--- 客胜 × 特征分桶 (利润排序) ---')
scanned = []
for feat, (lo, hi, nb) in FEATS.items():
    for i in range(nb):
        f_lo = lo + (hi-lo)*i/nb
        f_hi = lo + (hi-lo)*(i+1)/nb
        b = [r for r in away if (r['m'].get(feat) is not None) and f_lo <= float(r['m'][feat]) < f_hi]
        s = stats(b, f'{feat}[{f_lo:.2f},{f_hi:.2f})', min_n=10)
        if s: scanned.append(s)
for s in sorted(scanned, key=lambda x: -x['profit'])[:12]:
    print(f"  {s['title']:32s} {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")

# ── 平局预测子集: 反向买 (买非平局) ──
draw = [r for r in rows if r['pred'] == 'draw']
print(f'\n=== 平局预测子集 === {len(draw)}场')
s = stats(draw, 'draw-all')
print(f"  顺买(平局): 胜率{s['wr']:.1f}% 利润{s['profit']:+.2f}")
# 反向: 买主胜/客胜中赔率较低的一方
reverse = []
for r in draw:
    m = r['m']
    ow, ol = float(m.get('odds_win') or 0), float(m.get('odds_loss') or 0)
    if ow <= 0 or ol <= 0: continue
    if ow <= ol:
        pred, odds = 'home', ow
    else:
        pred, odds = 'away', ol
    reverse.append({'m': m, 'actual': r['actual'], 'pred': pred, 'odds': odds})
s2 = stats(reverse, '反向买低赔', min_n=10)
if s2: print(f"  反买(低赔方): {s2['n']}场 胜率{s2['wr']:.1f}% 利润{s2['profit']:+.2f}")

# ── 客胜 × 双特征组合 (每特征选2桶) ──
print('\n=== 客胜 × 双特征组合 (Top15) ===')
combos = []
feat_vals = {}
for feat, (lo, hi, nb) in FEATS.items():
    buckets = []
    for i in range(nb):
        f_lo = lo + (hi-lo)*i/nb
        f_hi = lo + (hi-lo)*(i+1)/nb
        b = [r for r in away if (r['m'].get(feat) is not None) and f_lo <= float(r['m'][feat]) < f_hi]
        s = stats(b, '', min_n=8)
        if s: buckets.append((f'{feat}[{f_lo:.2f},{f_hi:.2f})', s))
    feat_vals[feat] = buckets

for f1, f2 in combinations(FEATS.keys(), 2):
    for t1, s1 in feat_vals[f1][:3]:
        for t2, s2 in feat_vals[f2][:3]:
            # 交集
            def in_bucket(r, feat, bucket_title):
                lo, hi = [float(x) for x in bucket_title[bucket_title.index('[')+1:-1].split(',')]
                v = r['m'].get(feat)
                return v is not None and lo <= float(v) < hi
            b = [r for r in away if in_bucket(r, f1, t1) and in_bucket(r, f2, t2)]
            s = stats(b, f'{t1}+{t2}', min_n=10)
            if s: combos.append(s)
for s in sorted(combos, key=lambda x: -x['profit'])[:15]:
    print(f"  {s['title'][:60]:60s} {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")
