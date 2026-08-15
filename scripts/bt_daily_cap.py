#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测: 每日限额投注 — 每天按EV排序只取top N场"""
import json, re
from collections import defaultdict

with open('docs/data/results.json', encoding='utf-8') as f:
    matches = json.load(f)['matches']

def parse_score(s):
    if not s: return None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else None

# 收集所有信号场次: (day, ev, edge, outcome, odds, score)
sigs = []
for m in matches:
    bv = m.get('best_value') or {}
    if not bv.get('outcome'): continue
    ev, edge, out = bv.get('ev', 0), bv.get('edge', 0), bv['outcome']
    day = (m.get('match_time') or m.get('date') or '')[:10]
    if not day: continue
    odds = {'home': m.get('odds_win', 0), 'draw': m.get('odds_draw', 0), 'away': m.get('odds_loss', 0)}.get(out, 0) or 0
    sigs.append({'day': day, 'ev': ev, 'edge': edge, 'out': out, 'odds': odds,
                 'score': m.get('score', ''), 'teams': f"{m.get('home_team','')}vs{m.get('away_team','')}"})

# 按天分组, 每天按EV排序
by_day = defaultdict(list)
for s in sigs:
    by_day[s['day']].append(s)
for d in by_day:
    by_day[d].sort(key=lambda x: -x['ev'])

def bt(top_n, ev_min=0.05, edge_min=0.02, label=''):
    """每天取 top_n 场, 统计已完赛部分"""
    n = w = prof = 0
    for d, ss in sorted(by_day.items()):
        for s in ss[:top_n]:
            if s['ev'] <= ev_min or s['edge'] <= edge_min: continue
            st = parse_score(s['score'])
            if st is None: continue
            h, a = st
            act = 'home' if h > a else ('draw' if h == a else 'away')
            n += 1
            if act == s['out']:
                w += 1; prof += s['odds'] - 1
            else:
                prof -= 1
    if n:
        print(f'  {label:14s} {n:4d}场  胜率{w/n*100:5.1f}%  利润{prof:+7.2f}')
    else:
        print(f'  {label:14s} 0场')

print('=== 每日限额 (按EV排序取top N, 双门槛) ===')
for tn in [1, 2, 3, 5, 10]:
    bt(tn, label=f'每天top{tn}')

print('\n=== 每日限额 + 提高门槛 ===')
for tn in [2, 3, 5]:
    bt(tn, ev_min=0.10, label=f'top{tn} EV≥0.10')
    bt(tn, ev_min=0.15, label=f'top{tn} EV≥0.15')

print('\n=== 只投规则A (客胜, 每日限额) ===')
ruleA_days = defaultdict(list)
for s in sigs:
    if s['out'] == 'away' and s['ev'] > 0.5:
        ruleA_days[s['day']].append(s)
for d in ruleA_days:
    ruleA_days[d].sort(key=lambda x: -x['ev'])
def bt_ruleA(top_n, label=''):
    n = w = prof = 0
    for d, ss in sorted(ruleA_days.items()):
        for s in ss[:top_n]:
            st = parse_score(s['score'])
            if st is None: continue
            h, a = st
            act = 'home' if h > a else ('draw' if h == a else 'away')
            n += 1
            if act == s['out']:
                w += 1; prof += s['odds'] - 1
            else:
                prof -= 1
    if n:
        print(f'  {label:14s} {n:4d}场  胜率{w/n*100:5.1f}%  利润{prof:+7.2f}')
    else:
        print(f'  {label:14s} 0场')
for tn in [1, 2, 3, 5]:
    bt_ruleA(tn, label=f'规则A top{tn}')

print('\n=== 按月稳定性 (最优组合候选) ===')
def bt_month(top_n, ev_min, edge_min, label=''):
    for mo in sorted(set(s['day'][:7] for s in sigs)):
        n = w = prof = 0
        for d, ss in sorted(by_day.items()):
            if not d.startswith(mo): continue
            for s in ss[:top_n]:
                if s['ev'] <= ev_min or s['edge'] <= edge_min: continue
                st = parse_score(s['score'])
                if st is None: continue
                h, a = st
                act = 'home' if h > a else ('draw' if h == a else 'away')
                n += 1
                if act == s['out']:
                    w += 1; prof += s['odds'] - 1
                else:
                    prof -= 1
        if n:
            print(f'  {mo}  {n:3d}场  胜率{w/n*100:5.1f}%  利润{prof:+7.2f}')
print('--- top3 EV≥0.10 按月:')
bt_month(3, 0.10, 0.02)
print('--- top2 按月:')
bt_month(2, 0.05, 0.02)
