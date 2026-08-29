#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测用户想法 v2: 池=投注簿betting_ledger
信号: 推荐方向 平博升水 + HKJC掉水 → ★加倍/无★正常
红线: 推荐方向HKJC升水 不碰; 平博掉水 不碰
"""
import json, glob
from collections import Counter

# ---------- 投注池 ----------
ledger = json.load(open("docs/data/betting_ledger.json", encoding="utf-8"))
settled = [x for x in ledger if x.get("result") in ("win", "loss")]
print(f"投注池: {len(ledger)}条, 已结算 {len(settled)} (win+loss)")

# ---------- results.json: fid -> 平博双时点 + ts_draw + 赛果 ----------
data = json.load(open("docs/data/results.json", encoding="utf-8"))
res = {}
for m in data["matches"]:
    fid = str(m.get("fid"))
    res[fid] = {
        "pin_open": {"w": m.get("open_win_pin"), "l": m.get("open_loss_pin")},
        "pin_close": {"w": m.get("odds_win"), "l": m.get("odds_loss")},
        "ts_draw": m.get("ts_draw"),
        "hit": m.get("hit", ""),
    }

# ---------- matches JSON: fid -> HKJC 双时点 ----------
hk = {}
for fp in sorted(glob.glob("data/matches_2026*.json")):
    try:
        mm = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    if isinstance(mm, dict):
        mm = mm.get("matches", [])
    for m in mm:
        fid = str(m.get("fid"))
        if not fid:
            continue
        hk[fid] = {
            "open": {"w": m.get("odds_hkjc_open_win"), "l": m.get("odds_hkjc_open_loss")},
            "close": {"w": m.get("odds_hkjc_win"), "l": m.get("odds_hkjc_loss")},
        }
print(f"results 可join: {sum(1 for r in settled if str(r['fid']) in res)}")
print(f"HKJC 可join: {sum(1 for r in settled if str(r['fid']) in hk)}")

# ---------- 组装 ----------
rows = []
for x in settled:
    fid = str(x["fid"])
    r = res.get(fid)
    h = hk.get(fid)
    if not r or not h:
        continue
    side = "l" if x["outcome"] == "away" else "w"  # 推荐方向
    po, pc = r["pin_open"][side], r["pin_close"][side]
    ho, hc = h["open"][side], h["close"][side]
    if not all([po, pc, ho, hc]):
        continue
    star = (x.get("odds") or 0) < 2.0 and (r["ts_draw"] is not None and r["ts_draw"] < 25)
    rows.append({
        "teams": x["teams"], "date": x["match_time"][:10],
        "win": x["result"] == "win",
        "odds": x.get("odds"),
        "star": star,
        "pin_up": pc > po, "pin_down": pc < po,
        "hk_up": hc > ho, "hk_down": hc < ho,
        "signal": x.get("signal"),
    })
print(f"有效回测样本(双时点齐全): {len(rows)}\n")

# ---------- 统计 ----------
def stat(name, rs):
    n = len(rs)
    if n == 0:
        print(f"{name}: 0场"); return
    h = sum(1 for r in rs if r["win"])
    print(f"{name}: {n}场 命中{h} {h/n*100:.1f}%")

stat("基准(全部)", rows)
stat("★星级", [r for r in rows if r["star"]])
stat("无★", [r for r in rows if not r["star"]])
print()
stat("平博升水", [r for r in rows if r["pin_up"]])
stat("平博掉水", [r for r in rows if r["pin_down"]])
stat("HKJC掉水", [r for r in rows if r["hk_down"]])
stat("HKJC升水", [r for r in rows if r["hk_up"]])
print()
stat("【用户核心】平博升水+HKJC掉水", [r for r in rows if r["pin_up"] and r["hk_down"]])
stat("   +★", [r for r in rows if r["pin_up"] and r["hk_down"] and r["star"]])
stat("   +无★", [r for r in rows if r["pin_up"] and r["hk_down"] and not r["star"]])
print()
stat("红线A HKJC升水(不碰)", [r for r in rows if r["hk_up"]])
stat("红线B 平博掉水(不碰)", [r for r in rows if r["pin_down"]])
stat("红线A∩B", [r for r in rows if r["hk_up"] and r["pin_down"]])
print()
stat("★+HKJC掉水", [r for r in rows if r["star"] and r["hk_down"]])
stat("★+平博掉水", [r for r in rows if r["star"] and r["pin_down"]])
stat("★+平博升水", [r for r in rows if r["star"] and r["pin_up"]])
stat("★+HKJC升水", [r for r in rows if r["star"] and r["hk_up"]])
print()
# 按信号分组
for sig in ("value", "ruleA", "weight"):
    stat(f"signal={sig}", [r for r in rows if r["signal"] == sig])
