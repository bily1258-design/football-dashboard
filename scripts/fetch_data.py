#!/usr/bin/env python3
"""fetch_data.py — 主调度

Termux 模式：fetch raw → git push
完整模式：fetch raw → align → build

用法：
  python fetch_data.py --date 2026-05-30       # 全流程
  python fetch_data.py --fetch-only             # 只抓取（Termux用）
  python fetch_data.py --fetch-and-push         # 抓取+推送（Termux用）
  python fetch_data.py --build-only             # 只构建
"""

import os, sys, subprocess, argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)


def step_fetch(date_str: str):
    """Step 1: 抓取 raw 数据"""
    print("\n" + "=" * 50)
    print(f"STEP 1: 抓取 raw 数据 — {date_str}")
    print("=" * 50)

    from fetch_bsd import fetch_all as fetch_bsd
    from odds_api import fetch_all as fetch_om

    bsd = fetch_bsd(date_str)
    om = fetch_om(date_str)

    bsd_summary = bsd.get('summary', {})
    om_summary = om.get('summary', {})
    print(f"\n📊 BSD: 竞彩{bsd_summary.get('jingcai',0)} 完场{bsd_summary.get('wanchang',0)} 北单{bsd_summary.get('beidan',0)}")
    print(f"📊 OM:  平均{om_summary.get('avg_count',0)} Pinnacle{om_summary.get('pinnacle_count',0)} HKJC{om_summary.get('hkjc_count',0)}")
    return bsd, om


def step_push(date_str: str):
    """Step 1.5: git push raw 数据（Termux模式）"""
    print("\n" + "=" * 50)
    print("STEP 1.5: 推送 raw 数据到 GitHub")
    print("=" * 50)

    # git add + commit + push
    subprocess.run(['git', 'add', 'data/raw/'], cwd=REPO_DIR)
    result = subprocess.run(
        ['git', 'commit', '-m', f'raw data {date_str}'],
        cwd=REPO_DIR, capture_output=True, text=True
    )
    if 'nothing to commit' in result.stdout:
        print("  无新数据，跳过推送")
        return True
    push = subprocess.run(
        ['git', 'push', 'origin', 'main'],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=120
    )
    if push.returncode == 0:
        print("✅ raw 数据已推送 → GA 将自动触发构建")
        return True
    else:
        print(f"❌ 推送失败: {push.stderr[:200]}")
        return False


def step_align(date_str: str, db_path: str = None):
    """Step 2: 对齐合并 raw → processed"""
    print("\n" + "=" * 50)
    print(f"STEP 2: 对齐合并 — {date_str}")
    print("=" * 50)

    from align_and_merge import align_and_merge
    result = align_and_merge(date_str, db_path)
    return result


def step_build(db_path: str = None):
    """Step 3: 构建 results.json + index.html"""
    print("\n" + "=" * 50)
    print("STEP 3: 构建看板")
    print("=" * 50)

    from merge_and_build import load_from_processed, build_daily_stats, build_summary
    from merge_and_build import generate_results_json, generate_index_html
    from merge_and_build import load_from_db, DATA_DIR, DOCS_DIR

    by_date = load_from_processed()
    if not by_date and db_path:
        print(f'⚠️ processed为空，fallback读DB')
        by_date = load_from_db(db_path)
    if not by_date:
        print('[ERROR] 无数据，跳过构建')
        return False

    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)
    print(f'📊 {len(by_date)}天, {summary["total_matches"]}场已开奖')

    generate_results_json(by_date, daily_stats, summary, os.path.join(REPO_DIR, 'docs', 'data'))
    generate_index_html(by_date, daily_stats, summary, os.path.join(REPO_DIR, 'docs'))
    return True


def main():
    parser = argparse.ArgumentParser(description='主调度：fetch → align → build')
    parser.add_argument('--date', type=str, default=None, help='日期 YYYY-MM-DD')
    parser.add_argument('--yesterday', action='store_true', help='用昨天日期')
    parser.add_argument('--today', action='store_true', help='用今天日期')
    parser.add_argument('--fetch-only', action='store_true', help='只抓取')
    parser.add_argument('--fetch-and-push', action='store_true', help='抓取+推送（Termux模式）')
    parser.add_argument('--build-only', action='store_true', help='只构建')
    parser.add_argument('--db', type=str, default=None, help='数据库路径')
    args = parser.parse_args()

    # 确定日期
    if args.date:
        date_str = args.date
    elif args.today:
        date_str = datetime.now().strftime('%Y-%m-%d')
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    db_path = args.db or os.environ.get('FOOTBALL_DB_PATH',
        os.path.join(REPO_DIR, '..', 'data', 'shared_state', 'football.db'))

    print(f"🎯 目标日期: {date_str}")

    if not args.build_only:
        step_fetch(date_str)

    if args.fetch_and_push:
        step_push(date_str)

    if args.fetch_only or args.fetch_and_push:
        # Termux模式，到此结束
        if args.fetch_and_push:
            print("\n✅ Termux模式完成：raw数据已推送，GA将自动构建看板")
        else:
            print("\n✅ 数据已抓取到 data/raw/")
        return

    # 完整模式：align + build
    if not args.fetch_only:
        step_align(date_str, db_path)
        step_build(db_path)

    print("\n✅ 全流程完成")


if __name__ == '__main__':
    main()
