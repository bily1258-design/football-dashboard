#!/usr/bin/env python3
import json, os, math
from datetime import datetime
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
def convert(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    dc = date_str.replace("-","")
    om_path = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet", f"{dc}.json")
    if not os.path.exists(om_path):
        print("no data")
        return False
    with open(om_path, "r", encoding="utf-8") as fh:
        om = json.load(fh)
    matches = om.get("matches", {})
    if isinstance(matches, list):
        matches = {f"{m.get('info',{}).get('home','')}_{m.get('info',{}).get('away','')}": m for m in matches}
    real_odds = {}
    for k, match in matches.items():
        info = match.get("info",{})
        odds = match.get("odds",{})
        home = info.get("home","")
        away = info.get("away","")
        if not home or not away:
            continue
        avg = odds.get("avg",{})
        entry = {
            "home": avg.get("odds_w"), "draw": avg.get("odds_d"), "away": avg.get("odds_l"),
            "avg_margin": avg.get("margin"),
            "matchNum": info.get("number",""), "league": info.get("league",""), "kickoff": info.get("kickoff","")
        }
        for src in ["pinnacle", "hkjc"]:
            s = odds.get(src, {})
            if s:
                entry[f"{src}_open_w"] = s.get("odds_w")
                entry[f"{src}_open_d"] = s.get("odds_d")
                entry[f"{src}_open_l"] = s.get("odds_l")
        hhad = odds.get("hhad", {})
        if hhad:
            entry["hhad_home"] = hhad.get("odds_w")
            entry["hhad_draw"] = hhad.get("odds_d")
            entry["hhad_away"] = hhad.get("odds_l")
            entry["hhad_handicap"] = hhad.get("handicap")
        entry = {k2:v for k2,v in entry.items() if v is not None}
        real_odds[f"{home} vs {away}"] = entry
    cache_dir = os.path.join(REPO_DIR, "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, "real_odds.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(real_odds, fh, ensure_ascii=False, indent=2)
    print(f"OK {len(real_odds)} -> {out}")
    return True
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    a = p.parse_args()
    convert(a.date)
