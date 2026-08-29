#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测用户新想法:
池 = ①②③档规则 (model=LGBM一致; ②客 ③主 且TS同向; ①一方概率>44.9%)
信号 = 推荐方向 平博升水+HKJC掉水 → 加倍(有★)/正常(无★)
红线 = 推荐方向 HKJC升水 不碰; 平博掉水 不碰
"""
import json, glob, re
from collections import Counter

# ---------- 1. results.json 主表 ----------
data = json.load(open("docs/data/results.json", encoding="utf-8"))
ms = data["matches"]
print(f"results: {len(ms)}")

# ---------- 2. matches JSON 补 HKJC 双时点 ----------
hk = {}
for fp in sorted(glob.glob("data/matches_2026*.json")):
    try:
        mm = json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        print("skip", fp, e); continue
    if isinstance(mm, dict):
        mm = mm.get("matches", list(mm.values())[0] if mm else [])
    for m in mm:
        fid = str(m.get("fid", m.get("id", "")))
        if not fid:
            continue
        o = m.get("odds") or {}
        if isinstance(o, dict):
            hk[fid] = {
                "open": {"w": o.get("odds_hkjc_open_win") or o.get("hkjc_open_win"),
                          "d": o.get("odds_hkjc_open_draw") or o.get("hkjc_open_draw"),
                          "l": o.get("odds_hkjc_open_loss") or o.get("hkjc_open_loss")},
                "close": {"w": o.get("odds_hkjc_win") or o.get("hkjc_win"),
                           "d": o.get("odds_hkjc_draw") or o.get("hkjc_draw"),
                           "l": o.get("odds_hkjc_loss") or o.get("hkjc_loss")},
            }
print(f"HKJC双时点可join: {len(hk)}")

# ---------- 3. 池 + 星级 + 信号 ----------
def pick(pred, side):
    """pred: 'home'/'away'; side: 'win'/'loss'"""
    return "win" if pred == "home" else "loss"

rows = []
for m in ms:
    if not m.get("hit"):
        continue
    fid = str(m.get("fid"))
    pred = m.get("prediction") or m.get("model_prediction")
    lgbm = m.get("lgbm_prediction")
    if not pred or not lgbm:
        continue
    # ① 高置信: model=LGBM一致 且 一方概率>44.9%
    mp = m.get("model_prediction_prob", 0)
    if pred == lgbm and mp > 0.449:
        tier1 = True
    else:
        tier1 = False
    # ②③ 三方一致: model=lgbm=TS 同向
    ts_side = None
    ts = [m.get("ts_win"), m.get("ts_draw"), m.get("ts_loss")]
    if ts and all(x is not None for x in ts):
        mx = max(range(3), key=lambda i: ts[i])
        ts_side = ["home", "draw", "away"][mx]
    tier23 = (pred == lgbm == ts_side) and pred in ("home", "away")
    if not (tier1 or tier23):
        continue
    # 星级: 推荐方向赔率<2.0 且 TS平<25%
    side = pick(pred, None)
    odds = m.get("odds_win" if side == "win" else "odds_loss", 0) or 0
    tsd = m.get("ts_draw")
    star = odds < 2.0 and tsd is not None and tsd < 25
    # 平博 open->close 推荐方向
    o_win, c_win = m.get("open_win_pin"), m.get("odds_win")
    o_los, c_los = m.get("open_loss_pin"), m.get("odds_loss")
    po = o_win if side == "win" else o_los
    pc = c_win if side == "win" else c_los
    pin_up = pin_down = None
    if po and pc:
        pin_up = pc > po
        pin_down = pc < po
    # HKJC open->close
    h = hk.get(fid)
    hk_up = hk_down = None
    if h:
        ho = h["open"]["w" if side == "win" else "l"]
        hc = h["close"]["w" if side == "win" else "l"]
        if ho and hc:
            hk_up = hc > ho
            hk_down = hc < ho
    rows.append({
        "fid": fid, "teams": f"{m.get('home_team')} vs {m.get('away_team')}",
        "date": m.get("date"), "pred": pred, "hit": m.get("hit") in ("✓", "✔", "1", True),
        "star": star, "pin_up": pin_up, "pin_down": pin_down,
        "hk_up": hk_up, "hk_down": hk_down,
        "odds": odds, "tsd": tsd, "tier1": tier1, "tier23": tier23,
    })

print(f"池内(①或②③)有赛果: {len(rows)}")

# ---------- 4. 统计 ----------
def stat(name, rs):
    n = len(rs)
    if n == 0:
        print(f"{name}: 0场")
        return
    h = sum(1 for r in rs if r["hit"])
    print(f"{name}: {n}场 命中{h} {h/n*100:.1f}%")

base = rows
stat("基准(池内全部)", base)
stat("★星级", [r for r in base if r["star"]])
stat("无★", [r for r in base if not r["star"]])
print()
stat("平博升水", [r for r in base if r["pin_up"]])
stat("平博掉水", [r for r in base if r["pin_down"]])
stat("HKJC掉水", [r for r in base if r["hk_down"]])
stat("HKJC升水", [r for r in base if r["hk_up"]])
print()
stat("平博升水+HKJC掉水", [r for r in base if r["pin_up"] and r["hk_down"]])
stat("平博升水+HKJC掉水+★", [r for r in base if r["pin_up"] and r["hk_down"] and r["star"]])
stat("平博升水+HKJC掉水+无★", [r for r in base if r["pin_up"] and r["hk_down"] and not r["star"]])
print()
# 红线验证: 推荐方向 HKJC升水 / 平博掉水
stat("红线A HKJC升水(用户说不碰)", [r for r in base if r["hk_up"]])
stat("红线B 平博掉水(用户说不碰)", [r for r in base if r["pin_down"]])
stat("红线A∩B", [r for r in base if r["hk_up"] and r["pin_down"]])
print()
# 星级 × 赔率走向
stat("★+HKJC掉水", [r for r in base if r["star"] and r["hk_down"]])
stat("★+平博掉水", [r for r in base if r["star"] and r["pin_down"]])
stat("★+HKJC升水", [r for r in base if r["star"] and r["hk_up"]])
stat("★+平博升水", [r for r in base if r["star"] and r["pin_up"]])
stat("★+(平博掉水或HKJC掉水)", [r for r in base if r["star"] and (r["pin_down"] or r["hk_down"])])
print()
# ①档单独
t1 = [r for r in base if r["tier1"]]
print("--- ①档内 ---")
stat("①基准", t1)
stat("①★", [r for r in t1 if r["star"]])
stat("①平博升水+HKJC掉水", [r for r in t1 if r["pin_up"] and r["hk_down"]])
stat("①★+掉水(任一)", [r for r in t1 if r["star"] and (r["pin_down"] or r["hk_down"])])
print()
# ②③档单独
t23 = [r for r in base if r["tier23"]]
print("--- ②③档内 ---")
stat("②③基准", t23)
stat("②③★", [r for r in t23 if r["star"]])
stat("②③平博升水+HKJC掉水", [r for r in t23 if r["pin_up"] and r["hk_down"]])
stat("②③★+掉水(任一)", [r for r in t23 if r["star"] and (r["pin_down"] or r["hk_down"])])
