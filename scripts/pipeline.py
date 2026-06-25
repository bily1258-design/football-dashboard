#!/usr/bin/env python3
"""pipeline.py — 预测+赔率回填+融合+EV+Kelly+去重+对齐+复盘+构建+推送 全链路入口

步骤顺序硬编码，从根本上杜绝 yml 排列出错：
  1. predict_from_odds    — OM赔率 → 泊松预测 INSERT DB
  2. fetch_pinnacle_odds  — Pinnacle/HKJC/威廉初终盘 UPDATE DB
  3. calc_lambda          — 补算 lambda
  4. update_db_fusion     — LGBM融合概率填充
  5. value_bet --all      — EV 重算
  6. update_db_kelly      — Kelly 重算
  7. align_and_merge --cleanup-db  — 去重复
  8. align_and_merge --all         — 对齐合并 → processed/
  9. review               — 赛果回填 + 命中分析（填充 actual_outcome）
 10. recalibrate_db       — 联赛分层参数 + isotonic校准 + 信心分层
 11. merge_and_build --db — 构建 docs/ (results.json + index.html)
 12. git push docs/       — 推 docs/ 到仓库（触发 GA 部署）
 13. push_db              — DB 推 Release

用法：
  python scripts/pipeline.py --date 2026-06-18 --db data/football.db
  python scripts/pipeline.py --date 2026-06-18 --db data/football.db --skip-push
  python scripts/pipeline.py --date 2026-06-18 --db data/football.db --skip-review
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


def git_push_docs(date, repo_dir):
    """推 docs/ 到 GitHub，触发 GA 部署"""
    print(f"\n{'='*60}")
    print(f"▶ [12/13 git push docs/]")
    print(f"{'='*60}")

    token = os.environ.get('GITHUB_TOKEN', '')

    # git add docs/
    subprocess.run(['git', 'add', 'docs/'], cwd=repo_dir, capture_output=True)

    # 检查是否有变化
    r = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_dir, capture_output=True)
    if r.returncode == 0:
        print("⏭️ docs/ 无变化，跳过push")
        return 0

    # 有变化 → commit
    cr = subprocess.run(
        ['git', 'commit', '-m', f'docs: update {date}'],
        cwd=repo_dir, capture_output=True, text=True
    )
    if cr.returncode != 0:
        print(f"⚠️ git commit failed: {cr.stderr.strip()}")
        return cr.returncode

    # push — 优先用 token，否则走已有 credential
    if token:
        push_url = f'https://{token}@github.com/bily1258-design/football-dashboard.git'
        pr = subprocess.run(
            ['git', 'push', push_url, 'HEAD:main'],
            cwd=repo_dir, capture_output=True, text=True
        )
    else:
        pr = subprocess.run(['git', 'push'], cwd=repo_dir, capture_output=True, text=True)

    if pr.returncode != 0:
        print(f"❌ git push failed: {pr.stderr.strip()}")
        return pr.returncode

    print("✅ docs/ pushed to GitHub → GA will deploy")
    return 0


def main():
    parser = argparse.ArgumentParser(description="预测+赔率回填+融合+EV+Kelly+复盘+构建 全链路")
    parser.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--skip-push", action="store_true",
                        help="跳过 push_db（本地测试用）")
    parser.add_argument("--skip-predict", action="store_true",
                        help="跳过 predict（已有预测时）")
    parser.add_argument("--skip-review", action="store_true",
                        help="跳过 review（当日无赛果时）")
    args = parser.parse_args()

    date = args.date
    db = args.db

    # 1. 泊松预测
    if not args.skip_predict:
        run([sys.executable, os.path.join(SCRIPT_DIR, "predict_from_odds.py"),
             "--date", date, "--db", db],
            label="1/12 predict_from_odds",
            required=False)
    else:
        print("⏭️ 跳过 predict (--skip-predict)")

    # 2. Pinnacle/HKJC/威廉初终盘回填
    run([sys.executable, os.path.join(SCRIPT_DIR, "fetch_pinnacle_odds.py"),
         "--date", date],
        label="2/12 fetch_pinnacle_odds",
        required=False)

    # 3. 补算 lambda
    run([sys.executable, os.path.join(SCRIPT_DIR, "calc_lambda.py"),
         "--db", db, "--date", date],
        label="3/12 calc_lambda",
        required=False)

    # 4. LGBM 融合概率
    run([sys.executable, os.path.join(SCRIPT_DIR, "update_db_fusion.py"),
         "--db", db],
        label="4/12 update_db_fusion",
        required=False)

    # 5. EV 重算
    run([sys.executable, os.path.join(SCRIPT_DIR, "value_bet.py"),
         "--all", "--db", db],
        label="5/12 value_bet (EV)",
        required=False)

    # 6. Kelly 重算
    run([sys.executable, os.path.join(SCRIPT_DIR, "update_db_kelly.py"),
         "--db", db],
        label="6/12 update_db_kelly",
        required=False)

    # 7. DB 去重
    run([sys.executable, os.path.join(SCRIPT_DIR, "align_and_merge.py"),
         "--cleanup-db", "--db", db],
        label="7/12 cleanup_db_duplicates",
        required=False)

    # 8. 对齐合并 → processed/
    run([sys.executable, os.path.join(SCRIPT_DIR, "align_and_merge.py"),
         "--all", "--db", db],
        label="8/12 align_and_merge",
        required=True)

    # 9. 赛果回填 + 命中分析
    if not args.skip_review:
        run([sys.executable, os.path.join(SCRIPT_DIR, "review.py"),
             "--date", date, "--db", db],
            label="9/12 review (赛果回填)",
            required=False)
    else:
        print("⏭️ 跳过 review (--skip-review)")

    # 10. 重新校准 (联赛参数+isotonic+信心分层)
    run([sys.executable, os.path.join(SCRIPT_DIR, "recalibrate_db.py"),
         "--db", db],
        label="10/13 recalibrate_db (校准)",
        required=False)

    # 11. 构建 docs/
    run([sys.executable, os.path.join(SCRIPT_DIR, "merge_and_build.py"),
         "--db", db],
        label="11/13 merge_and_build (docs/)",
        required=False)

    # 11. git push docs/ → 触发 GA 部署
    repo_dir = os.path.dirname(SCRIPT_DIR)
    if not args.skip_push:
        git_push_docs(date, repo_dir)
    else:
        print("⏭️ 跳过 git push docs/ (--skip-push)")

    # 12. 推送 DB 到 Release
    if not args.skip_push:
        run([sys.executable, os.path.join(SCRIPT_DIR, "push_db.py"),
             "--db", db],
            label="13/13 push_db",
            required=False)
    else:
        print("⏭️ 跳过 push_db (--skip-push)")

    print(f"\n{'='*60}")
    print(f"🎉 pipeline 完成: {date}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
