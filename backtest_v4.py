#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测 v4: 加入高命中联赛筛选项
高命中联赛 = 现有HIGH_HIT_LEAGUES + 全量新秀(≥10场 & ≥65%)
"""
import json, glob, statistics
from opencc import OpenCC

_t2s = OpenCC('t2s')
HIGH_HIT = {'芬甲', '国际友谊赛', '日职联', '智利甲', '欧冠杯',
            '挪甲', '挪超', '丹麦超', '欧罗巴杯', '英联杯'}
NEW_HIGH = {'瑞士超', '韩女联', '中北美U20', '意甲', 'GERC', 'Mex MFW',
            '爱甲', '意杯', '奥甲', '东南亞錦標', '丹麥超'}

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
    league = _t2s.convert((r.get("league") or r.get("event") or "").strip())
    rows.append({
        "teams": x["teams"], "win": x["result"]=="win", "odds": x.get("odds"),
        "star": star, "signal": x.get("signal"), "league": league,
        "pin_up": pc>po, "pin_down": pc<po,
        "hk_up": hc>ho, "hk_down": hc<ho, "hk_same": hc==ho,
    })

def stat(name, rs):
    n = len(rs)
    if n == 0:
        print(f"{name}: 0场"); return
    h = sum(1 for r in rs if r["win"])
    print(f"{name}: {n}场 命中{h} {h/n*100:.1f}%")

print(f"有效样本: {len(rows)}\n")
stat("基准(全部)", rows)
stat("★", [r for r in rows if r["star"]])
print()
stat("高命中联赛(现有名单)", [r for r in rows if r["league"] in HIGH_HIT])
stat("高命中联赛(现有)+★", [r for r in rows if r["league"] in HIGH_HIT and r["star"]])
stat("高命中联赛(扩新秀)", [r for r in rows if r["league"] in HIGH_HIT or r["league"] in NEW_HIGH])
stat("   +★", [r for r in rows if (r["league"] in HIGH_HIT or r["league"] in NEW_HIGH) and r["star"]])
print()
stat("非高命中联赛", [r for r in rows if r["league"] not in HIGH_HIT])
stat("   +★", [r for r in rows if r["league"] not in HIGH_HIT and r["star"]])
print()
stat("★ + 高命中联赛(现有)", [r for r in rows if r["star"] and r["league"] in HIGH_HIT])
stat("★ + 非高命中", [r for r in rows if r["star"] and r["league"] not in HIGH_HIT])
print()
stat("新核心 + 高命中联赛(现有)", [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"]) and r["league"] in HIGH_HIT])
stat("新核心 + 高命中 + ★", [r for r in rows if r["pin_up"] and (r["hk_down"] or r["hk_same"]) and r["league"] in HIGH_HIT and r["star"]])
print()
# 池内联赛分布
from collections import Counter
lc = Counter(r["league"] for r in rows)
print("池内联赛分布 top15:", lc.most_common(15))
