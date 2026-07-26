#!/usr/bin/env python3
"""补抓亚盘盘口数据，适度并行，防限流"""

import json, time, sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from titan007_utils import fetch_asian_odds_batch

RESULTS = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data', 'results.json')
BATCH_SIZE = 30         # 每批30个fid（原5，HTTP延迟是瓶颈，并行才有效果）
MAX_WORKERS = 10        # 10线程并行
BATCH_DELAY = 0.05      # 每批间隔0.05s

def main():
    with open(RESULTS) as f:
        d = json.load(f)

    matches = d['matches']
    _cutoff_start = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')  # 昨天
    _cutoff_end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')   # 明天
    need = [(i, str(m.get('fid', ''))) for i, m in enumerate(matches)
            if m.get('fid') and m.get('fid') not in ('0', '') and m.get('ah_home') is None
            and (_cutoff_start <= (m.get('date', '')[:10]) <= _cutoff_end)]

    seen = {}
    for idx, fid in need:
        seen.setdefault(fid, []).append(idx)
    unique_fids = list(seen.keys())

    already = sum(1 for m in matches if m.get('ah_home') is not None)
    print(f"共 {len(matches)} 场比赛, 已有AH: {already}, 需补抓: {len(unique_fids)} 个唯一fid")

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

        processed = batch_start + len(batch)
        elapsed = time.time() - t0
        print(f"  [{processed}/{len(unique_fids)}] 成功={ok} 失败={fail}  耗时={elapsed:.0f}s")

        # 定期保存
        with open(RESULTS, 'w') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        time.sleep(BATCH_DELAY)

    elapsed = time.time() - t0
    print(f"\n完成! 成功: {ok}, 失败: {fail}, 总耗时: {elapsed:.0f}s")
    print(f"最终有AH盘口: {sum(1 for m in matches if m.get('ah_home') is not None)}/{len(matches)}")

if __name__ == '__main__':
    main()
