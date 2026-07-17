#!/usr/bin/env python3
"""更新指定比赛的即时赔率"""
import json, sys, os, time
sys.path.insert(0, 'scripts')
from titan007_utils import get_odds_history

# 目标fid（明天07-18 12:00前未开场，从results.json获取）
target_fids = ['1358420','1362048','1362351','1362361','1362704','1362710',
               '1363900','1384119','1384122','1384123','1384124','1384126',
               '1405739','1405746','1417973','1417974','1441200','1444918']

# 它们在 matches_hkjc_20260717.json 里
fpath = 'data/matches_hkjc_20260717.json'
data = json.load(open(fpath, 'r', encoding='utf-8'))
matches = data['matches']
total = len(matches)

# 找到目标比赛
to_update = [m for m in matches if m['fid'] in target_fids]
print(f'文件共 {total} 场，需更新 {len(to_update)} 场')

pin_ok = 0
hk_ok = 0

for i, m in enumerate(to_update):
    sid = m['fid']  # 已经是字符串
    
    # 平博
    p = get_odds_history(sid, '432')
    if p and p.get('open') and p.get('latest'):
        o = p['open']; l = p['latest']
        m['odds_pinnacle_open_win'] = o['win']
        m['odds_pinnacle_open_draw'] = o['draw']
        m['odds_pinnacle_open_loss'] = o['loss']
        m['odds_pinnacle_win'] = l['win']
        m['odds_pinnacle_draw'] = l['draw']
        m['odds_pinnacle_loss'] = l['loss']
        pin_ok += 1
        print(f'  ✓ {m["home_team"]} vs {m["away_team"]}: 平博={l["win"]}/{l["draw"]}/{l["loss"]}')
    else:
        print(f'  ✗ {m["home_team"]} vs {m["away_team"]}: 平博无数据')
    time.sleep(1.2)
    
    # HKJC
    h = get_odds_history(sid, '177')
    if h and h.get('open') and h.get('latest'):
        o = h['open']; l = h['latest']
        m['odds_hkjc_open_win'] = o['win']
        m['odds_hkjc_open_draw'] = o['draw']
        m['odds_hkjc_open_loss'] = o['loss']
        m['odds_hkjc_win'] = l['win']
        m['odds_hkjc_draw'] = l['draw']
        m['odds_hkjc_loss'] = l['loss']
        hk_ok += 1
        print(f'      HKJC={l["win"]}/{l["draw"]}/{l["loss"]}')
    else:
        print(f'      HKJC无数据')
    time.sleep(1.2)

data['odds_updated_at'] = '2026-07-17T22:40+08:00'
json.dump(data, open(fpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'\n✅ 完成: 平博{pin_ok}/{len(to_update)}  HKJC{hk_ok}/{len(to_update)}')
