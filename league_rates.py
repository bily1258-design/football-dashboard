#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""各联赛命中率(全量results + 投注池内)"""
import json, glob
from collections import defaultdict

data = json.load(open("docs/data/results.json", encoding="utf-8"))
ms = data["matches"]

# 全量: 有赛果且推荐方向且双时点(与回测同口径)
league = defaultdict(lambda: [0, 0])  # league -> [hit, total]
for m in ms:
    hit = m.get("hit", "")
    lname = m.get("league") or m.get("event") or "?"
    if not hit or (not hit.endswith("✓") and not hit.endswith("✘")):
        continue
    league[lname][1] += 1
    if hit.endswith("✓"):
        league[lname][0] += 1

rows = sorted(league.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1))
print("== 全量 results.json 各联赛命中率 (≥10场) ==")
for lname, (h, n) in rows:
    if n >= 10:
        print(f"{lname}: {n}场 命中{h} {h/n*100:.1f}%")
print()
print("== 全部(含小样本) top15 ==")
for lname, (h, n) in rows[:15]:
    print(f"{lname}: {n}场 {h/n*100:.1f}%")
