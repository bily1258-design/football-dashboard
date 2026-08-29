#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池内高命中联赛明细"""
import json, glob
from opencc import OpenCC

_t2s = OpenCC('t2s')
HIGH_HIT = {'芬甲', '国际友谊赛', '日职联', '智利甲', '欧冠杯',
            '挪甲', '挪超', '丹麦超', '欧罗巴杯', '英联杯'}

ledger = json.load(open("docs/data/betting_ledger.json", encoding="utf-8"))
settled = [x for x in ledger if x.get("result") in ("win", "loss")]
data = json.load(open("docs/data/results.json", encoding="utf-8"))
res = {str(m.get("fid")): m for m in data["matches"]}
hk = {}
for fp in sorted(glob.glob("data/matches_2026*.json")):
    try:
        mm = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    if isinstance(mm, dict):
        mm = mm.get("matches", [])
    for m in mm:
        hk[str(m.get("fid"))] = m

for x in settled:
    fid = str(x["fid"])
    r = res.get(fid)
    if not r:
        continue
    league = _t2s.convert((r.get("league") or r.get("event") or "").strip())
    if league in HIGH_HIT:
        h = hk.get(fid, {})
        side = "l" if x["outcome"] == "away" else "w"
        print(f"{league:8s} | {x['teams']:28s} | odds={x.get('odds'):<6} | sig={x.get('signal')!s:<6} | 方向={x['outcome']} | {'✓' if x['result']=='win' else '✗'}")
