#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
data = json.load(open("docs/data/results.json", encoding="utf-8"))
ms = data["matches"]
# 找一条有 league 字段的
keys = set()
for m in ms:
    keys |= set(m.keys())
print("全部字段:", sorted(keys))
# 有值示例
for m in ms:
    if m.get("league"):
        print("示例 league:", m.get("league"), "| teams:", m.get("home_team"), "vs", m.get("away_team"))
        break
    if m.get("event"):
        print("示例 event:", m.get("event"))
        break
# 检查 leder join 用的 fid 是否匹配到
ledger = json.load(open("docs/data/betting_ledger.json", encoding="utf-8"))
settled = [x for x in ledger if x.get("result") in ("win", "loss")]
print("\nsettled条数:", len(settled))
hit_has_league = sum(1 for m in ms if m.get("league") or m.get("event"))
print("results中有league/event的场:", hit_has_league, "/", len(ms))
