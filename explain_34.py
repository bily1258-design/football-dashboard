#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解释第3/4点的证据脚本"""
import json, glob

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
    po, pc = r.get("open_win_pin" if side=="w" else "open_loss_pin"), r.get("odds_win" if side=="w" else "odds_loss")
    ho, hc = h.get("odds_hkjc_open_win" if side=="w" else "odds_hkjc_open_loss"), h.get("odds_hkjc_win" if side=="w" else "odds_hkjc_loss")
    if not all([po, pc, ho, hc]):
        continue
    star = (x.get("odds") or 0) < 2.0 and (r.get("ts_draw") is not None and r["ts_draw"] < 25)
    rows.append({
        "teams": x["teams"], "win": x["result"]=="win", "odds": x.get("odds"),
        "star": star, "signal": x.get("signal"), "ts_draw": r.get("ts_draw"),
        "pin_up": pc>po, "pin_down": pc<po, "hk_up": hc>ho, "hk_down": hc<ho,
    })

print(f"有效样本: {len(rows)}")
# --- 4点: ★ vs 无★ 构成 ---
for label, rs in [("★", [r for r in rows if r["star"]]), ("无★", [r for r in rows if not r["star"]])]:
    import statistics
    odds = [r["odds"] for r in rs if r["odds"]]
    sig = {}
    for r in rs:
        sig[r["signal"]] = sig.get(r["signal"], 0) + 1
    print(f"{label}: {len(rs)}场 命中率{sum(r['win'] for r in rs)/len(rs)*100:.1f}% | 赔率中位{statistics.median(odds):.2f} | 信号构成{sig}")

# signal=weight 里★占比
w_all = [r for r in rows if r["signal"]=="weight"]
w_star = [r for r in w_all if r["star"]]
print(f"\nweight组: {len(w_all)}场, ★占{len(w_star)} ({len(w_star)/len(w_all)*100:.0f}%), 命中{sum(r['win'] for r in w_all)/len(w_all)*100:.1f}%")
v_all = [r for r in rows if r["signal"]=="value"]
print(f"value组: {len(v_all)}场, ★占{sum(1 for r in v_all if r['star'])} , 平均赔率{sum(r['odds'] for r in v_all)/len(v_all):.2f}")

# --- 3点: 核心组合21场的构成 ---
core = [r for r in rows if r["pin_up"] and r["hk_down"]]
print(f"\n平博升水+HKJC掉水: {len(core)}场")
for r in core:
    print(f"  {r['teams']} odds={r['odds']} sig={r['signal']} star={r['star']} 结果={'✓' if r['win'] else '✗'}")

# 为什么★∩组合=0: 看★24场的升降水分布
star_rows = [r for r in rows if r["star"]]
combos = {}
for r in star_rows:
    k = f"pin{'↑' if r['pin_up'] else '↓' if r['pin_down'] else '='}+hk{'↑' if r['hk_up'] else '↓' if r['hk_down'] else '='}"
    combos[k] = combos.get(k, 0) + 1
print(f"\n★24场的升降水组合: {combos}")
# 无★里组合占比
nstar_core = [r for r in rows if not r["star"] and r["pin_up"] and r["hk_down"]]
print(f"无★里符合组合的: {len(nstar_core)}/{len([r for r in rows if not r['star']])}")

# 投注池 vs 全量的赔率结构
all_rows = data["matches"]
pool_odds = [r["odds"] for r in rows if r["odds"]]
full_odds = [m.get("odds_win") for m in all_rows if m.get("odds_win")]
import statistics
print(f"\n投注池赔率中位: {statistics.median(pool_odds):.2f} vs 全量赔率中位: {statistics.median(full_odds):.2f}")
print(f"投注池赔率<2.0占比: {sum(1 for o in pool_odds if o<2.0)/len(pool_odds)*100:.0f}% vs 全量: {sum(1 for o in full_odds if o<2.0)/len(full_odds)*100:.0f}%")
