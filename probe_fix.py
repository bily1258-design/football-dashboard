#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测: results hit 取值分布 + matches JSON HKJC字段名"""
import json, glob
from collections import Counter

data = json.load(open("docs/data/results.json", encoding="utf-8"))
ms = data["matches"]
print("hit 取值分布:", Counter(str(m.get("hit")) for m in ms))
print("score 有无:", Counter(1 if m.get("score") else 0 for m in ms))

# matches JSON 字段
fp = sorted(glob.glob("data/matches_2026*.json"))[-1]
mm = json.load(open(fp, encoding="utf-8"))
if isinstance(mm, dict):
    mm = mm.get("matches", list(mm.values())[0])
print(f"\n{fp}: {len(mm)}场")
m0 = mm[0]
print("字段:", sorted(m0.keys()))
o = m0.get("odds") or {}
print("odds keys:", sorted(o.keys()) if isinstance(o, dict) else o)
print("sample odds:", json.dumps(o, ensure_ascii=False)[:600])
