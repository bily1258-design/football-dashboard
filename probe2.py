#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测: 1) ledger 的 signal 分布; 2) results.json matches 结构"""
import json
from collections import Counter

ledger = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/betting_ledger.json", encoding="utf-8"))
print("ledger signal 分布:", Counter(x.get("signal") for x in ledger))
print("ledger signal_cn 分布:", Counter(x.get("signal_cn") for x in ledger))
print("ledger result 分布:", Counter(x.get("result") for x in ledger))
print("ledger match_time 日期分布(前10):", Counter(x.get("match_time","")[:10] for x in ledger).most_common(10))
print("ledger 有score:", sum(1 for x in ledger if x.get("score")), "/", len(ledger))

data = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/results.json", encoding="utf-8"))
m = data["matches"]
print("\nresults matches type:", type(m), "len:", len(m))
sample = m[0] if isinstance(m, list) else list(m.values())[0]
print(json.dumps(sample, ensure_ascii=False, indent=1)[:1500])
