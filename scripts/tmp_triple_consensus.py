#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三模型一致(主主主/客客客) 按周测试 + EV 交叉"""
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
    """kind: '主主主' 或 '客客客'"""
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
        if kind == '主主主':
            if not (md == '主' and ld == '主' and td == '主'):
                continue
            tsl = m.get('ts_win', 0)  # TS主胜概率=三模型一致强度
            odds = m.get('odds_win')
            p = (m.get('model_win', 0) + m.get('lgbm_win', 0)) / 2
            h, a = sc.split('-')
            try:
                h, a = int(h), int(a)
            except Exception:
                continue
            won = h > a
        else:
            if not (md == '客' and ld == '客' and td == '客'):
                continue
            tsl = m.get('ts_loss', 0)
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
        wk, iso = week_key(mt[:10])
        rows.append({'won': won, 'odds': odds, 'ev': ev, 'tsl': tsl, 'mt': mt[:10], 'wk': wk, 'iso': iso})
    return rows


def report(name, sub):
    if len(sub) < 5:
        print(f'{name}: {len(sub)}场(样本不足)')
        return
    w = sum(1 for r in sub if r['won'])
    p = sum((r['odds'] - 1) if r['won'] else -1 for r in sub)
    print(f'{name}: {len(sub)}场 胜率{w/len(sub):.1%} 盈亏{p:+.2f} ROI{p/len(sub):+.2%}')


from collections import defaultdict

for kind in ['主主主', '客客客']:
    rows = build(kind)
    print(f'================ {kind} (跟{"主" if kind[0]=="主" else "客"}胜) ================')
    print(f'总数: {len(rows)}场')

    # 逐周
    weekly = defaultdict(list)
    for r in rows:
        weekly[r['wk']].append(r)
    print()
    print('--- 逐周 ---')
    for wk in sorted(weekly):
        allr = weekly[wk]
        w = sum(1 for r in allr if r['won'])
        p = sum((r['odds'] - 1) if r['won'] else -1 for r in allr)
        print(f'{wk} (W{allr[0]["iso"]}): {len(allr)}场 胜{w} 盈亏{p:+.2f} ROI{p/len(allr)*100:+.1f}%')

    # 汇总: 6-7月 vs 8月
    old = [r for r in rows if r['mt'] < '2026-08-01']
    new = [r for r in rows if r['mt'] >= '2026-08-01']
    print()
    print('--- 分段 ---')
    report('6/22-7/31', old)
    report('8/1-8/16', new)

    # EV 交叉 (8月)
    aug = [r for r in rows if r['mt'] >= '2026-08-01']
    print()
    print('--- 8月 EV 分段 ---')
    for lo, hi, name in [(-9, 0, 'EV<0'), (0, 0.1, 'EV0-0.1'), (0.1, 0.3, 'EV0.1-0.3'), (0.3, 9, 'EV>0.3')]:
        report(f'  {name}', [r for r in aug if lo <= r['ev'] < hi])

    # 8月 TS一致强度分段 (三模型一致时TS同向概率)
    print()
    print('--- 8月 TS强度分段 ---')
    for lo, hi, name in [(0, 0.4, 'TS<40%'), (0.4, 0.5, 'TS40-50%'), (0.5, 0.6, 'TS50-60%'), (0.6, 1.01, 'TS>=60%')]:
        report(f'  {name}', [r for r in aug if lo <= r['tsl'] < hi])

    # 8月 赔率分段
    print()
    print('--- 8月 赔率分段 ---')
    for lo, hi in [(1, 1.5), (1.5, 2), (2, 2.5), (2.5, 100)]:
        report(f'  赔率{lo}-{hi}', [r for r in aug if lo <= r['odds'] < hi])
    print()
