#!/usr/bin/env python3
"""用新sid重新抓取赔率 - 慢速版"""
import json, sys, os, time
sys.path.insert(0, 'scripts')
from titan007_utils import get_odds_history

# 旧fid -> 新sid 映射
sid_map = {
    '1358420': '2908636',  # 纳什维尔 vs 亚特兰大联
    '1362048': '2910657',  # 巴伊亚 vs 沙佩科
    '1362351': '2910812',  # 弗鲁米嫩塞 vs 巴甘蒂诺
    '1362361': '2910814',  # 米拉索尔 vs 格雷米奥
    '1362704': '2912205',  # 哥德堡 vs 布鲁马波卡纳
    '1362710': '2912209',  # 米亚尔比 vs 瓦斯特拉斯
    '1363900': '2912834',  # 博德闪耀 vs 腓特烈斯塔
    '1384122': '2940079',  # 福塔雷萨 vs 诺瓦里桑蒂诺
    '1405746': '2976487',  # 河床 vs 阿尔多西维
    '1417974': '2997342',  # 莱昂 vs 阿特拉斯
}

fpath = 'data/matches_hkjc_20260717.json'
data = json.load(open(fpath, 'r', encoding='utf-8'))

pin_ok = 0
hk_ok = 0

for m in data['matches']:
    old_fid = m['fid']
    if old_fid not in sid_map:
        continue
    new_sid = sid_map[old_fid]
    
    # 先休息3秒
    time.sleep(3)
    p = get_odds_history(new_sid, '432')
    if p and p.get('open') and p.get('latest'):
        o = p['open']; l = p['latest']
        m['odds_pinnacle_open_win'] = o['win']
        m['odds_pinnacle_open_draw'] = o['draw']
        m['odds_pinnacle_open_loss'] = o['loss']
        m['odds_pinnacle_win'] = l['win']
        m['odds_pinnacle_draw'] = l['draw']
        m['odds_pinnacle_loss'] = l['loss']
        pin_ok += 1
        print(f'  ✓ {m["home_team"]}: 平博={l["win"]}/{l["draw"]}/{l["loss"]}')
    else:
        print(f'  ✗ {m["home_team"]}: 平博无数据 (cid=432)')
    
    time.sleep(3)
    h = get_odds_history(new_sid, '177')
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
        print(f'      HKJC无数据 (cid=177)')

data['odds_updated_at'] = '2026-07-17T23:10+08:00'
json.dump(data, open(fpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'\n✅ 完成: 平博{pin_ok}/{len(sid_map)}  HKJC{hk_ok}/{len(sid_map)}')
