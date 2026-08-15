#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统性找规律: 扫描单特征分桶 + 组合模式, 找正利润子集"""
import json, re, sys
from collections import defaultdict

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
print(f'有效场次: {len(rows)}')

def stats(rows_, title, min_n=20):
    if len(rows_) < min_n: return None
    wins = sum(1 for r in rows_ if r['pred'] == r['actual'])
    profit = sum((r['odds']-1) if r['pred'] == r['actual'] else -1 for r in rows_)
    return {'n': len(rows_), 'wins': wins, 'wr': wins/len(rows_)*100, 'profit': round(profit,2), 'title': title}

base = stats(rows, 'all')
print(f"\n=== 基准: 模型预测全量 === {base['n']}场 胜率{base['wr']:.1f}% 利润{base['profit']:+.2f}")

print('\n=== 模型概率分档 ===')
for lo, hi in [(0.3,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.7),(0.7,1.01)]:
    b = [r for r in rows if lo <= (r['m'].get('model_prediction_prob') or 0) < hi]
    s = stats(b, f'p[{lo:.0%},{hi:.0%})')
    if s: print(f"  {s['title']}: {s['n']}场 胜率{s['wr']:.1f}% 利润{s['profit']:+.2f}")

print('\n=== 单特征分桶扫描 ===')
FEATS = {
    'importance_weight': (0, 2.0, 4),
    'margin': (0, 1.0, 5),
    'diff_lgb_poisson': (-1, 1, 4),
    'lgbm_entropy': (0, 2, 4),
    'raw_entropy': (0, 2, 4),
    'lgbm_confidence': (0, 1, 5),
    'draw_confidence': (-3, 3, 4),
    'home_form_gd': (-6, 6, 4),
    'away_form_gd': (-6, 6, 4),
    'home_form_pts': (-3, 12, 4),
    'away_form_pts': (-3, 12, 4),
    'odds_win': (1.0, 3.0, 4),
    'odds_draw': (2.5, 6.0, 4),
    'odds_loss': (1.5, 6.0, 4),
}
scanned = []
for feat, (lo, hi, nb) in FEATS.items():
    for i in range(nb):
        f_lo = lo + (hi-lo)*i/nb
        f_hi = lo + (hi-lo)*(i+1)/nb
        b = [r for r in rows if (r['m'].get(feat) is not None) and f_lo <= float(r['m'][feat]) < f_hi]
        s = stats(b, f'{feat}[{f_lo:.2f},{f_hi:.2f})', min_n=25)
        if s: scanned.append(s)
print('  利润 Top10:')
for s in sorted(scanned, key=lambda x: -x['profit'])[:10]:
    print(f"  {s['title']:35s} {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")
print('  胜率 Top10:')
for s in sorted(scanned, key=lambda x: -x['wr'])[:10]:
    print(f"  {s['title']:35s} {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")

# 方向分解: 客胜 vs 主胜 vs 平局
print('\n=== 按预测方向 ===')
for d, (cn, _) in DIRS.items():
    b = [r for r in rows if r['pred'] == d]
    s = stats(b, cn, min_n=5)
    if s: print(f"  {s['title']}: {s['n']}场 胜率{s['wr']:.1f}% 利润{s['profit']:+.2f}")

# 组合: 高概率 + 方向
print('\n=== 组合: 概率分档 × 方向 ===')
for lo, hi in [(0.5,0.6),(0.6,0.7),(0.7,1.01)]:
    for d, (cn, _) in DIRS.items():
        b = [r for r in rows if (r['m'].get('model_prediction_prob') or 0) >= lo and (r['m'].get('model_prediction_prob') or 0) < hi and r['pred'] == d]
        s = stats(b, f'p[{lo:.0%},{hi:.0%})+{cn}', min_n=15)
        if s: print(f"  {s['title']:30s} {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")
