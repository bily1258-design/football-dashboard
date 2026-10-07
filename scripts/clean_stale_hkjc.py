#!/usr/bin/env python3
"""清洗 data/matches_hkjc_*.json 里的「源页残留」场次。

背景：titan007 港彩赔率页会长期回带一批未结算的历史场次（比分恒为空，
如 2026 世界杯淘汰赛 fid 2907402~2907407），抓取脚本原样收下后写进当日档，
ai_analysis 每次全量重写 results.json 时会把它累积进库 → 永久驻留、永不结算。

规则：比赛日期早于该档日期 10 天 且 score 为空 → 判为残留丢弃（score='推迟' 保留）。
用法：
    python3 scripts/clean_stale_hkjc.py            # 只报告(dry-run)
    python3 scripts/clean_stale_hkjc.py --apply    # 实际写盘
注意：改写时保留每个文件**原有**格式（紧凑单行 / indent=2），否则会产生整文件空白 diff。
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cleaner():
    spec = importlib.util.spec_from_file_location(
        "fetch_hkjc_all", os.path.join(ROOT, "scripts", "fetch_hkjc_all.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写盘（默认仅报告）")
    ap.add_argument("--glob", default="data/matches_hkjc_*.json")
    args = ap.parse_args()

    fha = load_cleaner()
    total_files = hit_files = total_dropped = total_dups = 0
    for f in sorted(glob.glob(os.path.join(ROOT, args.glob))):
        total_files += 1
        m = re.search(r"(\d{8})", os.path.basename(f))
        if not m:
            continue
        d = m.group(1)
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        raw = open(f, encoding="utf-8").read()
        pretty = "\n" in raw
        obj = json.loads(raw)
        kept, dropped, dups = fha.clean_day_matches(obj.get("matches", []), date_str)
        if not (dropped or dups):
            continue
        hit_files += 1
        total_dropped += len(dropped)
        total_dups += len(dups)
        names = ", ".join(f"{x.get('home_team')}-{x.get('away_team')}({str(x.get('date'))[:10]})"
                          for x in dropped[:3])
        print(f"{os.path.basename(f)}: -{len(dropped)} 残留 -{len(dups)} 重复  {names}")
        if args.apply:
            obj["matches"] = kept
            obj["total"] = len(kept)
            txt = json.dumps(obj, ensure_ascii=False,
                             **({"indent": 2} if pretty else {"separators": (",", ":")}))
            open(f, "w", encoding="utf-8").write(txt)
    print(f"\n{'已写盘' if args.apply else 'DRY-RUN'}: 扫描 {total_files} 档，命中 {hit_files} 档，"
          f"剔除残留 {total_dropped} 条，去重 {total_dups} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
