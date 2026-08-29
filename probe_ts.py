#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 results.json: TS字段/prediction一致性/星级判定可用字段"""
import json
from collections import Counter

data = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/results.json", encoding="utf-8"))
ms = data["matches"]
print("total:", len(ms))

# 找 TS 相关键
ts_keys = set()
for m in ms[:500]:
    for k in m:
        if "ts" in k.lower() or "titan" in k.lower():
            ts_keys.add(k)
print("TS相关字段:", sorted(ts_keys))

# 样本: 判断 prediction 字段是否三向一致
agree = 0
for m in ms:
    if m.get("model_prediction") == m.get("lgbm_prediction") == m.get("prediction"):
        agree += 1
print(f"model=lgbm=prediction 一致: {agree}/{len(ms)}")

# 看有没有 draw 概率可用于 TS平
m0 = ms[0]
print("\n字段列表:", sorted(m0.keys()))
