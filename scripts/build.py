#!/usr/bin/env python3
"""build.py — 看板生成入口（合并 merge_and_build）

用法：
  python scripts/build.py --db data/football.db
  python scripts/build.py                    # fallback 读 processed/
"""

import subprocess
import sys
import os
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(description="看板生成入口")
    parser.add_argument("--db", help="数据库路径")
    parser.add_argument("--output", default=".", help="输出目录")
    args = parser.parse_args()

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "merge_and_build.py"),
           "--output", args.output]
    if args.db:
        cmd.extend(["--db", args.db])

    print(f"\n{'='*60}")
    print(f"▶ merge_and_build")
    print(f"  cmd: {' '.join(cmd)}")
    print(f"{'='*60}")

    # CWD 保持仓库根目录
    repo_dir = os.path.dirname(SCRIPT_DIR)
    result = subprocess.run(cmd, cwd=repo_dir)
    if result.returncode != 0:
        print(f"❌ build failed (exit={result.returncode})")
        sys.exit(result.returncode)
    print(f"✅ build done")


if __name__ == "__main__":
    main()
