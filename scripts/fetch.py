#!/usr/bin/env python3
"""fetch.py — 赔率数据抓取入口（合并 odds_api + fetch_pinnacle_odds）

用法：
  python scripts/fetch.py                     # 抓今天
  python scripts/fetch.py --date 2026-06-18   # 指定日期
  python scripts/fetch.py --date 2026-06-18 --odds-only  # 只抓百家平均
"""

import subprocess
import sys
import os
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(cmd, label, required=True):
    """运行子脚本，失败时根据 required 决定是否终止"""
    print(f"\n{'='*60}")
    print(f"▶ {label}")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"{'='*60}")
    # CWD 保持仓库根目录，不要设为 scripts/，否则 --db data/xxx 路径会错
    repo_dir = os.path.dirname(SCRIPT_DIR)
    result = subprocess.run(cmd, cwd=repo_dir)
    if result.returncode != 0:
        msg = f"⚠️ {label} failed (exit={result.returncode})"
        if required:
            print(f"❌ {msg} — 终止流程")
            sys.exit(result.returncode)
        else:
            print(f"{msg} — 继续下一步")
    else:
        print(f"✅ {label} done")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="赔率数据抓取入口")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--odds-only", action="store_true",
                        help="只抓百家平均(odds_api)，跳过 Pinnacle/HKJC")
    args = parser.parse_args()

    date = args.date or datetime.now().strftime('%Y-%m-%d')

    # Step 1: 足彩网百家平均 + 各公司欧赔（网络问题可容忍）
    run([sys.executable, os.path.join(SCRIPT_DIR, "odds_api.py"),
         "--date", date],
        label="1/2 odds_api (百家平均+欧赔)",
        required=False)

    # Step 2: Pinnacle / HKJC / 亚盘 写入 DB
    if not args.odds_only:
        run([sys.executable, os.path.join(SCRIPT_DIR, "fetch_pinnacle_odds.py"),
             "--date", date],
            label="2/2 fetch_pinnacle_odds (Pinnacle+HKJC+AH)",
            required=False)
    else:
        print("⏭️ 跳过 Pinnacle/HKJC (--odds-only)")


if __name__ == "__main__":
    main()
