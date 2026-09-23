#!/usr/bin/env python3
"""补抓亚盘盘口数据，适度并行，防限流

速度优化: BATCH_SIZE=30 每批30个fid并发, MAX_WORKERS=10线程
避免重复请求: 失败fid缓存到独立文件, 每7天自动重试一次

2026-09-24 补强(应对 ~58% 失败率):
  1) 同轮内二次重试: 首批失败的 fid 不再立刻写进失败缓存(会卡 RETRY_DAYS 天),
     而是收进重试队列, 降并发(2 线程, 每批 25, 间隔 0.3s)再跑一轮, 仍失败才落缓存。
  2) --days N: 默认只抓 [昨天, 明天](与 cron 一致), 传 N 可回补近 N 天的历史场次。
"""

import json, time, sys, os, argparse
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from titan007_utils import fetch_asian_odds_batch

SCRIPT_DIR = os.path.dirname(__file__)
RESULTS = os.path.join(SCRIPT_DIR, '..', 'docs', 'data', 'results.json')
FAILED_CACHE = os.path.join(SCRIPT_DIR, '..', '.ah_failed_cache.json')

BATCH_SIZE = 50         # 每批50个fid并发（原30）
MAX_WORKERS = 10        # 10线程并行（原5）
BATCH_DELAY = 0.02      # 每批间隔（原0.05）
RETRY_DAYS = 1          # 失败fid超过N天未重试时再试一次
# 批次级网络降级保护: 整批零成功通常是本机网络抽风(HTTP=000/ConnectError),
# 此时不该把 fid 写进失败缓存(否则正常场次被误判"无AH"卡住 RETRY_DAYS 天)。
OUTAGE_MIN_BATCH = 5    # 批次>=N 且 成功率=0 时, 本批失败不写缓存
# 同轮二次重试(降并发, 缓解限流/瞬断)
RETRY_WORKERS = 2
RETRY_BATCH = 25
RETRY_DELAY = 0.3
APPLY_KEYS = [
    ('ah_home', 'home_odds'), ('ah_away', 'away_odds'),
    ('ah_handicap', 'handicap'), ('ah_handicap_text', 'handicap_text'),
    ('ah_open_home', 'open_home_odds'), ('ah_open_away', 'open_away_odds'),
    ('ah_open_handicap', 'open_handicap'), ('ah_open_handicap_text', 'open_handicap_text'),
    ('ah_company_id', 'company_id'),
]


def apply_ah(matches, seen, fid, r):
    """把抓到的盘口写回该 fid 的所有行"""
    for match_idx in seen[fid]:
        m = matches[match_idx]
        for dst, src in APPLY_KEYS:
            m[dst] = r.get(src)

def load_failed_cache():
    """加载失败fid缓存 {fid: last_attempt_epoch}"""
    if os.path.exists(FAILED_CACHE):
        try:
            with open(FAILED_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_failed_cache(cache):
    with open(FAILED_CACHE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False)

def main():
    ap = argparse.ArgumentParser(description='补抓亚盘盘口')
    ap.add_argument('--days', type=int, default=1,
                    help='回补窗口: 近 N 天(+明天); 默认 1 = 昨天的场次(与 cron 一致)')
    args = ap.parse_args()

    with open(RESULTS) as f:
        d = json.load(f)

    matches = d['matches']
    failed_cache = load_failed_cache()
    now = datetime.now()
    now_ts = time.time()
    retry_threshold = now_ts - RETRY_DAYS * 86400

    _cutoff_start = (now - timedelta(days=max(1, args.days))).strftime('%Y-%m-%d')
    _cutoff_end = (now + timedelta(days=1)).strftime('%Y-%m-%d')

    need = []
    for i, m in enumerate(matches):
        fid = str(m.get('fid', ''))
        if not fid or fid == '0':
            continue
        if m.get('ah_home') is not None:
            continue
        date_str = m.get('date', '')[:10]
        if not (_cutoff_start <= date_str <= _cutoff_end):
            continue
        # 跳过已失败过的fid（超过RETRY_DAYS的再试一次）
        if fid in failed_cache:
            last_attempt = failed_cache[fid]
            if last_attempt > retry_threshold:
                continue
        need.append((i, fid))

    seen = {}
    for idx, fid in need:
        seen.setdefault(fid, []).append(idx)
    unique_fids = list(seen.keys())

    already = sum(1 for m in matches if m.get('ah_home') is not None)
    print(f"共 {len(matches)} 场比赛, 已有AH: {already}, "
          f"已确认无AH: {len(failed_cache)}, 本次需抓: {len(unique_fids)} 个唯一fid")

    if not unique_fids:
        print("无需补抓")
        return

    ok = fail = 0
    retry_queue = []
    t0 = time.time()

    for batch_start in range(0, len(unique_fids), BATCH_SIZE):
        batch = unique_fids[batch_start:batch_start + BATCH_SIZE]
        results = fetch_asian_odds_batch(batch, max_workers=MAX_WORKERS)

        batch_ok = sum(1 for fid in batch
                       if (results.get(fid) or {}).get('handicap') is not None)
        outage = len(batch) >= OUTAGE_MIN_BATCH and batch_ok == 0
        if outage:
            print(f"  ! 本批 {len(batch)} 个 fid 全部失败, 判定为网络降级, 不写失败缓存")

        for fid in batch:
            r = results.get(fid)
            if r and r.get('handicap') is not None:
                ok += 1
                apply_ah(matches, seen, fid, r)
            else:
                if outage:
                    continue        # 网络降级: 不重试也不落缓存, 留给下一轮
                retry_queue.append(fid)   # 先不落缓存, 轮末降并发重试一次

        processed = batch_start + len(batch)
        elapsed = time.time() - t0
        print(f"  [{processed}/{len(unique_fids)}] 成功={ok} 待重试={len(retry_queue)}  耗时={elapsed:.0f}s")

        # 定期保存结果+缓存
        save_failed_cache(failed_cache)
        with open(RESULTS, 'w') as f:
            json.dump(d, f, ensure_ascii=False, separators=(',', ':'))  # 紧凑输出, 防 results.json 回涨到 62MB
        time.sleep(BATCH_DELAY)

    # ── 同轮二次重试: 降并发 + 小间隔, 主要针对限流/瞬断, 仍失败才落失败缓存 ──
    if retry_queue:
        print(f"\n二次重试 {len(retry_queue)} 个 fid (并发 {RETRY_WORKERS}, 每批 {RETRY_BATCH})...")
        r_ok = 0
        for i in range(0, len(retry_queue), RETRY_BATCH):
            sub = retry_queue[i:i + RETRY_BATCH]
            rs = fetch_asian_odds_batch(sub, max_workers=RETRY_WORKERS)
            for fid in sub:
                r = rs.get(fid)
                if r and r.get('handicap') is not None:
                    ok += 1
                    r_ok += 1
                    apply_ah(matches, seen, fid, r)
                else:
                    fail += 1
                    failed_cache[fid] = now_ts
            print(f"  [{min(i+RETRY_BATCH, len(retry_queue))}/{len(retry_queue)}] 本轮救回 {r_ok} 个")
            time.sleep(RETRY_DELAY)
            save_failed_cache(failed_cache)
            with open(RESULTS, 'w') as f:
                json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
        print(f"二次重试救回 {r_ok}/{len(retry_queue)} 个")

    # 最终保存
    save_failed_cache(failed_cache)
    with open(RESULTS, 'w') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))  # 紧凑输出, 防 results.json 回涨到 62MB

    elapsed = time.time() - t0
    print(f"\n完成! 成功: {ok}, 失败: {fail}, 总耗时: {elapsed:.0f}s")
    print(f"最终有AH盘口: {sum(1 for m in matches if m.get('ah_home') is not None)}/{len(matches)}")
    print(f"累计确认无AH的fid: {len(failed_cache)}")

if __name__ == '__main__':
    main()
