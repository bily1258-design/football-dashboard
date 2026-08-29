#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 ledger 完整字段 + 星级 + 档位"""
import json
from collections import Counter

ledger = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/betting_ledger.json", encoding="utf-8"))
# 所有键
keys = set()
for x in ledger:
    keys.update(x.keys())
print("字段:", sorted(keys))
print()
# 有星级的条目
starred = [x for x in ledger if x.get("star") or x.get("starred") or x.get("tier") or x.get("level")]
print("有 tier/level/star 字段的条数:", len(starred))
if starred:
    print(json.dumps(starred[0], ensure_ascii=False)[:800])
print()
# 样本: 今天(08-29)的条目
today = [x for x in ledger if x.get("match_time","").startswith("2026-08-29")]
print("08-29 条目:", len(today))
for x in today[:5]:
    print(json.dumps(x, ensure_ascii=False)[:400])
