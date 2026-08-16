#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W32+W33 (8/3-8/16) 最优参数挖掘"""
import json
from datetime import datetime, timedelta

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


def build(matches):
    rows = []
    for m in matches:
        sc = m.get('score') or ''
        if not sc or '-' not in sc:
            continue
        mt = m.get('match_time', '')
        if not ('2026-08-03' <= mt[:10] <= '2026-08-16'):
            continue
        md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        if not (md == '主' and ld == '主' and td == '客'):
            continue
        h, a = sc.split('-')
        try:
            h, a = int(h), int(a)
        except Exception:
            continue
        won = h > a
        odds = m.get('odds_win')
        if not odds:
            continue
        p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
        ev = p * odds - 1
        tsl = m.get('ts_loss', 0)
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'tsl': tsl, 'mt': mt[:10]})
    return rows


rows = build(matches)
print(f'W32+W33 主主客(带赔率): {len(rows)}场')


def report(name, sub):
    if len(sub) < 5:
        print(f'{name}: {len(sub)}场(样本不足)')
        return
    w = sum(1 for r in sub if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
    print(f'{name}: {len(sub)}场 胜率{w/len(sub):.1%} 盈亏{p:+.2f} ROI{p/len(sub):+.2%}')


print()
print('=== TS 阈值阶梯 ===')
for lo, hi in [(0.3, 0.4), (0.4, 0.45), (0.45, 0.5), (0.5, 1.01)]:
    report(f'TS {lo:.0%}-{hi:.0%}', [r for r in rows if lo <= r['tsl'] < hi])
report('TS>=45%', [r for r in rows if r['tsl'] >= 0.45])
report('TS>=40%', [r for r in rows if r['tsl'] >= 0.4])

print()
print('=== TS>=40% 内赔率分段 ===')
sub = [r for r in rows if r['tsl'] >= 0.4]
for lo, hi in [(1, 1.5), (1.5, 2), (2, 2.5), (2.5, 100)]:
    report(f'赔率{lo}-{hi}', [r for r in sub if lo <= r['odds'] < hi])

print()
print('=== TS>=40% 内 EV 分段 ===')
for lo, hi in [(-9, -0.2), (-0.2, -0.1), (-0.1, 0), (0, 9)]:
    report(f'EV{lo}-{hi}', [r for r in sub if lo <= r['ev'] < hi])

print()
print('=== TS>=40% 内 双模型均值分段 ===')
for lo, hi in [(0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)]:
    report(f'均值{lo:.0%}-{hi:.0%}', [r for r in sub if lo <= (r['ev'] + 1) / r['odds'] < hi])

print()
print('=== 组合 ===')
report('TS>=45% + 赔率1.5-2.5', [r for r in rows if r['tsl'] >= 0.45 and 1.5 <= r['odds'] <= 2.5])
report('TS>=40% + 赔率2-2.5', [r for r in rows if r['tsl'] >= 0.4 and 2 <= r['odds'] <= 2.5])
report('TS>=40% + 赔率1-2', [r for r in rows if r['tsl'] >= 0.4 and 1 <= r['odds'] < 2])
