#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 TS反向强度 x EV 规律: 7月对照 + 客客主对照 + 官方best_value.ev"""
import json

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


def build(follow_main, month):
    """follow_main: True=主主客(跟主胜), False=客客主(跟客胜)"""
    rows = []
    for m in matches:
        sc = m.get('score') or ''
        if not sc or '-' not in sc:
            continue
        mt = m.get('match_time', '')
        if not mt.startswith(month):
            continue
        md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        if follow_main:
            if not (md == '主' and ld == '主' and td == '客'):
                continue
            tsl = m.get('ts_loss', 0)
            p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
            odds = m.get('odds_win')
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = h > a
        else:
            if not (md == '客' and ld == '客' and td == '主'):
                continue
            tsl = m.get('ts_win', 0)
            p = (m.get('model_loss', 0) + m.get('lgbm_loss', 0)) / 2
            odds = m.get('odds_loss')
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = a > h
        if not odds:
            continue
        ev = p * odds - 1
        bv = m.get('best_value') or {}
        bv_ev = bv.get('ev') if bv.get('signal') in ('value', 'ruleA') else None
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'bv_ev': bv_ev, 'tsl': tsl})
    return rows


def report(name, sub):
    if len(sub) < 5:
        print(f'{name}: {len(sub)}场(样本不足)')
        return
    w = sum(1 for r in sub if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
    print(f'{name}: {len(sub)}场 胜率{w/len(sub):.1%} 盈亏{p:+.2f} ROI{p/len(sub):+.2%}')


for month in ['2026-07', '2026-08']:
    for main_name, follow in [('主主客(跟主胜)', True), ('客客主(跟客胜)', False)]:
        rows = build(follow, month)
        if not rows:
            continue
        print(f'=== {month} {main_name}: {len(rows)}场 ===')
        report('  TS<40%', [r for r in rows if r['tsl'] < 0.4])
        report('  TS40-50%', [r for r in rows if 0.4 <= r['tsl'] < 0.5])
        report('  TS>=50%', [r for r in rows if r['tsl'] >= 0.5])
        report('  TS>=40% & EV<0', [r for r in rows if r['tsl'] >= 0.4 and r['ev'] < 0])
        report('  TS>=40% & EV>=0', [r for r in rows if r['tsl'] >= 0.4 and r['ev'] >= 0])
        print()

# 官方 best_value.ev 交叉(8月主主客)
print('=== 8月主主客 x 官方best_value.ev (仅入选value/ruleA的场) ===')
rows = build(True, '2026-08')
bv_rows = [r for r in rows if r['bv_ev'] is not None]
report('  官方EV入选场', bv_rows)
if bv_rows:
    for lo, hi, name in [(-9, 0, '官方EV<0'), (0, 0.1, '官方EV0-0.1'), (0.1, 0.3, '官方EV0.1-0.3'), (0.3, 9, '官方EV>0.3')]:
        report(f'  {name} + TS>=40%', [r for r in bv_rows if lo <= r['bv_ev'] < hi and r['tsl'] >= 0.4])
