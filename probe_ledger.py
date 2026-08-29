#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""侦察 betting_ledger.json 结构: 档位/星级/日期分布"""
import json

ledger = json.load(open("/data/data/com.termux/files/home/football-dashboard/docs/data/betting_ledger.json", encoding="utf-8"))
print("type:", type(ledger))
if isinstance(ledger, dict):
    print("keys:", list(ledger.keys())[:10])
    for k, v in list(ledger.items())[:2]:
        print(f"--- {k}: {type(v)}")
        print(json.dumps(v, ensure_ascii=False)[:800])
elif isinstance(ledger, list):
    print("len:", len(ledger))
    for it in ledger[:3]:
        print(json.dumps(it, ensure_ascii=False)[:800])
