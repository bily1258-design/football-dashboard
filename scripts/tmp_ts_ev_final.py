#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TS x EV 最终验证: 8月分段延续性 + 赔率结构 + 今日候选"""
import json

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data


def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')


def build(matches, follow_main=True, start='2026-08', end='2026-08-31'):
    rows = []
    for m in matches:
        sc = m.get('score') or ''
        if not sc or '-' not in sc:
            continue
        mt = m.get('match_time', '')
        if not (start <= mt[:10] <= end):
            continue
        md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        if follow_main:
            if not (md == '主' and ld == '主' and td == '客'):
                continue
            tsl = m.get('ts_loss', 0)
            odds = m.get('odds_win')
            p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
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
            odds = m.get('odds_loss')
            p = (m.get('model_loss', 0) + m.get('lgbm_loss', 0)) / 2
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = a > h
        if not odds:
            continue
        ev = p * odds - 1
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'tsl': tsl, 'mt': mt[:10],
                     'home': m.get('home_team', ''), 'away': m.get('away_team', ''), 'sc': sc})
    return rows


def report(name, sub):
    if len(sub) < 5:
        print(f'{name}: {len(sub)}场(样本不足)')
        return
    w = sum(1 for r in sub if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
    print(f'{name}: {len(sub)}场 胜率{w/len(sub):.1%} 盈亏{p:+.2f} ROI{p/len(sub):+.2%}')


# 8月分段: 上半(8/1-8/8) vs 下半(8/9-8/16)
print('=== 8月主主客+TS>=40% 分段延续性 ===')
r1 = [r for r in build(matches, True, '2026-08-01', '2026-08-08') if r['tsl'] >= 0.4]
r2 = [r for r in build(matches, True, '2026-08-09', '2026-08-16') if r['tsl'] >= 0.4]
report('  8/1-8/8', r1)
report('  8/9-8/16', r2)

# 赔率结构
print()
print('=== 8月主主客+TS>=40% 赔率结构 ===')
r = [r for r in build(matches, True) if r['tsl'] >= 0.4]
odds_list = sorted(rr['odds'] for rr in r)
import statistics
print(f'  赔率中位数 {statistics.median(odds_list):.2f} | 均值 {statistics.mean(odds_list):.2f} | 范围 {min(odds_list):.2f}-{max(odds_list):.2f}')
for lo, hi in [(1, 1.5), (1.5, 2), (2, 2.5), (2.5, 100)]:
    report(f'  赔率{lo}-{hi}', [rr for rr in r if lo <= rr['odds'] < hi])

# 命中明细(展示TS>=50%的11场)
print()
print('=== 8月主主客+TS>=50% 全部场次明细 ===')
for rr in sorted(r, key=lambda x: -x['tsl']):
    if rr['tsl'] >= 0.5:
        print(f"  {rr['mt']} {rr['home']} vs {rr['away']} {rr['sc']} @{rr['odds']} TS客{rr['tsl']:.0%} "
              f"{'✅' if rr['won'] else '❌'} EV{rr['ev']:+.2f}")

# 今日候选(未开赛)
print()
print('=== 今日(08-16) 未开赛候选: 主主客+TS>=40% ===')
n = 0
for m in matches:
    sc = m.get('score') or ''
    if sc:
        continue
    mt = m.get('match_time', '')
    if not mt.startswith('2026-08-16'):
        continue
    md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
    ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
    td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
    if not (md == '主' and ld == '主' and td == '客'):
        continue
    tsl = m.get('ts_loss', 0)
    if tsl < 0.4:
        continue
    n += 1
    p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
    odds = m.get('odds_win')
    ev = p * odds - 1 if odds else None
    print(f"  {mt[11:16]} {m.get('home_team')} vs {m.get('away_team')} | 主胜@{odds} | TS客{tsl:.0%} | EV{ev:+.2f}")
print(f'共 {n} 场')
