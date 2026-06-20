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

import os, sys, subprocess, argparse, shutil, json
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(REPO_DIR, 'data', 'cache')
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
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=600)
    # 打印关键输出（亚盘/赔率等），让用户看到进度
    for line in result.stdout.split('\n'):
        if any(k in line for k in ['亚盘', 'AH', 'ah_', 'Pinnacle', 'HKJC', '百家', '更新', 'ERROR', 'WARN', 'INFO', 'matched', 'updated', '记录']):
            print(f"  {line.strip()}")
    if result.returncode == 0:
        print("OK Pinnacle/HKJC -> DB")
    else:
        print(f"WARN Pinnacle/HKJC fail: {result.stderr[:300]}")
        if result.stdout:
            print(f"  stdout(tail): {result.stdout[-300:]}")
    return result.returncode == 0

def step_update_ah(date_str: str, db_path: str = None):
    """Step 1.25: 从odds_api产出的AH文件读取百家平均亚盘，写入DB"""
    print("\n" + "=" * 50)
    print(f"STEP 1.25: 亚盘让球盘 -> DB - {date_str}")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))
    
    import sqlite3, json
    from datetime import datetime, timedelta
    
    ah_updated = 0
    prev_day = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    window_start = f"{prev_day} 12:00"
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    window_end = f"{next_day} 11:59"
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 确保ah字段存在
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT'),
                       ('ah_open_handicap', 'REAL'), ('ah_open_home_water', 'REAL'), ('ah_open_away_water', 'REAL'),
                       ('liji_handicap', 'REAL'), ('liji_home_water', 'REAL'), ('liji_away_water', 'REAL'),
                       ('liji_open_handicap', 'REAL'), ('liji_open_home_water', 'REAL'), ('liji_open_away_water', 'REAL'),
                       ('ms_handicap', 'REAL'), ('ms_home_water', 'REAL'), ('ms_away_water', 'REAL'),
                       ('ms_open_handicap', 'REAL'), ('ms_open_home_water', 'REAL'), ('ms_open_away_water', 'REAL'),
                       # 大小球字段
                       ('ou_over', 'REAL'), ('ou_line', 'REAL'), ('ou_under', 'REAL'),
                       ('ou_open_over', 'REAL'), ('ou_open_line', 'REAL'), ('ou_open_under', 'REAL'),
                       ('liji_ou_over', 'REAL'), ('liji_ou_line', 'REAL'), ('liji_ou_under', 'REAL'),
                       ('liji_ou_open_over', 'REAL'), ('liji_ou_open_line', 'REAL'), ('liji_ou_open_under', 'REAL'),
                       ('ms_ou_over', 'REAL'), ('ms_ou_line', 'REAL'), ('ms_ou_under', 'REAL'),
                       ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL')]:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass
    
    for date_tag in [date_str, prev_day]:
        ah_path = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet", f"ah_{date_tag.replace('-', '')}.json")
        if not os.path.exists(ah_path):
            continue
        try:
            with open(ah_path, 'r', encoding='utf-8') as f:
                ah_data = json.load(f)
            if not ah_data:
                continue
            print(f"  读取亚盘: {ah_path} ({len(ah_data)} 场)")
            
            cursor.execute("""
                SELECT id, home_team, away_team FROM poisson_predictions
                WHERE kickoff_time >= ? AND kickoff_time <= ?
            """, (window_start, window_end))
            db_records = cursor.fetchall()
            
            for key, ah in ah_data.items():
                ah_home = ah.get('home', '')
                ah_away = ah.get('away', '')
                ah_close = ah.get('close', {})
                if not ah_close or (ah_close.get('handicap', 0) == 0 and ah_close.get('home_w', 0) == 0):
                    continue
                
                # 逐条相似度匹配，避免精确队名匹配漏掉同名变体（如"沙特"vs"沙特阿拉伯"）
                matched_ids = []
                for rid, db_home, db_away in db_records:
                    h_match = max(
                        len(set(ah_home) & set(db_home)) / max(len(ah_home), len(db_home), 1),
                        len(set(ah_away) & set(db_away)) / max(len(ah_away), len(db_away), 1)
                    )
                    h_rev = max(
                        len(set(ah_home) & set(db_away)) / max(len(ah_home), len(db_away), 1),
                        len(set(ah_away) & set(db_home)) / max(len(ah_away), len(db_home), 1)
                    )
                    sim = max(h_match, h_rev)
                    if sim >= 0.4:
                        matched_ids.append(rid)
                
                if matched_ids:
                    # 逐条更新所有相似匹配的记录（按id，不依赖精确队名）
                    ah_val = ah_close.get('handicap', 0) or 0
                    hw_val = ah_close.get('home_w', 0) or 0
                    aw_val = ah_close.get('away_w', 0) or 0
                    src_val = ah.get('source', 'avg')

                    # 百家平均初盘
                    ah_open = ah.get('open', {})
                    ah_open_h = ah_open.get('handicap', 0) or 0
                    ah_open_hw = ah_open.get('home_w', 0) or 0
                    ah_open_aw = ah_open.get('away_w', 0) or 0

                    # 利记/明升数据
                    liji = ah.get('liji', {})
                    liji_close = liji.get('close', {})
                    liji_open = liji.get('open', {})
                    ms = ah.get('ms', {})
                    ms_close = ms.get('close', {})
                    ms_open = ms.get('open', {})

                    # 大小球数据
                    ou = ah.get('ou', {})
                    ou_close = ou.get('close', {})
                    ou_open = ou.get('open', {})
                    ou_liji = ah.get('ou_liji', {})
                    ou_liji_close = ou_liji.get('close', {})
                    ou_liji_open = ou_liji.get('open', {})
                    ou_ms = ah.get('ou_ms', {})
                    ou_ms_close = ou_ms.get('close', {})
                    ou_ms_open = ou_ms.get('open', {})

                    cnt = 0
                    for rid in matched_ids:
                        cursor.execute("""
                            UPDATE poisson_predictions SET
                                ah_handicap = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_handicap ELSE ? END,
                                ah_home_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_home_water ELSE ? END,
                                ah_away_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_away_water ELSE ? END,
                                ah_source = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_source ELSE ? END,
                                ah_open_handicap = CASE WHEN ah_open_handicap IS NOT NULL AND ah_open_handicap != 0 THEN ah_open_handicap ELSE ? END,
                                ah_open_home_water = CASE WHEN ah_open_handicap IS NOT NULL AND ah_open_handicap != 0 THEN ah_open_home_water ELSE ? END,
                                ah_open_away_water = CASE WHEN ah_open_handicap IS NOT NULL AND ah_open_handicap != 0 THEN ah_open_away_water ELSE ? END,
                                liji_handicap = CASE WHEN liji_handicap IS NOT NULL AND liji_handicap != 0 THEN liji_handicap ELSE ? END,
                                liji_home_water = CASE WHEN liji_handicap IS NOT NULL AND liji_handicap != 0 THEN liji_home_water ELSE ? END,
                                liji_away_water = CASE WHEN liji_handicap IS NOT NULL AND liji_handicap != 0 THEN liji_away_water ELSE ? END,
                                liji_open_handicap = CASE WHEN liji_open_handicap IS NOT NULL AND liji_open_handicap != 0 THEN liji_open_handicap ELSE ? END,
                                liji_open_home_water = CASE WHEN liji_open_handicap IS NOT NULL AND liji_open_handicap != 0 THEN liji_open_home_water ELSE ? END,
                                liji_open_away_water = CASE WHEN liji_open_handicap IS NOT NULL AND liji_open_handicap != 0 THEN liji_open_away_water ELSE ? END,
                                ms_handicap = CASE WHEN ms_handicap IS NOT NULL AND ms_handicap != 0 THEN ms_handicap ELSE ? END,
                                ms_home_water = CASE WHEN ms_handicap IS NOT NULL AND ms_handicap != 0 THEN ms_home_water ELSE ? END,
                                ms_away_water = CASE WHEN ms_handicap IS NOT NULL AND ms_handicap != 0 THEN ms_away_water ELSE ? END,
                                ms_open_handicap = CASE WHEN ms_open_handicap IS NOT NULL AND ms_open_handicap != 0 THEN ms_open_handicap ELSE ? END,
                                ms_open_home_water = CASE WHEN ms_open_handicap IS NOT NULL AND ms_open_handicap != 0 THEN ms_open_home_water ELSE ? END,
                                ms_open_away_water = CASE WHEN ms_open_handicap IS NOT NULL AND ms_open_handicap != 0 THEN ms_open_away_water ELSE ? END,
                                ou_over = ?, ou_line = ?, ou_under = ?,
                                ou_open_over = ?, ou_open_line = ?, ou_open_under = ?,
                                liji_ou_over = ?, liji_ou_line = ?, liji_ou_under = ?,
                                liji_ou_open_over = ?, liji_ou_open_line = ?, liji_ou_open_under = ?,
                                ms_ou_over = ?, ms_ou_line = ?, ms_ou_under = ?,
                                ms_ou_open_over = ?, ms_ou_open_line = ?, ms_ou_open_under = ?
                            WHERE id = ?
                        """, (ah_val, hw_val, aw_val, src_val,
                              ah_open_h, ah_open_hw, ah_open_aw,
                              liji_close.get('handicap', 0) or 0, liji_close.get('home_w', 0) or 0, liji_close.get('away_w', 0) or 0,
                              liji_open.get('handicap', 0) or 0, liji_open.get('home_w', 0) or 0, liji_open.get('away_w', 0) or 0,
                              ms_close.get('handicap', 0) or 0, ms_close.get('home_w', 0) or 0, ms_close.get('away_w', 0) or 0,
                              ms_open.get('handicap', 0) or 0, ms_open.get('home_w', 0) or 0, ms_open.get('away_w', 0) or 0,
                              ou_close.get('over', 0) or 0, ou_close.get('line', 0) or 0, ou_close.get('under', 0) or 0,
                              ou_open.get('over', 0) or 0, ou_open.get('line', 0) or 0, ou_open.get('under', 0) or 0,
                              ou_liji_close.get('over', 0) or 0, ou_liji_close.get('line', 0) or 0, ou_liji_close.get('under', 0) or 0,
                              ou_liji_open.get('over', 0) or 0, ou_liji_open.get('line', 0) or 0, ou_liji_open.get('under', 0) or 0,
                              ou_ms_close.get('over', 0) or 0, ou_ms_close.get('line', 0) or 0, ou_ms_close.get('under', 0) or 0,
                              ou_ms_open.get('over', 0) or 0, ou_ms_open.get('line', 0) or 0, ou_ms_open.get('under', 0) or 0,
                              rid))
                        cnt += cursor.rowcount
                    ah_updated += cnt
                    if cnt > 0:
                        print(f"  [AH] {ah_home} vs {ah_away} -> 盘口{ah_close['handicap']} 主水{ah_close.get('home_w',0):.2f} 客水{ah_close.get('away_w',0):.2f} ({cnt}条)")
        except Exception as e:
            print(f"  WARN 亚盘读取失败: {e}")
    
    conn.commit()
    conn.close()
    print(f"  OK 亚盘写入: {ah_updated} 条")
    return ah_updated

def step_update_db(db_path: str = None):
    """Step 1.3: 3 链条全量更新 (fusion -> EV -> kelly)

    顺序：fusion 必须在 EV 之前 (EV 依赖 fusion 概率)，
          kelly 在最后 (用 fusion 或 final + odds)
    """
    print("\n" + "=" * 50)
    print("STEP 1.3: DB 3链条全量更新 (fusion + EV + kelly)")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))

    scripts = [
        ('LGBM 融合概率', os.path.join(SCRIPT_DIR, 'update_db_fusion.py'),
         ['--db', db_path], 600),
        ('价值投注 EV (V3 概率优势法)', os.path.join(SCRIPT_DIR, 'value_bet.py'),
         ['--all', '--db', db_path], 600),
        ('Kelly 指数 (半凯利)', os.path.join(SCRIPT_DIR, 'update_db_kelly.py'),
         ['--db', db_path], 300),
    ]

    all_ok = True
    for label, script, args, timeout in scripts:
        print(f"\n[1.3.{scripts.index((label, script, args, timeout))+1}] {label}")
        cmd = [sys.executable, script] + args
        result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            # 输出关键统计行
            for line in result.stdout.split('\n'):
                if any(k in line for k in ['更新', '完成', 'updated', 'updated:', '条记录', '条 (错误', '总记录']):
                    print(' ', line.strip())
            print(f"  OK {label}")
        else:
            print(f"  WARN {label} failed (rc={result.returncode})")
            if result.stderr:
                print(f"  stderr: {result.stderr[:300]}")
            if result.stdout:
                print(f"  stdout (tail): {result.stdout[-300:]}")
            all_ok = False

    return all_ok


def step_predict(date_str: str, db_path: str = None):
    """Step 1.4: 从 OM 赔率生成泊松预测，INSERT 到 DB

    termux 抓 raw 后，把当日比赛写入 poisson_predictions 表，
    这样 align_and_merge 才能从 DB 读到新比赛，新比赛才会进看板
    """
    print("\n" + "=" * 50)
    print(f"STEP 1.4: 生成预测 (OM → DB) — {date_str}")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'predict_from_odds.py'),
           '--date', date_str, '--db', db_path]
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        # 输出脚本关键行
        for line in result.stdout.split('\n'):
            if any(k in line for k in ['OM matches', 'Predictions', '新增', '跳过']):
                print(' ', line)
        print("OK 预测已生成")
    else:
        print(f"WARN 预测生成失败: {result.stderr[:200]}")
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
    print("STEP 1.9: 按 conf=0.5 重算 final (recalc_final.py)")
    result = subprocess.run(
        ['python3', 'scripts/recalc_final.py'],
        cwd=REPO_DIR,
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print('⚠️ recalc_final 失败:', result.stderr)

    print(f"STEP 2: 生成日报 — {date_str}")
    print("=" * 50)

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'daily_report.py'), '--date', date_str]
    if incremental:
        cmd.append('--incremental')
    
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=600)
    if result.returncode == 0:
        print(f"✅ 日报生成完成")
    else:
        print(f"⚠️ 日报生成失败: {result.stderr[:200]}")
    return result.returncode == 0


def step_backfill_results(date_str: str, db_path: str = None):
    """Step: 从足彩网抓赛果回填DB（优先于500.com）"""
    print("\n" + "=" * 50)
    print(f"STEP: 赛果回填 — {date_str}")
    print("=" * 50)

    try:
        from fetch_zgzcw_results import fetch_results, PAGE_JZ, PAGE_BD, backfill_db as _backfill_zgzcw
        results = fetch_results(date_str, PAGE_JZ)
        bd_results = fetch_results(date_str, PAGE_BD)
        all_results = results + bd_results
        if all_results:
            count = _backfill_zgzcw(all_results, db_path)
            print(f"✅ 足彩网回填 {count} 条")
            return count
        else:
            print("  足彩网无赛果数据，尝试500.com...")
    except Exception as e:
        print(f"⚠️ 足彩网赛果抓取失败: {e}，尝试500.com...")

    # 兜底: 500.com缓存
    try:
        from fetch_500com_termux import fetch_results as _fetch_500com
        base = date_str.replace('-', '')
        next_base = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y%m%d')
        cache_files = [
            os.path.join(CACHE_DIR, f"500com_results_{base}.json"),
            os.path.join(CACHE_DIR, f"500com_results_{next_base}.json"),
        ]
        all_500 = []
        for cf in cache_files:
            if os.path.exists(cf):
                with open(cf, 'r', encoding='utf-8') as f:
                    all_500.extend(json.load(f).get('results', []))
        if all_500:
            from review import _backfill_results
            count = _backfill_results(all_500)
            print(f"✅ 500.com回填 {count} 条")
            return count
    except Exception as e:
        print(f"⚠️ 500.com兜底也失败: {e}")

    print("  无赛果可回填")
    return 0


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
        step_predict(date_str, db_path)          # 先INSERT预测记录
        step_fetch_pinnacle(date_str, db_path)   # 再UPDATE赔率
        step_update_ah(date_str, db_path)        # 百家平均亚盘写入DB
        step_update_db(db_path)
        step_backfill_results(date_str, db_path) # 赛果回填（足彩网优先）
        
        if args.with_report:
            step_prepare_odds(date_str)
            step_daily_report(date_str, args.incremental)
        
        if args.with_review:
            step_review(date_str)
        
        # 先上传DB到Release，再git push触发GA
        # 确保GA下载Release时拿到的是含AH数据的最新DB
        if not args.skip_db_push:
            step_push_db(db_path)
        
        step_push(date_str)
        
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
    step_predict(date_str, db_path)          # 先INSERT预测记录
    step_fetch_pinnacle(date_str, db_path)   # 再UPDATE赔率
    step_update_ah(date_str, db_path)        # 百家平均亚盘写入DB
    step_update_db(db_path)
    step_backfill_results(date_str, db_path) # 赛果回填（足彩网优先）
    
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
