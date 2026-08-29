#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""侦察 results.json 结构: 平博双时点+hit 字段"""
import json

data = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/results.json", encoding="utf-8"))
print("type:", type(data))
if isinstance(data, dict):
    print("keys:", list(data.keys())[:10])
    v = data[list(data.keys())[0]]
    print("sample:", json.dumps(v, ensure_ascii=False)[:1000])
elif isinstance(data, list):
    print("len:", len(data))
    print(json.dumps(data[0], ensure_ascii=False)[:1000])
