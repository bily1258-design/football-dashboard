#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TS x EV 按周(周一~周日)分析"""
import json
from datetime import datetime, timedelta

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


def week_key(datestr):
    """返回 'MM-DD(周一)' 周起始日期 + ISO周号"""
    d = datetime.strptime(datestr, '%Y-%m-%d')
    # 周一到周日
    monday = d - timedelta(days=d.weekday())
    return monday.strftime('%Y-%m-%d'), d.isocalendar()[1]


def build(matches):
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
        wk, iso = week_key(mt[:10])
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'tsl': tsl, 'mt': mt[:10], 'wk': wk, 'iso': iso})
    return rows


def report_row(rows):
    if not rows:
        return None
    w = sum(1 for r in rows if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in rows)
    return w, len(rows), w / len(rows), p, p / len(rows)


rows = build(matches)
print(f'总主主客场次: {len(rows)}')

# 按周分组
from collections import defaultdict
weekly = defaultdict(list)
for r in rows:
    weekly[r['wk']].append(r)

print()
print('=== 逐周: 全部主主客 vs TS>=40% 子集 ===')
print(f'{"周(周一)":<12}{"全部场次":>10}{"全部ROI":>10}{"TS>=40%":>10}{"胜率":>8}{"ROI":>9}')
for wk in sorted(weekly):
    allr = weekly[wk]
    sub = [r for r in allr if r['tsl'] >= 0.4]
    a = report_row(allr)
    s = report_row(sub)
    if a:
        print(f'{wk:<12}{a[1]:>8}场{a[3]:>+9.2f}{s[1] if s else 0:>8}场'
              f'{s[2]*100:>7.1f}%{s[4]*100:>+8.1f}%' if s else
              f'{wk:<12}{a[1]:>8}场{a[3]:>+9.2f}      -      -      -')
    else:
        print(f'{wk:<12}(空)')

# 简化输出, 避免上面三元表达式混乱
print()
print('=== 逐周明细(可读版) ===')
for wk in sorted(weekly):
    allr = weekly[wk]
    sub = [r for r in allr if r['tsl'] >= 0.4]
    a = report_row(allr)
    s = report_row(sub)
    parts = [f'{wk} (W{allr[0]["iso"]})']
    if a:
        parts.append(f'全部 {a[1]}场 胜{a[0]} 盈亏{a[3]:+.2f} ROI{a[4]*100:+.1f}%')
    if s:
        parts.append(f'TS>=40% {s[1]}场 胜{s[0]} 盈亏{s[3]:+.2f} ROI{s[4]*100:+.1f}%')
    else:
        parts.append('TS>=40%: 0场')
    print(' | '.join(parts))
