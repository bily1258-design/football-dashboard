#!/usr/bin/env python3
"""pipeline.py — 预测+赔率回填+融合+EV+Kelly+去重+对齐+复盘+构建+推送 全链路入口

步骤顺序硬编码，从根本上杜绝 yml 排列出错：
  0. extract_fids        — 从500.com补fid_500
  1. predict_from_odds   — OM赔率 → 泊松预测 INSERT DB
  2. fetch_500com_odds   — Bet365/Pinnacle/利记等赔率 UPDATE DB
  3. calc_lambda         — 补算 lambda
  4. update_db_fusion    — LGBM融合概率填充
  5. value_bet --all     — EV 重算
  6. update_db_kelly     — Kelly 重算
  7. align_and_merge --cleanup-db  — 去重复
  8. align_and_merge --all         — 对齐合并 → processed/
  9. review              — 赛果回填 + 命中分析（填充 actual_outcome）
 10. recalibrate_db      — 联赛分层参数 + isotonic校准 + 信心分层
 11. merge_and_build --db — 构建 docs/ (results.json + index.html)
 12. git push docs/      — 推 docs/ 到仓库（触发 GA 部署）
 13. push_db             — DB 推 Release

用法：
  python scripts/pipeline.py --date 2026-07-02 --db data/football.db
  python scripts/pipeline.py --date 2026-07-02 --db data/football.db --skip-push
  python scripts/pipeline.py --date 2026-07-02 --db data/football.db --skip-review
  python scripts/pipeline.py --date 2026-07-02 --db data/football.db --verbose
"""

import subprocess
import sys
import os
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 步骤定义: (label, script, extra_args, required, skip_key)
STEPS = [
    ("0  补fid",         "extract_fids_from_live.py", ["--db", "{db}", "--date", "{date}", "--save-future", "-v"], False, "skip_fid"),
    ("1  500.com赔率",   "fetch_500com_odds.py",   ["--db", "{db}", "--company", "all", "--limit", "80"], False, "skip_odds"),
    ("2  泊松预测",      "predict_from_odds.py",   ["--date", "{date}", "--db", "{db}"], False, "skip_predict"),
    ("3  λ补算",         "calc_lambda.py",          ["--db", "{db}", "--date", "{date}"],   False, None),
    ("4  LGBM融合",      "update_db_fusion.py",    ["--db", "{db}"],                       False, None),
    ("5  EV重算",        "value_bet.py",            ["--all", "--db", "{db}"],              False, None),
    ("6  Kelly重算",     "update_db_kelly.py",     ["--db", "{db}"],                       False, None),
    ("7  DB去重",        "align_and_merge.py",      ["--cleanup-db", "--db", "{db}"],       False, None),
    ("8  对齐合并",      "align_and_merge.py",      ["--all", "--db", "{db}"],              True,  None),
    ("9  复盘",          "review.py",               ["--date", "{date}", "--db", "{db}"],   False, "skip_review"),
    ("10 校准",          "recalibrate_db.py",       ["--db", "{db}"],                       False, None),
    ("11 构建",          "merge_and_build.py",      ["--db", "{db}"],                       False, None),
    ("12 推送docs",      None,                       None,                                  False, "skip_push"),
    ("13 推送DB",        "push_db.py",              ["--db", "{db}"],                       False, "skip_push"),
]


def run(cmd, label, required=True, verbose=False):
    """运行子脚本，默认静默，失败时显示尾部输出"""
    repo_dir = os.path.dirname(SCRIPT_DIR)
    if verbose:
        result = subprocess.run(cmd, cwd=repo_dir)
    else:
        result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)

    if result.returncode != 0:
        if not verbose and result.stderr:
            tail = result.stderr.strip().split('\n')
            for line in tail[-10:]:
                print(f"    {line}")
        msg = f"⚠️ {label} failed (exit={result.returncode})"
        if required:
            print(f"❌ {msg} — 终止流程")
            sys.exit(result.returncode)
        else:
            print(f"{msg} — 继续下一步")
        return result.returncode
    return 0


def git_push_docs(date, repo_dir, verbose=False):
    """推 docs/ 到 GitHub，触发 GA 部署"""
    token = os.environ.get('GITHUB_TOKEN', '')

    subprocess.run(['git', 'add', 'docs/'], cwd=repo_dir, capture_output=True)
    r = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_dir, capture_output=True)
    if r.returncode == 0:
        return "⏭️"

    cr = subprocess.run(
        ['git', 'commit', '-m', f'docs: update {date}'],
        cwd=repo_dir, capture_output=True, text=True
    )
    if cr.returncode != 0:
        return f"❌ commit failed: {cr.stderr.strip()[:80]}"

    if token:
        push_url = f'https://{token}@github.com/bily1258-design/football-dashboard.git'
        pr = subprocess.run(['git', 'push', push_url, 'HEAD:main'],
                            cwd=repo_dir, capture_output=True, text=True)
    else:
        pr = subprocess.run(['git', 'push'], cwd=repo_dir, capture_output=True, text=True)

    if pr.returncode != 0:
        return f"❌ push failed: {pr.stderr.strip()[:80]}"

    return "✅"


def main():
    parser = argparse.ArgumentParser(description="预测+赔率回填+融合+EV+Kelly+复盘+构建 全链路")
    parser.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--skip-push", action="store_true", help="跳过推送（本地测试用 / GA用）")
    parser.add_argument("--skip-predict", action="store_true", help="跳过 predict（已有预测时）")
    parser.add_argument("--skip-review", action="store_true", help="跳过 review（当日无赛果时）")
    parser.add_argument("--skip-fid", action="store_true", help="跳过补fid（fid已有时）")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="跳过赛果抓取（DB已有赛果时用）")
    parser.add_argument("--skip-odds", action="store_true",
                        help="跳过500.com赔率抓取（DB已由本地补全时用，GA应带此参数）")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示子脚本完整输出")
    args = parser.parse_args()

    date = args.date
    db = args.db
    verbose = args.verbose

    skip_flags = {
        "skip_predict": args.skip_predict,
        "skip_review": args.skip_review,
        "skip_push": args.skip_push,
        "skip_fetch": args.skip_fetch,
        "skip_fid": args.skip_fid,
        "skip_odds": args.skip_odds,
    }

    print(f"🚀 pipeline {date} {'(verbose)' if verbose else ''}")
    print(f"─" * 40)

    failed = []
    for label, script, extra_args, required, skip_key in STEPS:
        if skip_key and skip_flags.get(skip_key):
            print(f"  ⏭️  {label}")
            continue

        # 特殊处理: git push docs
        if script is None and label == "12 推送docs":
            repo_dir = os.path.dirname(SCRIPT_DIR)
            result = git_push_docs(date, repo_dir, verbose)
            print(f"  {result}  {label}")
            continue

        # 构建命令
        fmt_args = [a.format(date=date, db=db) for a in extra_args]
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, script)] + fmt_args

        # review步骤：--skip-fetch时跳过网络抓取
        if label.startswith("9") and args.skip_fetch:
            cmd.append("--skip-fetch")

        print(f"  ⏳  {label}...", end="", flush=True)
        rc = run(cmd, label, required=required, verbose=verbose)
        if rc == 0:
            print(f"\r  ✅  {label}")
        else:
            print(f"\r  ❌  {label}")
            if required:
                break
            failed.append(label)

    print(f"─" * 40)
    if failed:
        print(f"🎉 pipeline 完成: {date} (失败: {', '.join(failed)})")
    else:
        print(f"🎉 pipeline 完成: {date}")


if __name__ == "__main__":
    main()
