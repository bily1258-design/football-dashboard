#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三轮: 验证最强规律的时间稳定性 + 精确规则"""
import json, re

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

def stats(rows_, title, min_n=1):
    if len(rows_) < min_n: return None
    wins = sum(1 for r in rows_ if r['pred'] == r['actual'])
    profit = sum((r['odds']-1) if r['pred'] == r['actual'] else -1 for r in rows_)
    return {'n': len(rows_), 'wins': wins, 'wr': wins/len(rows_)*100, 'profit': round(profit,2), 'title': title}

def rule(r, hpts_lo, hpts_hi, hgd_lo, hgd_hi, agd_lo, agd_hi):
    """主队状态差 + 客队状态非负"""
    m = r['m']
    hpts = m.get('home_form_pts'); hgd = m.get('home_form_gd'); agd = m.get('away_form_gd')
    if hpts is None or hgd is None or agd is None: return False
    return hpts_lo <= float(hpts) < hpts_hi and hgd_lo <= float(hgd) < hgd_hi and agd_lo <= float(agd) < agd_hi

RULES = {
    'R1 客胜+主队积分低': lambda r: rule(r, -3, 0.75, -10, 10, -10, 10),
    'R2 客胜+主队净胜球负': lambda r: rule(r, -10, 10, -3, 0, -10, 10),
    'R3 客胜+主队积分低+客队GD非负': lambda r: rule(r, -3, 0.75, -10, 10, 0, 10),
    'R4 客胜+主队GD负+客队GD非负': lambda r: rule(r, -10, 10, -3, 0, 0, 10),
    'R5 客胜+主队积分低+主队GD负': lambda r: rule(r, -3, 0.75, -3, 0, -10, 10),
}

print('=== 规则验证 (全期) ===')
for name, fn in RULES.items():
    b = [r for r in rows if r['pred'] == 'away' and fn(r)]
    s = stats(b, name)
    if s: print(f"  {s['title']}: {s['n']:4d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")

print('\n=== 时间稳定性 (按月) ===')
for name, fn in RULES.items():
    print(f'  {name}:')
    for month in ['2026-06', '2026-07', '2026-08']:
        b = [r for r in rows if r['pred'] == 'away' and fn(r) and (r['m'].get('match_time') or '').startswith(month)]
        s = stats(b, month)
        if s: print(f"    {month}: {s['n']:3d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")
        else: print(f"    {month}: 0场")

print('\n=== 最强规则 R4 的赔率分布 ===')
b = [r for r in rows if r['pred'] == 'away' and RULES['R4 客胜+主队GD负+客队GD非负'](r)]
for lo, hi in [(1.5,2.5),(2.5,3.5),(3.5,4.5),(4.5,6.0)]:
    bb = [r for r in b if lo <= r['odds'] < hi]
    s = stats(bb, f'odds[{lo},{hi})')
    if s: print(f"  odds[{lo},{hi}): {s['n']:3d}场 胜率{s['wr']:5.1f}% 利润{s['profit']:+7.2f}")

print('\n=== R4 场次明细(最近15场) ===')
for r in sorted(b, key=lambda x: x['m'].get('match_time',''), reverse=True)[:15]:
    m = r['m']
    print(f"  {m.get('match_time','')[:10]} {m.get('home_team','')[:10]} vs {m.get('away_team','')[:10]} 客胜@{r['odds']:.2f} 实际:{'✓' if r['pred']==r['actual'] else '✗'}")
