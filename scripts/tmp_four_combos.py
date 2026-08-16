#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四组合对照: 主主主/主主客/客客客/客客主 × 逐周 × EV"""
import json
from datetime import datetime, timedelta

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


def week_key(datestr):
    d = datetime.strptime(datestr, '%Y-%m-%d')
    monday = d - timedelta(days=d.weekday())
    return monday.strftime('%Y-%m-%d'), d.isocalendar()[1]


def build(kind):
    rows = []
    for m in matches:
        sc = m.get('score') or ''
        if not sc or '-' not in sc:
            continue
        mt = m.get('match_time', '')
        if not mt.startswith('2026'):
            continue
        md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        combo = md + ld + td
        if kind == '主主主':
            if combo != '主主主':
                continue
            odds = m.get('odds_win')
            p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
            tsl = m.get('ts_win', 0)
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = h > a
        elif kind == '主主客':
            if combo != '主主客':
                continue
            odds = m.get('odds_win')
            p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
            tsl = m.get('ts_loss', 0)
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = h > a
        elif kind == '客客客':
            if combo != '客客客':
                continue
            odds = m.get('odds_loss')
            p = (m.get('model_loss', 0) + m.get('lgbm_loss', 0)) / 2
            tsl = m.get('ts_loss', 0)
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = a > h
        else:  # 客客主
            if combo != '客客主':
                continue
            odds = m.get('odds_loss')
            p = (m.get('model_loss', 0) + m.get('lgbm_loss', 0)) / 2
            tsl = m.get('ts_win', 0)
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = a > h
        if not odds:
            continue
        ev = p * odds - 1
        wk, iso = week_key(mt[:10])
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'tsl': tsl, 'mt': mt[:10], 'wk': wk, 'iso': iso})
    return rows


def stat(rows):
    if not rows:
        return (0, 0.0, 0.0)
    w = sum(1 for r in rows if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in rows)
    return (len(rows), w / len(rows) * 100, p)


kinds = ['主主主', '主主客', '客客客', '客客主']
data_all = {k: build(k) for k in kinds}

print('=== 逐周 ROI% 对照表 ===')
weeks = sorted(set(r['wk'] for k in kinds for r in data_all[k]))
hdr = '周'.ljust(12) + ''.join(k.ljust(12) for k in kinds)
print(hdr)
for wk in weeks:
    line = wk.ljust(12)
    for k in kinds:
        n, wr, p = stat([r for r in data_all[k] if r['wk'] == wk])
        if n >= 5:
            line += f'{p/n*100:>+9.1f}%({n}) '
        elif n > 0:
            line += f'{p/n*100:>+9.1f}%({n}小)'
        else:
            line += '    -     '
    print(line)

print()
print('=== 8月全量 vs W32+W33 ===')
for k in kinds:
    aug = [r for r in data_all[k] if r['mt'] >= '2026-08-01']
    w2 = [r for r in data_all[k] if '2026-08-03' <= r['mt'] <= '2026-08-16']
    n1, wr1, p1 = stat(aug)
    n2, wr2, p2 = stat(w2)
    print(f'{k}: 8月全 {n1}场 胜率{wr1:.1f}% ROI{p1/n1*100 if n1 else 0:+.1f}% | '
          f'W32+33 {n2}场 胜率{wr2:.1f}% ROI{p2/n2*100 if n2 else 0:+.1f}%')

print()
print('=== 8月 EV 交叉 (主主主 vs 客客客) ===')
for k in ['主主主', '客客客']:
    aug = [r for r in data_all[k] if r['mt'] >= '2026-08-01']
    print(f'--- {k} ---')
    for lo, hi, name in [(-9, -0.1, 'EV<-0.1'), (-0.1, 0, 'EV-0.1-0'), (0, 0.1, 'EV0-0.1'), (0.1, 9, 'EV>0.1')]:
        sub = [r for r in aug if lo <= r['ev'] < hi]
        n, wr, p = stat(sub)
        if n >= 5:
            print(f'  {name}: {n}场 胜率{wr:.1f}% 盈亏{p:+.2f} ROI{p/n*100:+.1f}%')
        elif n > 0:
            print(f'  {name}: {n}场(样本不足)')

print()
print('=== 8月 TS强度交叉 (主主主 vs 客客客) ===')
for k in ['主主主', '客客客']:
    aug = [r for r in data_all[k] if r['mt'] >= '2026-08-01']
    print(f'--- {k} ---')
    for lo, hi, name in [(0, 0.4, 'TS<40%'), (0.4, 0.5, 'TS40-50%'), (0.5, 0.6, 'TS50-60%'), (0.6, 1.01, 'TS>=60%')]:
        sub = [r for r in aug if lo <= r['tsl'] < hi]
        n, wr, p = stat(sub)
        if n >= 5:
            print(f'  {name}: {n}场 胜率{wr:.1f}% 盈亏{p:+.2f} ROI{p/n*100:+.1f}%')
        elif n > 0:
            print(f'  {name}: {n}场(样本不足)')
