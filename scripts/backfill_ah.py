#!/usr/bin/env python3
"""补抓亚盘盘口数据，带限速，合并到 results.json"""

import json, time, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from titan007_utils import fetch_asian_odds

RESULTS = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data', 'results.json')

def main():
    with open(RESULTS) as f:
        d = json.load(f)

    matches = d['matches']
    need = [(i, str(m.get('fid', ''))) for i, m in enumerate(matches)
            if m.get('fid') and m.get('fid') not in ('0', '') and m.get('ah_home') is None]

    # Deduplicate by fid (keep first index)
    seen = {}
    for idx, fid in need:
        if fid not in seen:
            seen[fid] = []
        seen[fid].append(idx)
    unique_fids = list(seen.keys())

    print(f"共 {len(matches)} 场比赛")
    print(f"已有AH数据: {sum(1 for m in matches if m.get('ah_home') is not None)}")
    print(f"需补抓: {len(unique_fids)} 个唯一fid")

    if not unique_fids:
        print("无需补抓")
        return

    ok = 0
    fail = 0
    for idx, fid in enumerate(unique_fids):
        if idx > 0 and idx % 30 == 0:
            print(f"  [{idx}/{len(unique_fids)}] 已完成 {ok} 场...")
        r = fetch_asian_odds(fid)
        time.sleep(1.5)  # 限速，避免被封

        if r and r.get('handicap') is not None:
            ok += 1
            # 更新所有使用该fid的比赛
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

    print(f"\n完成! 成功: {ok}, 失败: {fail}")
    print(f"最终有AH盘口: {sum(1 for m in matches if m.get('ah_home') is not None)}/{len(matches)}")

    # 写入结果
    with open(RESULTS, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"已写入 {RESULTS}")

if __name__ == '__main__':
    main()
