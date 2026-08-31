#!/usr/bin/env python3
"""补抓亚盘盘口数据，适度并行，防限流

速度优化: BATCH_SIZE=30 每批30个fid并发, MAX_WORKERS=10线程
避免重复请求: 失败fid缓存到独立文件, 每7天自动重试一次
"""

import json, time, sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from titan007_utils import fetch_asian_odds_batch

SCRIPT_DIR = os.path.dirname(__file__)
RESULTS = os.path.join(SCRIPT_DIR, '..', 'docs', 'data', 'results.json')
FAILED_CACHE = os.path.join(SCRIPT_DIR, '..', '.ah_failed_cache.json')

BATCH_SIZE = 50         # 每批50个fid并发（原30）
MAX_WORKERS = 10        # 10线程并行（原5）
BATCH_DELAY = 0.02      # 每批间隔（原0.05）
RETRY_DAYS = 7          # 失败fid超过N天未重试时再试一次

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
    with open(RESULTS) as f:
        d = json.load(f)

    matches = d['matches']
    failed_cache = load_failed_cache()
    now = datetime.now()
    now_ts = time.time()
    retry_threshold = now_ts - RETRY_DAYS * 86400

    _cutoff_start = (now - timedelta(days=1)).strftime('%Y-%m-%d')
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
    t0 = time.time()

    for batch_start in range(0, len(unique_fids), BATCH_SIZE):
        batch = unique_fids[batch_start:batch_start + BATCH_SIZE]
        results = fetch_asian_odds_batch(batch, max_workers=MAX_WORKERS)

        for fid in batch:
            r = results.get(fid)
            if r and r.get('handicap') is not None:
                ok += 1
                for match_idx in seen[fid]:
                    m = matches[match_idx]
                    m['ah_home'] = r.get('home_odds')
                    m['ah_away'] = r.get('away_odds')
                    m['ah_handicap'] = r.get('handicap')
                    m['ah_handicap_text'] = r.get('handicap_text')
                    m['ah_open_home'] = r.get('open_home_odds')
                    m['ah_open_away'] = r.get('open_away_odds')
                    m['ah_open_handicap'] = r.get('open_handicap')
                    m['ah_open_handicap_text'] = r.get('open_handicap_text')
                    m['ah_company_id'] = r.get('company_id')
            else:
                fail += 1
                failed_cache[fid] = now_ts

        processed = batch_start + len(batch)
        elapsed = time.time() - t0
        print(f"  [{processed}/{len(unique_fids)}] 成功={ok} 失败={fail}  耗时={elapsed:.0f}s")

        # 定期保存结果+缓存
        save_failed_cache(failed_cache)
        with open(RESULTS, 'w') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        time.sleep(BATCH_DELAY)

    # 最终保存
    save_failed_cache(failed_cache)
    with open(RESULTS, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    print(f"\n完成! 成功: {ok}, 失败: {fail}, 总耗时: {elapsed:.0f}s")
    print(f"最终有AH盘口: {sum(1 for m in matches if m.get('ah_home') is not None)}/{len(matches)}")
    print(f"累计确认无AH的fid: {len(failed_cache)}")

if __name__ == '__main__':
    main()
