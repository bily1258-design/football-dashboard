#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测 v3: 核心组合 = 平博升水 + (HKJC掉水或不变)"""
import json, glob, statistics

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

rows = []
for x in settled:
    fid = str(x["fid"])
    r, h = res.get(fid), hk.get(fid)
    if not r or not h:
        continue
    side = "l" if x["outcome"] == "away" else "w"
    po = r.get("open_win_pin" if side=="w" else "open_loss_pin")
    pc = r.get("odds_win" if side=="w" else "odds_loss")
    ho = h.get("odds_hkjc_open_win" if side=="w" else "odds_hkjc_open_loss")
    hc = h.get("odds_hkjc_win" if side=="w" else "odds_hkjc_loss")
    if not all([po, pc, ho, hc]):
        continue
    star = (x.get("odds") or 0) < 2.0 and (r.get("ts_draw") is not None and r["ts_draw"] < 25)
    rows.append({
        "teams": x["teams"], "win": x["result"]=="win", "odds": x.get("odds"),
        "star": star, "signal": x.get("signal"),
        "pin_up": pc>po, "pin_down": pc<po, "pin_same": pc==po,
        "hk_up": hc>ho, "hk_down": hc<ho, "hk_same": hc==ho,
    })

def stat(name, rs):
    n = len(rs)
    if n == 0:
        print(f"{name}: 0场"); return
    h = sum(1 for r in rs if r["win"])
    odds = [r["odds"] for r in rs if r["odds"]]
    print(f"{name}: {n}场 命中{h} {h/n*100:.1f}% (赔率中位{statistics.median(odds):.2f})")

print(f"有效样本: {len(rows)}\n")
stat("基准(全部)", rows)
stat("★星级", [r for r in rows if r["star"]])
stat("无★", [r for r in rows if not r["star"]])
print()
stat("【新核心】平博升水+HKJC(掉水或不变)", [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"])])
stat("   +★", [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"]) and r["star"]])
stat("   +无★", [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"]) and not r["star"]])
print()
stat("旧组合 平博升水+HKJC掉水", [r for r in rows if r["pin_up"] and r["hk_down"]])
stat("   +★", [r for r in rows if r["pin_up"] and r["hk_down"] and r["star"]])
print()
stat("★+平博升水(不限HKJC)", [r for r in rows if r["star"] and r["pin_up"]])
print()
stat("红线A HKJC升水(不碰)", [r for r in rows if r["hk_up"]])
stat("红线B 平博掉水(不碰)", [r for r in rows if r["pin_down"]])
print()
# 新核心组合里的★明细
core = [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"]) and r["star"]]
print("新核心+★明细:")
for r in core:
    print(f"  {r['teams']} odds={r['odds']} sig={r['signal']} 结果={'✓' if r['win'] else '✗'}")
