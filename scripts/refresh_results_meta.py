#!/usr/bin/env python3
"""refresh_results_meta.py — 重算 results.json 的派生元数据（外科式改库后必跑）。

背景: `results.json` 的 `total_matches` / `date_range` / `daily_stats` / `hit_count` /
`total_scored` / `hit_rate` 都是**上一次全量重建时写死的快照**。用脚本增删过 matches
（比如剔源页残留、剔永久空比分、补「推迟」标记）之后，这些字段不会自动跟着变，
看板会显示对不上的数字。

本脚本严格按 `ai_analysis.py` 写库时的同一口径重算：
  hit_count    = 「hit 字段含 ✓」的条数
  total_scored = hit_count + 「hit == '✘'」的条数
  daily_stats  = 按每行自带 date 分组计数（降序）
只改元数据，**不动 matches 本身**；保持原文件紧凑单行格式；默认 dry-run，
`--apply` 才写盘。跑完接 `python3 scripts/gen_light_results.py`。
"""
import argparse
import json
import os
import re
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO_DIR, "docs", "data", "results.json")


def detect_compact(path):
    raw = open(path, "r", encoding="utf-8").read()
    return "\n" not in raw.strip()


def write_json(path, obj):
    compact = detect_compact(path)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只 dry-run）")
    args = ap.parse_args()

    d = json.load(open(RESULTS, "r", encoding="utf-8"))
    ms = d.get("matches", [])

    hit_count = sum(1 for r in ms if "✓" in (r.get("hit") or ""))
    total_scored = hit_count + sum(1 for r in ms if (r.get("hit") or "") == "✘")
    hit_rate = round(hit_count / total_scored, 3) if total_scored else 0.0

    by_date = {}
    for r in ms:
        k = (r.get("date") or r.get("match_time") or "")[:10]
        by_date.setdefault(k, 0)
        by_date[k] += 1
    dates = sorted(by_date.keys(), reverse=True)

    new = {
        "total_matches": len(ms),
        "date_range": f"{min(dates)} ~ {max(dates)}" if dates else "无数据",
        "hit_count": hit_count,
        "total_scored": total_scored,
        "hit_rate": hit_rate,
        "daily_stats": [{"date": k, "count": by_date[k]} for k in dates],
    }

    changed = []
    for k, v in new.items():
        old = d.get(k)
        if old != v:
            changed.append(f"  {k}: {old if not isinstance(old, list) else f'[{len(old)}天]'} → {v if not isinstance(v, list) else f'[{len(v)}天]'}")

    print(f"matches {len(ms)} 场 | 盘中不一致字段 {len(changed)} 个")
    for line in changed:
        print(line)
    if not changed:
        print("元数据已一致，无需处理。")
        return 0
    if not args.apply:
        print("\n[dry-run] 加 --apply 才写盘。")
        return 0

    d.update(new)
    write_json(RESULTS, d)
    print("✅ 已重算元数据 →", RESULTS)
    print("→ 接着跑: python3 scripts/gen_light_results.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
