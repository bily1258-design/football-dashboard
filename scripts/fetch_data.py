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
    """Step 1: 抓取 raw 数据（赔率+亚盘，赛果回填已移至step_backfill_results走足彩网）"""
    print("\n" + "=" * 50)
    print(f"STEP 1: 抓取 raw 数据 — {date_str}")
    print("=" * 50)

    from odds_api import fetch_all as fetch_om

    om = fetch_om(date_str)

    om_summary = om.get('summary', {})
    print(f"📊 OM:  Pinnacle{om_summary.get('pinnacle_count',0)} HKJC{om_summary.get('hkjc_count',0)} 利记{om_summary.get('liji_count',0)}")
    return om

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
    """Step 1.25: 从oyzs产出的亚盘数据读取Pinnacle/利记/HKJC亚盘，写入DB"""
    print("\n" + "=" * 50)
    print(f"STEP 1.25: 亚盘让球盘 -> DB - {date_str}")
    print("=" * 50)
    if not db_path:
        db_path = os.environ.get('FOOTBALL_DB_PATH',
            os.path.join(REPO_DIR, 'data', 'football.db'))
    
    import sqlite3, json
    from datetime import datetime, timedelta

    def _ah_or_none(d, k):
        """亚盘 close/open dict 取值。
        - dict 为空 → None
        - dict 三个字段（盘口/主水/客水）全 0 → None（视作"没抓到"，COALESCE 保留 DB 旧值）
        - 否则返回 dict.get(k)（真平手 0.0 也能正常取到）
        """
        if not d:
            return None
        h = d.get('handicap', 0) or 0
        hw = d.get('home_w', 0) or 0
        aw = d.get('away_w', 0) or 0
        if h == 0 and hw == 0 and aw == 0:
            return None
        return d.get(k)

    def _ou_or_none(d, k):
        """OU 大小球 dict 取值。
        - dict 为空 → None
        - dict 三个字段（over/line/under）全 0 → None（视作"没抓到"，COALESCE 保留 DB 旧值）
        - 否则返回 dict.get(k)（line=0 不算"全 0"，只要 over/under 水位非 0 就放过）
        """
        if not d:
            return None
        o = d.get('over', 0) or 0
        l = d.get('line', 0) or 0
        u = d.get('under', 0) or 0
        if o == 0 and l == 0 and u == 0:
            return None
        return d.get(k)

    ah_updated = 0
    prev_day = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    window_start = f"{prev_day} 12:00"
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    window_end = f"{next_day} 12:06"  # 2026-06-20 改造：11:59 -> 12:06，覆盖12:00整点边界
    
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
                       ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL'),
                       # 威廉希尔字段
                       ('william_1x2_w', 'REAL'), ('william_1x2_d', 'REAL'), ('william_1x2_l', 'REAL'),
                       ('william_ah_handicap', 'REAL'), ('william_ah_home_water', 'REAL'), ('william_ah_away_water', 'REAL'),
                       ('william_ah_open_handicap', 'REAL'), ('william_ah_open_home_water', 'REAL'), ('william_ah_open_away_water', 'REAL'),
                       ('william_ou_over', 'REAL'), ('william_ou_line', 'REAL'), ('william_ou_under', 'REAL')]:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass
    
    for date_tag in [date_str, prev_day]:
        oyzs_path = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet", f"oyzs_{date_tag.replace('-', '')}.json")
        if not os.path.exists(oyzs_path):
            continue
        try:
            with open(oyzs_path, 'r', encoding='utf-8') as f:
                oyzs_data = json.load(f)
            if not oyzs_data:
                continue
            print(f"  读取亚盘: {oyzs_path} ({len(oyzs_data)} 场)")
            
            cursor.execute("""
                SELECT id, home_team, away_team FROM poisson_predictions
                WHERE kickoff_time >= ? AND kickoff_time <= ?
            """, (window_start, window_end))
            db_records = cursor.fetchall()
            
            for key, entry in oyzs_data.items():
                ah_home = entry.get('home', '')
                ah_away = entry.get('away', '')
                # 亚盘优先级: Pinnacle → 利记 → 威廉希尔 → HKJC
                pin_ah = entry.get('pin_ah', {})
                liji_ah = entry.get('liji_ah', {})
                william_ah = entry.get('william_ah', {})
                hkjc_ah = entry.get('hkjc_ah', {})
                
                ah_close = {}
                ah_source = ''
                ah_open_data = {}
                if pin_ah.get('close', {}).get('handicap') is not None and pin_ah['close'].get('handicap', 0) != 0:
                    ah_close = pin_ah.get('close', {})
                    ah_open_data = pin_ah.get('open', {})
                    ah_source = 'pinnacle'
                elif liji_ah.get('close', {}).get('handicap') is not None and liji_ah['close'].get('handicap', 0) != 0:
                    ah_close = liji_ah.get('close', {})
                    ah_open_data = liji_ah.get('open', {})
                    ah_source = 'liji'
                elif william_ah.get('close', {}).get('handicap') is not None and william_ah['close'].get('handicap', 0) != 0:
                    ah_close = william_ah.get('close', {})
                    ah_open_data = william_ah.get('open', {})
                    ah_source = 'william'
                elif hkjc_ah.get('close', {}).get('handicap') is not None and hkjc_ah['close'].get('handicap', 0) != 0:
                    ah_close = hkjc_ah.get('close', {})
                    ah_open_data = hkjc_ah.get('open', {})
                    ah_source = 'hkjc'
                
                if not ah_close:
                    continue
                if (ah_close.get('handicap', 0) == 0
                    and ah_close.get('home_w', 0) == 0
                    and ah_close.get('away_w', 0) == 0):
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
                    # 2026-06-21 改造：用 _ah_or_none helper 统一处理"没抓到 vs 真平手 vs 真数据"三种场景
                    # 返回 None → COALESCE 保留 DB 旧值；返回 0.0/非零 → 写入
                    ah_val = _ah_or_none(ah_close, 'handicap')
                    hw_val = _ah_or_none(ah_close, 'home_w')
                    aw_val = _ah_or_none(ah_close, 'away_w')
                    src_val = ah_source or 'pinnacle'

                    # Pinnacle/利记/HKJC初盘
                    ah_open = ah_open_data
                    ah_open_h = _ah_or_none(ah_open, 'handicap')
                    ah_open_hw = _ah_or_none(ah_open, 'home_w')
                    ah_open_aw = _ah_or_none(ah_open, 'away_w')

                    # 利记/明升数据
                    liji_data = entry.get('liji_ah', {})
                    liji_close = liji_data.get('close', {})
                    liji_open = liji_data.get('open', {})
                    liji_h = _ah_or_none(liji_close, 'handicap')
                    liji_hw = _ah_or_none(liji_close, 'home_w')
                    liji_aw = _ah_or_none(liji_close, 'away_w')
                    liji_oh = _ah_or_none(liji_open, 'handicap')
                    liji_ohw = _ah_or_none(liji_open, 'home_w')
                    liji_oaw = _ah_or_none(liji_open, 'away_w')

                    ms_data = entry.get('ms_ah', {})
                    ms_close = ms_data.get('close', {})
                    ms_open = ms_data.get('open', {})
                    ms_h = _ah_or_none(ms_close, 'handicap')
                    ms_hw = _ah_or_none(ms_close, 'home_w')
                    ms_aw = _ah_or_none(ms_close, 'away_w')
                    ms_oh = _ah_or_none(ms_open, 'handicap')
                    ms_ohw = _ah_or_none(ms_open, 'home_w')
                    ms_oaw = _ah_or_none(ms_open, 'away_w')

                    # 威廉希尔数据
                    william_data = entry.get('william_ah', {})
                    william_close = william_data.get('close', {})
                    william_open = william_data.get('open', {})
                    william_h = _ah_or_none(william_close, 'handicap')
                    william_hw = _ah_or_none(william_close, 'home_w')
                    william_aw = _ah_or_none(william_close, 'away_w')
                    william_oh = _ah_or_none(william_open, 'handicap')
                    william_ohw = _ah_or_none(william_open, 'home_w')
                    william_oaw = _ah_or_none(william_open, 'away_w')

                    # 大小球数据（OU 段 2026-06-21 改造：用 _ou_or_none helper 统一处理"没抓到 vs 真数据"，SQL 同步改 COALESCE 保留旧值）
                    # OU大小球优先级: Pinnacle → 利记
                    pin_ou = entry.get('pin_ou', {})
                    liji_ou_data = entry.get('liji_ou', {})
                    ou_close = pin_ou.get('close', {}) or liji_ou_data.get('close', {})
                    ou_open = pin_ou.get('open', {}) or liji_ou_data.get('open', {})
                    ou_liji_close = entry.get('liji_ou', {}).get('close', {})
                    ou_liji_open = entry.get('liji_ou', {}).get('open', {})
                    ou_ms_close = entry.get('ms_ou', {}).get('close', {})
                    ou_ms_open = entry.get('ms_ou', {}).get('open', {})

                    ou_o = _ou_or_none(ou_close, 'over')
                    ou_l = _ou_or_none(ou_close, 'line')
                    ou_u = _ou_or_none(ou_close, 'under')
                    ou_op_o = _ou_or_none(ou_open, 'over')
                    ou_op_l = _ou_or_none(ou_open, 'line')
                    ou_op_u = _ou_or_none(ou_open, 'under')
                    ou_liji_o = _ou_or_none(ou_liji_close, 'over')
                    ou_liji_l = _ou_or_none(ou_liji_close, 'line')
                    ou_liji_u = _ou_or_none(ou_liji_close, 'under')
                    ou_liji_op_o = _ou_or_none(ou_liji_open, 'over')
                    ou_liji_op_l = _ou_or_none(ou_liji_open, 'line')
                    ou_liji_op_u = _ou_or_none(ou_liji_open, 'under')
                    ou_ms_o = _ou_or_none(ou_ms_close, 'over')
                    ou_ms_l = _ou_or_none(ou_ms_close, 'line')
                    ou_ms_u = _ou_or_none(ou_ms_close, 'under')
                    ou_ms_op_o = _ou_or_none(ou_ms_open, 'over')
                    ou_ms_op_l = _ou_or_none(ou_ms_open, 'line')
                    ou_ms_op_u = _ou_or_none(ou_ms_open, 'under')

                    # 威廉希尔OU数据
                    ou_william_close = entry.get('william_ou', {}).get('close', {})
                    ou_william_open = entry.get('william_ou', {}).get('open', {})
                    ou_william_o = _ou_or_none(ou_william_close, 'over')
                    ou_william_l = _ou_or_none(ou_william_close, 'line')
                    ou_william_u = _ou_or_none(ou_william_close, 'under')

                    # 威廉希尔1X2欧赔数据
                    william_1x2 = entry.get('william_1x2', {})
                    william_1x2_close = william_1x2.get('close', {})
                    william_1x2_open = william_1x2.get('open', {})
                    w1x2_w = william_1x2_close.get('w') or None
                    w1x2_d = william_1x2_close.get('d') or None
                    w1x2_l = william_1x2_close.get('l') or None

                    cnt = 0
                    for rid in matched_ids:
                        cursor.execute("""
                            UPDATE poisson_predictions SET
                                ah_handicap = COALESCE(?, ah_handicap),
                                ah_home_water = COALESCE(?, ah_home_water),
                                ah_away_water = COALESCE(?, ah_away_water),
                                ah_source = COALESCE(?, ah_source),
                                ah_open_handicap = COALESCE(?, ah_open_handicap),
                                ah_open_home_water = COALESCE(?, ah_open_home_water),
                                ah_open_away_water = COALESCE(?, ah_open_away_water),
                                liji_handicap = COALESCE(?, liji_handicap),
                                liji_home_water = COALESCE(?, liji_home_water),
                                liji_away_water = COALESCE(?, liji_away_water),
                                liji_open_handicap = COALESCE(?, liji_open_handicap),
                                liji_open_home_water = COALESCE(?, liji_open_home_water),
                                liji_open_away_water = COALESCE(?, liji_open_away_water),
                                ms_handicap = COALESCE(?, ms_handicap),
                                ms_home_water = COALESCE(?, ms_home_water),
                                ms_away_water = COALESCE(?, ms_away_water),
                                ms_open_handicap = COALESCE(?, ms_open_handicap),
                                ms_open_home_water = COALESCE(?, ms_open_home_water),
                                ms_open_away_water = COALESCE(?, ms_open_away_water),
                                william_ah_handicap = COALESCE(?, william_ah_handicap),
                                william_ah_home_water = COALESCE(?, william_ah_home_water),
                                william_ah_away_water = COALESCE(?, william_ah_away_water),
                                william_ah_open_handicap = COALESCE(?, william_ah_open_handicap),
                                william_ah_open_home_water = COALESCE(?, william_ah_open_home_water),
                                william_ah_open_away_water = COALESCE(?, william_ah_open_away_water),
                                ou_over = COALESCE(?, ou_over), ou_line = COALESCE(?, ou_line), ou_under = COALESCE(?, ou_under),
                                ou_open_over = COALESCE(?, ou_open_over), ou_open_line = COALESCE(?, ou_open_line), ou_open_under = COALESCE(?, ou_open_under),
                                liji_ou_over = COALESCE(?, liji_ou_over), liji_ou_line = COALESCE(?, liji_ou_line), liji_ou_under = COALESCE(?, liji_ou_under),
                                liji_ou_open_over = COALESCE(?, liji_ou_open_over), liji_ou_open_line = COALESCE(?, liji_ou_open_line), liji_ou_open_under = COALESCE(?, liji_ou_open_under),
                                ms_ou_over = COALESCE(?, ms_ou_over), ms_ou_line = COALESCE(?, ms_ou_line), ms_ou_under = COALESCE(?, ms_ou_under),
                                ms_ou_open_over = COALESCE(?, ms_ou_open_over), ms_ou_open_line = COALESCE(?, ms_ou_open_line), ms_ou_open_under = COALESCE(?, ms_ou_open_under),
                                william_ou_over = COALESCE(?, william_ou_over), william_ou_line = COALESCE(?, william_ou_line), william_ou_under = COALESCE(?, william_ou_under),
                                william_1x2_w = COALESCE(?, william_1x2_w), william_1x2_d = COALESCE(?, william_1x2_d), william_1x2_l = COALESCE(?, william_1x2_l)
                            WHERE id = ?
                        """, (ah_val, hw_val, aw_val, src_val,
                              ah_open_h, ah_open_hw, ah_open_aw,
                              liji_h, liji_hw, liji_aw,
                              liji_oh, liji_ohw, liji_oaw,
                              ms_h, ms_hw, ms_aw,
                              ms_oh, ms_ohw, ms_oaw,
                              william_h, william_hw, william_aw,
                              william_oh, william_ohw, william_oaw,
                              ou_o, ou_l, ou_u,
                              ou_op_o, ou_op_l, ou_op_u,
                              ou_liji_o, ou_liji_l, ou_liji_u,
                              ou_liji_op_o, ou_liji_op_l, ou_liji_op_u,
                              ou_ms_o, ou_ms_l, ou_ms_u,
                              ou_ms_op_o, ou_ms_op_l, ou_ms_op_u,
                              ou_william_o, ou_william_l, ou_william_u,
                              w1x2_w, w1x2_d, w1x2_l,
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
    
    # 使用 HTTPS+PAT 方式推送（SSH 在 Termux 不稳定）
    pat = os.environ.get('GITHUB_PAT', '')
    https_url = f'https://{pat}@github.com/bily1258-design/football-dashboard.git' if pat else ''
    
    # 先pull rebase再push
    if https_url:
        pull = subprocess.run(
            ['git', 'pull', '--rebase', https_url, 'main'],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=180
        )
    else:
        pull = subprocess.run(
            ['git', 'pull', '--rebase', 'origin', 'main'],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=180
        )
    if pull.returncode != 0:
        print(f"⚠️ pull失败: {pull.stderr[:200]}")
    
    if https_url:
        push = subprocess.run(
            ['git', 'push', https_url, 'main'],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=180
        )
    else:
        push = subprocess.run(
            ['git', 'push', 'origin', 'main'],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=180
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
    
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=1200)
    if result.returncode == 0:
        print(f"✅ 日报生成完成")
    else:
        print(f"⚠️ 日报生成失败: {result.stderr[:200]}")
    return result.returncode == 0


def step_backfill_results(date_str: str, db_path: str = None):
    """Step: 从足彩网抓赛果回填DB（优先于500.com）
    
    回填当天+前一天：当天可能有凌晨完场的，前一天是主要完场日
    """
    print("\n" + "=" * 50)
    print(f"STEP: 赛果回填 — {date_str} + 前一天")
    print("=" * 50)

    total = 0
    # 回填当天和前一天
    dates = [date_str]
    try:
        prev = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        dates.append(prev)
    except:
        pass

    try:
        from fetch_zgzcw_results import fetch_results, PAGE_JZ, PAGE_BD, backfill_db as _backfill_zgzcw
        for d in dates:
            results = fetch_results(d, PAGE_JZ)
            bd_results = fetch_results(d, PAGE_BD)
            all_results = results + bd_results
            if all_results:
                count = _backfill_zgzcw(all_results, db_path)
                total += count
        if total > 0:
            print(f"✅ 足彩网回填共 {total} 条")
            return total
        else:
            print("  足彩网无赛果数据，尝试500.com...")
    except Exception as e:
        print(f"⚠️ 足彩网赛果抓取失败: {e}，尝试500.com...")

    # 兜底: 500.com缓存
    try:
        all_500 = []
        for d in dates:
            base = d.replace('-', '')
            next_base = (datetime.strptime(d, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y%m%d')
            for suffix in [base, next_base]:
                cf = os.path.join(CACHE_DIR, f"500com_results_{suffix}.json")
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
    return total


def step_review(date_str: str):
    """Step 3: 运行复盘"""
    print("\n" + "=" * 50)
    print(f"STEP 3: 执行复盘 — {date_str}")
    print("=" * 50)

    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'review.py'), '--date', date_str]
    result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=300)
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

    # Termux 模式：只抓raw + push（其余步骤由GA完成）
    if args.fetch_and_push:
        step_fetch(date_str)
        step_backfill_results(date_str, db_path)  # 赛果回填（Termux有chromium可抓zgzcw）
        step_push_db(db_path)  # 推送回填后的DB到Release
        step_push(date_str)
        
        print("\n✅ Termux模式完成：raw数据+赛果回填已推送，GA将执行后续全流程")
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
    step_update_ah(date_str, db_path)        # 亚盘数据写入DB
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

