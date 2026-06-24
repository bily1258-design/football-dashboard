#!/usr/bin/env python3
"""pipeline.py — 预测+赔率回填+融合+EV+Kelly+去重+对齐+推送 全链路入口

步骤顺序硬编码，从根本上杜绝 yml 排列出错：
  1. predict_from_odds    — OM赔率 → 泊松预测 INSERT DB
  2. fetch_pinnacle_odds  — Pinnacle/HKJC/威廉初终盘 UPDATE DB
  3. calc_lambda          — 补算 lambda（对 jingcai 等无 lambda 的记录）
  4. update_db_fusion     — LGBM融合概率填充
  5. value_bet --all      — EV 重算
  6. update_db_kelly      — Kelly 重算
  7. align_and_merge --cleanup-db  — 去重复
  8. align_and_merge --all         — 对齐合并 → processed/
  9. push_db              — DB 推 Release

用法：
  python scripts/pipeline.py --date 2026-06-18 --db data/football.db
  python scripts/pipeline.py --date 2026-06-18 --db data/football.db --skip-push
"""

import subprocess
import sys
import os
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(cmd, label, required=True):
    """运行子脚本"""
    print(f"\n{'='*60}")
    print(f"▶ [{label}]")
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
    parser = argparse.ArgumentParser(description="预测+赔率回填+融合+EV+Kelly 全链路")
    parser.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--skip-push", action="store_true",
                        help="跳过 push_db（本地测试用）")
    parser.add_argument("--skip-predict", action="store_true",
                        help="跳过 predict（已有预测时）")
    args = parser.parse_args()

    date = args.date
    db = args.db

    # 1. 泊松预测
    if not args.skip_predict:
        run([sys.executable, os.path.join(SCRIPT_DIR, "predict_from_odds.py"),
             "--date", date, "--db", db],
            label="1/9 predict_from_odds",
            required=False)
    else:
        print("⏭️ 跳过 predict (--skip-predict)")

    # 2. Pinnacle/HKJC/威廉初终盘回填（UPDATE已有记录，补william/HKJC等）
    run([sys.executable, os.path.join(SCRIPT_DIR, "fetch_pinnacle_odds.py"),
         "--date", date],
        label="2/9 fetch_pinnacle_odds",
        required=False)

    # 3. 补算 lambda（对 jingcai 等无 lambda 的记录用赔率反推）
    run([sys.executable, os.path.join(SCRIPT_DIR, "calc_lambda.py"),
         "--db", db, "--date", date],
        label="3/9 calc_lambda",
        required=False)

    # 4. LGBM 融合概率
    run([sys.executable, os.path.join(SCRIPT_DIR, "update_db_fusion.py"),
         "--db", db],
        label="4/9 update_db_fusion",
        required=False)

    # 5. EV 重算
    run([sys.executable, os.path.join(SCRIPT_DIR, "value_bet.py"),
         "--all", "--db", db],
        label="5/9 value_bet (EV)",
        required=False)

    # 6. Kelly 重算
    run([sys.executable, os.path.join(SCRIPT_DIR, "update_db_kelly.py"),
         "--db", db],
        label="6/9 update_db_kelly",
        required=False)

    # 7. DB 去重
    run([sys.executable, os.path.join(SCRIPT_DIR, "align_and_merge.py"),
         "--cleanup-db", "--db", db],
        label="7/9 cleanup_db_duplicates",
        required=False)

    # 8. 对齐合并 → processed/
    run([sys.executable, os.path.join(SCRIPT_DIR, "align_and_merge.py"),
         "--all", "--db", db],
        label="8/9 align_and_merge",
        required=True)

    # 9. 推送 DB 到 Release
    if not args.skip_push:
        run([sys.executable, os.path.join(SCRIPT_DIR, "push_db.py"),
             "--db", db],
            label="9/9 push_db",
            required=False)
    else:
        print("⏭️ 跳过 push_db (--skip-push)")

    print(f"\n{'='*60}")
    print(f"🎉 pipeline 完成: {date}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
