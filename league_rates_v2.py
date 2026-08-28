#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""各联赛命中率 v2: opencc t2s 归一化 + 全量results 口径"""
import json
from collections import defaultdict
from opencc import OpenCC

cc = OpenCC("t2s")

data = json.load(open("docs/data/results.json", encoding="utf-8"))
ms = data["matches"]

league = defaultdict(lambda: [0, 0])  # league -> [hit, total]
alias = defaultdict(list)
for m in ms:
    hit = m.get("hit", "")
    if not hit or (not hit.endswith("✓") and not hit.endswith("✘")):
        continue
    lname = cc.convert(m.get("event") or "?")
    alias[lname].append(m.get("event") or "?")
    league[lname][1] += 1
    if hit.endswith("✓"):
        league[lname][0] += 1

rows = sorted(league.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1))
print("== 归一化后各联赛命中率 (全量, ≥10场) ==")
for lname, (h, n) in rows:
    if n >= 10:
        print(f"{lname}: {n}场 命中{h} {h/n*100:.1f}%")
print()
print("== 全部(含小样本) top20 ==")
for lname, (h, n) in rows[:20]:
    print(f"{lname}: {n}场 {h/n*100:.1f}%")
print()
print("== 别名检查(归一化后同名的原词) ==")
for lname, al in alias.items():
    if len(set(al)) > 1:
        print(f"{lname}: {sorted(set(al))}")
