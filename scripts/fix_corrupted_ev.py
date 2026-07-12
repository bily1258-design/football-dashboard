#!/usr/bin/env python3
"""修复22场重叠比赛（同时存在于管线和北单）的错误EV/泊松值"""
import json
import copy

with open('docs/data/results.json') as f:
    d = json.load(f)

fixed_count = 0
corrupted_fids = {2225, 2226, 2227, 2228, 2229, 2230, 2231, 2232, 2233, 2234,
                  2235, 2236, 2237, 2238, 2239, 2240, 3057, 3058, 3059, 3060, 3061, 3062}

for date, records in d['matches'].items():
    for i, r in enumerate(records):
        if r['id'] not in corrupted_fids:
            continue
        
        # 这些比赛有fusion_prob（来自管线），但没有correct EV
        odds = r.get('odds')
        fp = r.get('fusion_prob')
        if not odds or not fp:
            continue
        
        w_odds, d_odds, l_odds = odds['w'], odds['d'], odds['l']
        fp_w, fp_d, fp_l = fp['w'], fp['d'], fp['l']
        
        # 正确的 EV = fusion_prob × odds - 1
        ev_w = round(fp_w * w_odds - 1, 4)
        ev_d = round(fp_d * d_odds - 1, 4)
        ev_l = round(fp_l * l_odds - 1, 4)
        
        # 用fusion_prob作为poisson和final_prob
        poisson = {'w': round(fp_w, 4), 'd': round(fp_d, 4), 'l': round(fp_l, 4)}
        final_prob = {'w': round(fp_w, 4), 'd': round(fp_d, 4), 'l': round(fp_l, 4)}
        
        # Kelly
        kelly = {}
        for k, ov in [('w', w_odds), ('d', d_odds), ('l', l_odds)]:
            ev_k = {'w': ev_w, 'd': ev_d, 'l': ev_l}[k]
            kelly[k] = round(ev_k / (ov - 1), 4) if ov > 1 else 0.0
        
        # 最高EV方向
        max_ev_dir = max(['w', 'd', 'l'], key=lambda k: {'w': ev_w, 'd': ev_d, 'l': ev_l}[k])
        ev_direction_cn = {'w': '主胜', 'd': '平局', 'l': '客胜'}[max_ev_dir]
        
        # 更新记录
        records[i]['ev'] = {'w': ev_w, 'd': ev_d, 'l': ev_l}
        records[i]['poisson'] = poisson
        records[i]['final_prob'] = final_prob
        records[i]['kelly'] = kelly
        records[i]['ev_direction'] = ev_direction_cn
        records[i]['source'] = 'beidan'
        
        old_ev = None
        print(f"  fid={r['id']} {r['home']:<16} vs {r['away']:<16}  "
              f"EV: {ev_w*100:.1f}%/{ev_d*100:.1f}%/{ev_l*100:.1f}%")
        fixed_count += 1

# 保存
with open('docs/data/results.json', 'w') as f:
    json.dump(d, f, ensure_ascii=False)

print(f"\n修复了 {fixed_count} 场比赛")

# 验证
print("\n=== 验证 ===")
with open('docs/data/results.json') as f:
    d2 = json.load(f)

total = sum(len(v) for v in d2['matches'].values())
bd = sum(1 for v in d2['matches'].values() for r in v if r.get('source') == 'beidan')
bd_ne = sum(1 for v in d2['matches'].values() for r in v if r.get('source') == 'beidan' 
            and not (r.get('ev') and r.get('poisson') and r.get('kelly') and r.get('final_prob')))
print(f"总共 {total} 场, 北单 {bd} 场, 缺EV/泊松 {bd_ne} 场")
