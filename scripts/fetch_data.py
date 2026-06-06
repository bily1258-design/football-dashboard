#!/usr/bin/env python3
"""fetch_data.py — 主调度

Termux 模式：fetch raw → git push
完整模式：fetch raw → align → build

用法：
  python fetch_data.py --date 2026-05-30       # 全流程
  python fetch_data.py --fetch-only             # 只抓取（Termux用）
  python fetch_data.py --fetch-and-push         # 抓取+推送（Termux用）
  python fetch_data.py --build-only             # 只构建
  python fetch_data.py --with-report            # 抓取后跑日报
  python fetch_data.py --with-review            # 抓取后跑复盘
  python fetch_data.py --incremental            # 日报增量模式
"""

import os, sys, subprocess, argparse, shutil
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

def step_fetch_pinnacle(date_str: str, db_path: str = None):
    print("\n" + "=" * 50)
    print(f"STEP 1.2: Pinnacle/HKJC -> DB - {date_str}")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'fetch_pinnacle_odds.py'), '--date', date_str]
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        print("OK Pinnacle/HKJC -> DB")
    else:
        print(f"WARN Pinnacle/HKJC fail: {result.stderr[:200]}")
    return result.returncode == 0

def step_recalc_ev(db_path: str = None):
    print("\n" + "=" * 50)
    print("STEP 1.3: EV recalc")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'value_bet.py'), '--db', db_path]
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        print("OK EV recalc")
    else:
        print(f"WARN EV fail: {result.stderr[:200]}")
    return result.returncode == 0


def step_push(date_str: str):
    """Step 1.5: git push raw 数据（Termux模式）"""
    print("\n" + "=" * 50)
    print("STEP 1.5: 推送 raw 数据到 GitHub")
    print("=" * 50)

    subprocess.run(['git', 'add', '-A'], cwd=REPO_DIR)
    result = subprocess.run(
        ['git', 'commit', '-m', f'raw data {date_str}'],
        cwd=REPO_DIR, capture_output=True, text=True
    )
    if 'nothing to commit' in result.stdout:
        print("  无新数据，跳过推送")
        return True
    # 清理 rebase 残留（防中途崩了留尾巴）
    repo_git = os.path.join(REPO_DIR, '.git')
    if os.path.isdir(os.path.join(repo_git, 'rebase-merge')) or os.path.isdir(os.path.join(repo_git, 'rebase-apply')):
        print("⚠️ 检测到 rebase 残留，自动清理")
        subprocess.run(['git', 'rebase', '--abort'], cwd=REPO_DIR, capture_output=True)
        for stale in ('rebase-merge', 'rebase-apply'):
            stale_path = os.path.join(repo_git, stale)
            if os.path.isdir(stale_path):
                shutil.rmtree(stale_path, ignore_errors=True)
        rebase_head = os.path.join(repo_git, 'REBASE_HEAD')
        if os.path.isfile(rebase_head):
            try:
                os.remove(rebase_head)
            except OSError:
                pass
    # 先pull rebase再push
    pull = subprocess.run(
        ['git', 'pull', '--rebase', 'origin', 'main'],
        cwd=REPO_DIR, capture_output=True, text=True, timeout=120
    )
    if pull.returncode != 0:
        print(f"⚠️ pull失败: {pull.stderr[:200]}")
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


def step_prepare_odds(date_str: str):
    """Step 1.8: 将oddsmagnet赔率转为日报可用的real_odds.json"""
    print("\n" + "=" * 50)
    print("STEP 1.8: 转换赔率数据供日报使用")
    print("=" * 50)
    
    from oddsmagnet_to_realodds import convert
    result = convert(date_str)
    return result

def step_daily_report(date_str: str, incremental: bool = False):
    """Step 2: 运行日报生成"""
    print("\n" + "=" * 50)
    print(f"STEP 2: 生成日报 — {date_str}")
    print("=" * 50)

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'daily_report.py'), '--date', date_str]
    if incremental:
        cmd.append('--incremental')
    
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        print(f"✅ 日报生成完成")
    else:
        print(f"⚠️ 日报生成失败: {result.stderr[:200]}")
    return result.returncode == 0


def step_review(date_str: str):
    """Step 3: 运行复盘"""
    print("\n" + "=" * 50)
    print(f"STEP 3: 执行复盘 — {date_str}")
    print("=" * 50)

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'review.py'), '--date', date_str]
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        print(f"✅ 复盘完成")
    else:
        print(f"⚠️ 复盘失败: {result.stderr[:200]}")
    return result.returncode == 0


def step_align(date_str: str, db_path: str = None):
    """Step 4: 对齐合并 raw → processed"""
    print("\n" + "=" * 50)
    print(f"STEP 4: 对齐合并 — {date_str}")
    print("=" * 50)

    from align_and_merge import align_and_merge
    result = align_and_merge(date_str, db_path)
    return result


def step_build(db_path: str = None):
    """Step 5: 构建 results.json + index.html"""
    print("\n" + "=" * 50)
    print("STEP 5: 构建看板")
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


def step_push_db(db_path: str = None):
    """Step 6: 推送 DB 到 Release"""
    print("\n" + "=" * 50)
    print("STEP 6: 推送 DB 到 Release")
    print("=" * 50)

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'push_db.py')]
    if db_path:
        cmd.extend(['--db', db_path])
    
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=180)
    if result.returncode == 0:
        print(f"✅ DB推送完成")
        return True
    else:
        print(f"⚠️ DB推送失败: {result.stderr[:200]}")
        return False


def main():
    parser = argparse.ArgumentParser(description='主调度：fetch → report → review → align → build → push')
    parser.add_argument('--date', type=str, default=None, help='日期 YYYY-MM-DD')
    parser.add_argument('--yesterday', action='store_true', help='用昨天日期')
    parser.add_argument('--today', action='store_true', help='用今天日期')
    parser.add_argument('--fetch-only', action='store_true', help='只抓取')
    parser.add_argument('--fetch-and-push', action='store_true', help='抓取+推送（Termux模式）')
    parser.add_argument('--build-only', action='store_true', help='只构建')
    parser.add_argument('--db', type=str, default=None, help='数据库路径')
    parser.add_argument('--with-report', action='store_true', help='抓取后生成日报')
    parser.add_argument('--with-review', action='store_true', help='抓取后执行复盘')
    parser.add_argument('--incremental', action='store_true', help='日报增量模式')
    parser.add_argument('--skip-db-push', action='store_true', help='跳过DB推送')
    args = parser.parse_args()

    # 确定日期
    if args.date:
        date_str = args.date
    elif args.today:
        date_str = datetime.now().strftime('%Y-%m-%d')
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    db_path = args.db or os.environ.get('FOOTBALL_DB_PATH',
        os.path.join(REPO_DIR, 'data', 'football.db'))

    print(f"🎯 目标日期: {date_str}")

    # Termux 模式：fetch → report → review → push
    if args.fetch_and_push:
        step_fetch(date_str)
        step_fetch_pinnacle(date_str, db_path)
        step_recalc_ev(db_path)
        
        if args.with_report:
            step_prepare_odds(date_str)
            step_daily_report(date_str, args.incremental)
        
        if args.with_review:
            step_review(date_str)
        
        step_push(date_str)
        
        if not args.skip_db_push:
            step_push_db(db_path)
        
        print("\n✅ Termux模式完成：数据已推送，GA将自动构建看板")
        return

    # 只抓取模式
    if args.fetch_only:
        step_fetch(date_str)
        print("\n✅ 数据已抓取到 data/raw/")
        return

    # 只构建模式
    if args.build_only:
        step_align(date_str, db_path)
        step_build(db_path)
        print("\n✅ 构建完成")
        return

    # 完整模式：fetch → report → review → align → build
    step_fetch(date_str)
    step_fetch_pinnacle(date_str, db_path)
    step_recalc_ev(db_path)
    
    if args.with_report:
        step_prepare_odds(date_str)
        step_daily_report(date_str, args.incremental)
    
    if args.with_review:
        step_review(date_str)
    
    step_align(date_str, db_path)
    step_build(db_path)
    
    if not args.skip_db_push:
        step_push_db(db_path)

    print("\n✅ 全流程完成")


if __name__ == '__main__':
    main()
