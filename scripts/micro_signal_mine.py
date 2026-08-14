#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投注簿微信号挖掘：在 value/rise/away_rule 信号内部找条件胜率显著高于基线的细分组合"""
import json
from collections import defaultdict

D = json.load(open('docs/data/betting_ledger.json'))
SETTLED = [r for r in D if r.get('result') in ('win', 'loss')]
print(f"已结算 {len(SETTLED)} 条")

def base(sig=None, oc=None):
    rs = SETTLED
    if sig: rs = [r for r in rs if r.get('signal') == sig]
    if oc:  rs = [r for r in rs if r.get('outcome') == oc]
    n = len(rs)
    if not n: return None
    w = sum(1 for r in rs if r['result'] == 'win')
    return n, w/n, sum(r['profit'] for r in rs)

# ---------- 基线 ----------
print("\n=== 基线 ===")
for sig in ['value', 'rise', 'away_rule']:
    b = base(sig)
    if b: print(f"{sig:10s} n={b[0]:5d} 胜率={b[1]:.1%} 利润={b[2]:+.2f}")

print("\n=== 信号×方向 ===")
for sig in ['value', 'rise', 'away_rule']:
    for oc in ['home', 'draw', 'away']:
        b = base(sig, oc)
        if b and b[0] >= 10:
            print(f"{sig:10s} {oc:5s} n={b[0]:5d} 胜率={b[1]:.1%} 利润={b[2]:+.2f}")

# ---------- 条件分箱 ----------
def bins(rs, key, edges, labels, sig=None, oc=None):
    out = []
    rs2 = rs
    if sig: rs2 = [r for r in rs2 if r.get('signal') == sig]
    if oc:  rs2 = [r for r in rs2 if r.get('outcome') == oc]
    for i, e in enumerate(edges):
        if i + 1 >= len(edges): break
        lo, hi = edges[i], edges[i+1]
        if lo is None:
            grp = [r for r in rs2 if r.get(key) is not None and r[key] < hi]
        elif hi is None:
            grp = [r for r in rs2 if r.get(key) is not None and r[key] >= lo]
        else:
            grp = [r for r in rs2 if r.get(key) is not None and lo <= r[key] < hi]
        n = len(grp)
        if not n: continue
        w = sum(1 for r in grp if r['result'] == 'win')
        out.append((labels[i], n, w/n, sum(r['profit'] for r in grp)))
    return out

print("\n=== 价值投注：按赔率×方向 ===")
for oc in ['away', 'draw', 'home']:
    print(f"--- {oc} ---")
    for row in bins(SETTLED, 'odds', [1, 2.5, 4, 6, None], ['<2.5', '2.5-4', '4-6', '6+'], 'value', oc):
        if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 价值投注：按EV×方向 ===")
for oc in ['away', 'draw', 'home']:
    print(f"--- {oc} ---")
    for row in bins(SETTLED, 'ev', [0, 0.5, 1.0, 2.0, None], ['<0.5', '0.5-1', '1-2', '2+'], 'value', oc):
        if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 升水信号：按升水档×方向 ===")
for oc in ['away', 'draw', 'home']:
    print(f"--- {oc} ---")
    for row in bins(SETTLED, 'rise', [None, 0.4, 0.5, 0.8, None], ['<0.4', '0.4-0.5', '0.5-0.8', '0.8+'], 'rise', oc):
        if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 客胜规则A：按赔率分档 ===")
for row in bins(SETTLED, 'odds', [1, 3, 4, 5, 6, None], ['<3', '3-4', '4-5', '5-6', '6+'], 'away_rule', 'away'):
    if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 客胜规则A：按EV分档 ===")
for row in bins(SETTLED, 'ev', [0, 0.5, 1.0, 2.0, None], ['<0.5', '0.5-1', '1-2', '2+'], 'away_rule', 'away'):
    if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 价值投注客胜：按edge分档 ===")
for row in bins(SETTLED, 'edge', [None, 0.05, 0.10, 0.15, None], ['<5%', '5-10%', '10-15%', '15%+'], 'value', 'away'):
    if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 价值投注客胜：按kelly分档 ===")
for row in bins(SETTLED, 'kelly', [None, 0.05, 0.1, 0.15, None], ['<5%', '5-10%', '10-15%', '15%+'], 'value', 'away'):
    if row[1] >= 10: print(f"  {row[0]:6s} n={row[1]:4d} 胜率={row[2]:.1%} 利润={row[3]:+.2f}")

# ---------- 综合筛选：正利润格子 ----------
print("\n=== 正利润条件组合（n>=15, 利润>0）===")
cands = []
# 信号×方向×赔率带
for sig in ['value', 'rise', 'away_rule']:
    for oc in ['home', 'draw', 'away']:
        for row in bins(SETTLED, 'odds', [1, 2.5, 4, 6, None], ['<2.5', '2.5-4', '4-6', '6+'], sig, oc):
            n, wr, pf = row[1], row[2], row[3]
            if n >= 15 and pf > 0:
                cands.append((sig, oc, 'odds' + row[0], n, wr, pf))
        for row in bins(SETTLED, 'ev', [0, 0.5, 1.0, 2.0, None], ['<0.5', '0.5-1', '1-2', '2+'], sig, oc):
            n, wr, pf = row[1], row[2], row[3]
            if n >= 15 and pf > 0:
                cands.append((sig, oc, 'ev' + row[0], n, wr, pf))
for c in sorted(cands, key=lambda x: -x[5]):
    print(f"  {c[0]:10s} {c[1]:5s} {c[2]:6s} n={c[3]:4d} 胜率={c[4]:.1%} 利润={c[5]:+.2f}")
