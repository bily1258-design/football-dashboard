#!/usr/bin/env python3
"""补抓亚盘盘口数据，并行3个线程，0.3s间隔，每批保存"""

import json, time, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from titan007_utils import fetch_asian_odds_batch

RESULTS = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data', 'results.json')
SAVE_INTERVAL = 24  # 每抓完24个fid保存一次

def main():
    with open(RESULTS) as f:
        d = json.load(f)

    matches = d['matches']
    need = [(i, str(m.get('fid', ''))) for i, m in enumerate(matches)
            if m.get('fid') and m.get('fid') not in ('0', '') and m.get('ah_home') is None]

    seen = {}
    for idx, fid in need:
        seen.setdefault(fid, []).append(idx)
    unique_fids = list(seen.keys())

    print(f"共 {len(matches)} 场比赛")
    already = sum(1 for m in matches if m.get('ah_home') is not None)
    print(f"已有AH数据: {already}")
    print(f"需补抓: {len(unique_fids)} 个唯一fid")

    if not unique_fids:
        print("无需补抓")
        return

    ok = 0
    fail = 0
    last_save = 0

    for batch_start in range(0, len(unique_fids), 3):
        batch = unique_fids[batch_start:batch_start + 3]
        results = fetch_asian_odds_batch(batch, max_workers=3)
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
        if processed - last_save >= SAVE_INTERVAL:
            with open(RESULTS, 'w') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            last_save = processed

        print(f"  [{processed}/{len(unique_fids)}] 成功={ok} 失败={fail}")
        time.sleep(0.3)  # 限速

    # 最终保存
    with open(RESULTS, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print(f"\n完成! 成功: {ok}, 失败: {fail}")
    print(f"最终有AH盘口: {sum(1 for m in matches if m.get('ah_home') is not None)}/{len(matches)}")
    print(f"已写入 {RESULTS}")

if __name__ == '__main__':
    main()
