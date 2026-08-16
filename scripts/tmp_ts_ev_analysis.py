#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TS反向强度 x EV 交叉分析 (8月主主客)"""
import json

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


rows = []
for m in matches:
    sc = m.get('score') or ''
    if not sc or '-' not in sc:
        continue
    mt = m.get('match_time', '')
    if not mt.startswith('2026-08'):
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

print(f'8月主主客(带赔率): {len(rows)}场')


def report(name, sub):
    if len(sub) < 5:
        print(f'{name}: {len(sub)}场(样本不足)')
        return
    w = sum(1 for r in sub if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
    print(f'{name}: {len(sub)}场 胜率{w/len(sub):.1%} 盈亏{p:+.2f} ROI{p/len(sub):+.2%}')


print()
print('=== 1. TS反向强度 x EV 交叉表 (ROI%) ===')
ev_buckets = [(-9, 0, '<0'), (0, 0.1, '0-0.1'), (0.1, 0.3, '0.1-0.3'), (0.3, 9, '>0.3')]
ts_buckets = [(0, 0.3, 'TS<30%'), (0.3, 0.4, 'TS30-40%'), (0.4, 0.5, 'TS40-50%'), (0.5, 1.01, 'TS>=50%')]
hdr = 'TS\\EV'.ljust(12) + ''.join(f'{name:>10}' for _, _, name in ev_buckets) + '合计'.rjust(8)
print(hdr)
for ts_lo, ts_hi, tsname in ts_buckets:
    line = f'{tsname:<12}'
    for ev_lo, ev_hi, _ in ev_buckets:
        sub = [r for r in rows if ts_lo <= r['tsl'] < ts_hi and ev_lo <= r['ev'] < ev_hi]
        if len(sub) < 5:
            line += f' {"-":>10}'
        else:
            w = sum(1 for r in sub if r['won'])
            p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
            line += f' {p/len(sub)*100:>8.1f}%'
    sub = [r for r in rows if ts_lo <= r['tsl'] < ts_hi]
    if sub:
        w = sum(1 for r in sub if r['won'])
        p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
        line += f' {p/len(sub)*100:>6.1f}%'
    else:
        line += '    -'
    print(line)

print()
print('=== 2. EV 区间(全量主主客) ===')
for ev_lo, ev_hi, name in [(-9, 0, 'EV<0'), (0, 0.1, 'EV0-0.1'), (0.1, 0.2, 'EV0.1-0.2'),
                           (0.2, 0.3, 'EV0.2-0.3'), (0.3, 9, 'EV>0.3')]:
    report(name, [r for r in rows if ev_lo <= r['ev'] < ev_hi])

print()
print('=== 3. 交叉最优角 ===')
report('EV>0 + TS>=40%', [r for r in rows if r['ev'] > 0 and r['tsl'] >= 0.4])
report('EV>0.05 + TS>=40%', [r for r in rows if r['ev'] > 0.05 and r['tsl'] >= 0.4])
report('EV<0 + TS>=40%', [r for r in rows if r['ev'] < 0 and r['tsl'] >= 0.4])
report('EV>0 + TS<40%', [r for r in rows if r['ev'] > 0 and r['tsl'] < 0.4])
report('EV>0.1 + TS>=40%', [r for r in rows if r['ev'] > 0.1 and r['tsl'] >= 0.4])
report('EV>0.2 + TS>=40%', [r for r in rows if r['ev'] > 0.2 and r['tsl'] >= 0.4])
