#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输家画像挖掘：推荐后输掉的比赛有什么共同特征？→ 避雷规则"""
import json
from collections import defaultdict

D = json.load(open('docs/data/betting_ledger.json'))
S = [r for r in D if r.get('result') in ('win', 'loss')]
W = [r for r in S if r['result'] == 'win']
L = [r for r in S if r['result'] == 'loss']
print(f"已结算 {len(S)} = 胜 {len(W)} + 负 {len(L)}")

def avg(rs, k):
    v = [r[k] for r in rs if r.get(k) is not None]
    return round(sum(v) / len(v), 3) if v else None

# ---------- 1. 赢家 vs 输家 特征均值对比 ----------
print("\n=== 赢家 vs 输家 特征均值（避雷画像）===")
print(f"{'特征':8s} {'胜均值':>8s} {'负均值':>8s} {'差异':>8s}  解读")
for k in ['odds', 'ev', 'edge', 'kelly', 'rise']:
    a, b = avg(W, k), avg(L, k)
    if a is None or b is None: continue
    d = b - a
    tag = '输家更高→高值避雷' if d > 0.02 else ('输家更低' if d < -0.02 else '~无差异')
    print(f"{k:8s} {a:8.3f} {b:8.3f} {d:+8.3f}  {tag}")

# ---------- 2. 输家集中度：各档位败率 ----------
def loss_rate_bins(rs, key, edges, labels, sig=None, oc=None):
    rs2 = [r for r in rs if (not sig or r.get('signal') == sig) and (not oc or r.get('outcome') == oc)]
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i+1]
        grp = [r for r in rs2 if r.get(key) is not None and (lo is None or r[key] >= lo) and (hi is None or r[key] < hi)]
        n = len(grp)
        if n < 10: continue
        ls = sum(1 for r in grp if r['result'] == 'loss')
        out.append((labels[i], n, ls / n, sum(r['profit'] for r in grp)))
    return out

print("\n=== 全信号：败率按赔率带（败率越高=越该避）===")
for row in loss_rate_bins(S, 'odds', [1, 2.5, 4, 6, None], ['<2.5', '2.5-4', '4-6', '6+']):
    print(f"  赔率{row[0]:6s} n={row[1]:4d} 败率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 全信号：败率按EV带 ===")
for row in loss_rate_bins(S, 'ev', [None, 0.5, 1.0, 2.0, None], ['<0.5', '0.5-1', '1-2', '2+']):
    print(f"  EV{row[0]:7s} n={row[1]:4d} 败率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 全信号：败率按edge带 ===")
for row in loss_rate_bins(S, 'edge', [None, 0.05, 0.1, 0.15, None], ['<5%', '5-10%', '10-15%', '15%+']):
    print(f"  edge{row[0]:6s} n={row[1]:4d} 败率={row[2]:.1%} 利润={row[3]:+.2f}")

print("\n=== 全信号：败率按kelly带 ===")
for row in loss_rate_bins(S, 'kelly', [None, 0.05, 0.1, 0.15, None], ['<5%', '5-10%', '10-15%', '15%+']):
    print(f"  kelly{row[0]:5s} n={row[1]:4d} 败率={row[2]:.1%} 利润={row[3]:+.2f}")

# ---------- 3. 方向性避雷 ----------
print("\n=== 信号×方向 败率（方向级避雷）===")
agg = defaultdict(list)
for r in S:
    agg[(r['signal'], r['outcome'])].append(r)
for k in sorted(agg):
    rs = agg[k]
    if len(rs) < 10: continue
    n = len(rs); ls = sum(1 for r in rs if r['result'] == 'loss')
    print(f"  {k[0]:8s} {k[1]:5s} n={n:4d} 败率={ls/n:.1%} 利润={sum(r['profit'] for r in rs):+.2f}")

# ---------- 4. 特殊字段 ----------
print("\n=== weight(⚡高权重) 明细 ===")
wr = [r for r in S if r['signal'] == 'weight']
print(f"  已结算 {len(wr)} 场: 胜 {sum(1 for r in wr if r['result']=='win')} / 负 {sum(1 for r in wr if r['result']=='loss')}")
for r in wr[:10]:
    print(f"    {r['teams']} | 方向={r['outcome']} 赔率={r['odds']} EV={r['ev']:.2f} → {r['result']}")

print("\n=== same_dir 字段交叉（同向=？）===")
sd = defaultdict(lambda: [0, 0])
for r in S:
    v = r.get('same_dir')
    sd[str(v)][0 if r['result'] == 'win' else 1] += 1
for k, (w, l) in sorted(sd.items()):
    if w + l >= 10:
        print(f"  same_dir={k:6s} n={w+l:4d} 胜率={w/(w+l):.1%}")

# ---------- 5. 败率最高的组合（候选避雷规则）----------
print("\n=== 败率>75% 的组合（样本>=15）===")
cands = []
for sig in ['value', 'rise', 'ruleA']:
    for oc in ['home', 'draw', 'away']:
        for row in loss_rate_bins(S, 'odds', [1, 2.5, 4, 6, None], ['<2.5', '2.5-4', '4-6', '6+'], sig, oc):
            if row[1] >= 15 and row[2] > 0.75:
                cands.append((sig, oc, 'odds' + row[0], row[1], row[2], row[3]))
        for row in loss_rate_bins(S, 'ev', [0, 0.5, 1.0, 2.0, None], ['<0.5', '0.5-1', '1-2', '2+'], sig, oc):
            if row[1] >= 15 and row[2] > 0.75:
                cands.append((sig, oc, 'ev' + row[0], row[1], row[2], row[3]))
for c in sorted(cands, key=lambda x: -x[4]):
    print(f"  {c[0]:8s} {c[1]:5s} {c[2]:6s} n={c[3]:4d} 败率={c[4]:.1%} 利润={c[5]:+.2f}")
