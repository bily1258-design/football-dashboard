#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测: M最大概率方向作为下注信号, 各策略胜率/利润"""
import json, re, sys

with open('docs/data/results.json', encoding='utf-8') as f:
    matches = json.load(f)['matches']

def parse_score(s):
    if not s: return None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else None

def outcome_result(st, dirn):
    h, a = st
    actual = 'home' if h > a else ('draw' if h == a else 'away')
    return 'win' if actual == dirn else 'loss'

def argmax3(w, d, l):
    mx = max(w, d, l)
    return ['home', 'draw', 'away'][0 if mx == w else (1 if mx == d else 2)]

ODD = {'home': 'odds_win', 'draw': 'odds_draw', 'away': 'odds_loss'}
rows = []
for m in matches:
    st = parse_score(m.get('score'))
    if st is None: continue
    mw, md, ml = m.get('model_win', 0) or 0, m.get('model_draw', 0) or 0, m.get('model_loss', 0) or 0
    lw, ld, ll = m.get('lgbm_win', 0) or 0, m.get('lgbm_draw', 0) or 0, m.get('lgbm_loss', 0) or 0
    if mw + md + ml == 0: continue
    mp = argmax3(mw, md, ml)
    lp = argmax3(lw, ld, ll)
    rows.append({
        'mp': mp, 'mp_prob': max(mw, md, ml), 'lp': lp,
        'odds': {d: m.get(ODD[d], 0) or 0 for d in ['home', 'draw', 'away']},
        'ev': (m.get('best_value') or {}).get('ev', 0),
        'edge': (m.get('best_value') or {}).get('edge', 0),
        'bv_out': (m.get('best_value') or {}).get('outcome', ''),
        'month': (m.get('match_time') or '')[:7],
        'st': st,
    })

def backtest(rs, name, min_odds=1.01, max_odds=99):
    """对每场按策略选出方向, 返回胜率/利润"""
    n = w = profit = 0
    for r in rs:
        odds = r['odds'][r['mp']]
        if odds < min_odds or odds > max_odds: continue
        res = outcome_result(r['st'], r['mp'])
        if res is None: continue
        n += 1
        if res == 'win':
            w += 1; profit += odds - 1
        else:
            profit -= 1
    if n == 0:
        print(f'  {name}: 0场'); return
    print(f'  {name}: {n:4d}场  胜率 {w/n*100:5.1f}%  利润 {profit:+7.2f}')

print('=== 策略1: 裸下注 M最大概率方向 ===')
backtest(rows, '全量')

print('\n=== 策略2: M方向 + 概率门槛 ===')
for lo, hi in [(0.3, 1.01), (0.4, 1.01), (0.45, 1.01), (0.5, 1.01), (0.6, 1.01)]:
    backtest([r for r in rows if lo <= r['mp_prob'] < hi], f'p≥{lo:.0%}')

print('\n=== 策略3: M方向 + 赔率门槛 (低赔率陷阱排除) ===')
backtest(rows, '赔率≥1.5', min_odds=1.5)
backtest(rows, '赔率≥1.7', min_odds=1.7)
backtest(rows, '赔率≥2.0', min_odds=2.0)
backtest(rows, '赔率 1.3-2.0', min_odds=1.3, max_odds=2.0)

print('\n=== 策略4: M vs LGBM 方向 ===')
agree = [r for r in rows if r['mp'] == r['lp']]
conflict = [r for r in rows if r['mp'] != r['lp']]
backtest(agree, 'M与LGBM同向')
backtest(conflict, 'M与LGBM冲突')
backtest([r for r in conflict if r['mp_prob'] >= 0.35], '冲突且M概率≥35%')

print('\n=== 策略5: 按月稳定性 (M裸下注) ===')
for mo in sorted(set(r['month'] for r in rows)):
    backtest([r for r in rows if r['month'] == mo], mo)

print('\n=== 策略6: 只用 best_value 的 EV 方向 (现有价值投注) ===')
def bt_bv(rs, name, ev_min=0.05, edge_min=0.02):
    n = w = profit = 0
    for r in rs:
        if not r['bv_out'] or r['ev'] <= ev_min or r['edge'] <= edge_min: continue
        odds = r['odds'][r['bv_out']]
        res = outcome_result(r['st'], r['bv_out'])
        if res is None: continue
        n += 1
        if res == 'win': w += 1; profit += odds - 1
        else: profit -= 1
    print(f'  {name}: {n:4d}场  胜率 {w/n*100:5.1f}%  利润 {profit:+7.2f}') if n else print(f'  {name}: 0场')

bt_bv(rows, '现有双门槛(0.05/0.02)')
bt_bv(rows, '收紧EV≥0.10', ev_min=0.10)
bt_bv(rows, '收紧EV≥0.15', ev_min=0.15)
bt_bv(rows, '收紧EV≥0.20', ev_min=0.20)
bt_bv(rows, '收紧EV≥0.25', ev_min=0.25)
bt_bv(rows, '收紧edge≥0.05', edge_min=0.05)
