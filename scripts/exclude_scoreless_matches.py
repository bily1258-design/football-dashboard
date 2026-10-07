#!/usr/bin/env python3
"""exclude_scoreless_matches.py — 清掉「已开赛很久仍无比分」的积压场次（fid 级）。

判定（三条同时成立才算，避免误伤）：
  1) 比赛日期距今 > --min-age 天（默认 10）
  2) 库里该场 score 为空（`推迟` 算已标，不动）
  3) **全仓日档里没有任何带比分的副本**（有分数副本的一律放过）

动作（--apply 才写盘；默认 dry-run）：
  a) 把这些 fid 记进 `data/excluded_fids.json` → 结果库组装时跳过，重建不会灌回来
  b) 从所有 `data/matches_*.json` 日档里删掉这些记录
  c) 从 `docs/data/results.json` 里删掉这些记录（保持原文件格式: 单行紧凑 / 缩进）

之后跑 `python3 scripts/gen_light_results.py` 刷新派生库。
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fid_blocklist import load_blocklist, save_blocklist  # noqa: E402

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_DIR, "data")
RESULTS = os.path.join(REPO_DIR, "docs", "data", "results.json")


def parse_day(s):
    s = (s or "")[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def detect_format(path):
    """返回 (pretty: bool, indent: int|None) —— 读原文件写法，避免整文件 diff 爆炸。"""
    try:
        raw = open(path, "r", encoding="utf-8").read()
    except Exception:
        return False, None
    if "\n" not in raw.strip():
        return False, None
    m = re.search(r"\n(\s+)\"", raw)
    if m:
        return True, len(m.group(1))
    return True, 2


def write_json(path, obj):
    pretty, indent = detect_format(path)
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=indent or 2)
            f.write("\n")
        else:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def collect_state(today, min_age):
    """返回 (targets: {fid: info}, scored_fids: set)"""
    scored, empty, meta, copies = set(), {}, {}, {}
    for fp in sorted(os.listdir(DATA_DIR)):
        if not (fp.startswith("matches_") and fp.endswith(".json")):
            continue
        path = os.path.join(DATA_DIR, fp)
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️ 读取失败 {fp}: {e}")
            continue
        ms = data.get("matches", data if isinstance(data, list) else [])
        for m in ms if isinstance(ms, list) else []:
            fid = str(m.get("fid") or m.get("id") or "")
            if not fid:
                continue
            s = (m.get("score") or "").strip()
            copies[fid] = copies.get(fid, 0) + 1
            if s:
                scored.add(fid)
            else:
                empty.setdefault(fid, []).append((fp, m.get("date"), m.get("home_team"), m.get("away_team")))

    lib = json.load(open(RESULTS, "r", encoding="utf-8"))
    lib_ms = lib.get("matches", [])
    targets = {}
    for m in lib_ms:
        fid = str(m.get("fid") or "")
        if not fid or fid in scored:
            continue
        s = (m.get("score") or "").strip()
        if s:                      # 有比分 / 标了「推迟」 → 不动
            continue
        d = parse_day(m.get("date"))
        if not d:
            continue
        age = (today - d).days
        if age <= min_age:
            continue
        targets[fid] = {
            "team": f"{m.get('home_team','')}-{m.get('away_team','')}",
            "date": (m.get("date") or "")[:10],
            "age_days": age,
            "league": m.get("league_cn") or m.get("league") or "",
        }
    return targets, scored, copies, lib_ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只 dry-run）")
    ap.add_argument("--min-age", type=int, default=10, help="开赛距今超过几天算积压（默认 10）")
    ap.add_argument("--date", default=date.today().isoformat(), help="今天日期 YYYY-MM-DD")
    args = ap.parse_args()
    today = parse_day(args.date) or date.today()

    targets, scored, copies, lib_ms = collect_state(today, args.min_age)
    print(f"库里总 {len(lib_ms)} 场 | 命中「>{args.min_age}天仍无比分且全仓无带分副本」 {len(targets)} 场")
    for fid, info in sorted(targets.items(), key=lambda kv: kv[1]["date"]):
        print(f"  fid={fid:<9} {info['date']} (隔{info['age_days']:>3}天) {info['league'][:6]:<7} {info['team']}")

    if not targets:
        print("无需处理。")
        return 0
    if not args.apply:
        print("\n[dry-run] 加 --apply 才写盘。")
        return 0

    # a) 记进屏蔽名单
    bl = load_blocklist()
    for fid, info in targets.items():
        bl.setdefault("fids", {})[str(fid)] = {
            "team": info["team"], "date": info["date"], "league": info["league"],
            "reason": "比分源无覆盖(永久空比分)", "added": args.date,
        }
    bl["updated"] = args.date
    save_blocklist(bl)
    print(f"✅ 屏蔽名单 → {len(bl['fids'])} 条")

    # b) 日档删记录
    removed_day = 0
    for fp in sorted(os.listdir(DATA_DIR)):
        if not (fp.startswith("matches_") and fp.endswith(".json")):
            continue
        path = os.path.join(DATA_DIR, fp)
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
        except Exception:
            continue
        ms = data.get("matches", data if isinstance(data, list) else [])
        if not isinstance(ms, list):
            continue
        keep = [m for m in ms if str(m.get("fid") or m.get("id") or "") not in targets]
        if len(keep) != len(ms):
            removed_day += len(ms) - len(keep)
            if isinstance(data, dict):
                data["matches"] = keep
                write_json(path, data)
            else:
                write_json(path, keep)
    print(f"✅ 日档删除 {removed_day} 行")

    # c) 结果库删记录
    lib = json.load(open(RESULTS, "r", encoding="utf-8"))
    before = len(lib.get("matches", []))
    lib["matches"] = [m for m in lib.get("matches", []) if str(m.get("fid") or "") not in targets]
    after = len(lib["matches"])
    write_json(RESULTS, lib)
    print(f"✅ results.json {before} → {after} (删 {before - after})")
    print("→ 接着跑: python3 scripts/gen_light_results.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
