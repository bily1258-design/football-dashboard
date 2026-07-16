"""补充测试：组合策略 + 精选参数精细扫描"""
import json

D = json.load(open('docs/data/results.json'))
matches = [m for m in D['matches'] if m.get('hit','') and m['raw_model_win']>0]
total = len(matches)
print(f'有结果场次: {total}')

def parse_result(score):
    parts = score.split('-')
    sh, sa = int(parts[0]), int(parts[1])
    return 'home' if sh > sa else ('draw' if sh == sa else 'away')

data = []
for m in matches:
    result = parse_result(m['score'])
    open_odds = [m['open_win_pin'], m['open_draw_pin'], m['open_loss_pin']]
    cur_odds = [m['odds_win'], m['odds_draw'], m['odds_loss']]
    lgbm_probs = [m['lgbm_win'], m['lgbm_draw'], m['lgbm_loss']]
    raw_probs = [m['raw_model_win'], m['raw_model_draw'], m['raw_model_loss']]
    if all(o>1 for o in open_odds):
        divs = [(c - o)/o for c, o in zip(cur_odds, open_odds)]
    else:
        divs = None
    data.append({'result':result,'lgbm_probs':lgbm_probs,'raw_probs':raw_probs,
                 'open_odds':open_odds,'cur_odds':cur_odds,'divs':divs})

def max_direction(w,d,l):
    vals=[w,d,l]; names=['home','draw','away']; return names[vals.index(max(vals))]

def middle_direction(w,d,l):
    vals=[w,d,l]; names=['home','draw','away']
    return names[sorted(range(3),key=lambda i:vals[i])[1]]

def check_hit(result, lgbm_probs, model_probs):
    return result == max_direction(*lgbm_probs) or result == middle_direction(*model_probs)

def adjust(params):
    upturn_lev = params['ul']; upturn_pen = params['up']
    drought_lev = params.get('dl',[0.10]); drought_pen = params.get('dp',[0.40])
    drought_max = params.get('dm',0.50)
    extra = params.get('extra',0)
    hits = 0
    for d in data:
        if d['divs'] is None: continue
        probs = list(d['raw_probs'])
        lgbm_max_en = max_direction(*d['lgbm_probs'])
        model_max_en = max_direction(*probs)
        disagree = (lgbm_max_en != model_max_en)
        for i in range(3):
            if d['cur_odds'][i] >= 7.0:
                distrust = 0.85
            else:
                dd = d['divs'][i]
                if dd > 0:
                    distrust = 0
                    for lvl, pen in sorted(zip(upturn_lev, upturn_pen), key=lambda x: -x[0]):
                        if dd > lvl: distrust = pen; break
                    if disagree and extra > 0:
                        distrust = min(0.95, distrust + extra)
                elif dd < 0:
                    ad = abs(dd)
                    distrust = 0
                    for lvl, pen in sorted(zip(drought_lev, drought_pen), key=lambda x: -x[0]):
                        if ad > lvl: distrust = pen; break
                    if distrust == 0 and ad <= 0.10:
                        distrust = ad / 0.10 * drought_max
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        adj = [p/t for p in probs]
        if check_hit(d['result'], d['lgbm_probs'], adj):
            hits += 1
    return hits

results = []
base_hits = sum(1 for d in data if check_hit(d['result'], d['lgbm_probs'], d['raw_probs']))

# 当前逻辑
cur_hits = sum(1 for m in matches if check_hit(parse_result(m['score']),
    [m['lgbm_win'],m['lgbm_draw'],m['lgbm_loss']],
    [m['model_win'],m['model_draw'],m['model_loss']]))

print(f'基准(无调整): {base_hits}/{total}')
print(f'当前逻辑:      {cur_hits}/{total}')

# ─── 组合精细扫描 ──────────────────────────────────
# 回升分级: 3档(高/中/低)
upturn_sets = [
    ([0.15, 0.08, 0.04], [0.60, 0.30, 0.10]),   # B-激进3
    ([0.12, 0.06, 0.03], [0.50, 0.25, 0.08]),
]
for ul, up in upturn_sets:
    for extra in [0, 0.15, 0.20]:
        hits = adjust({'ul':ul,'up':up,'extra':extra})
        label = f"分级{'/'.join(f'{l*100:.0f}%' for l in ul)}→{'/'.join(f'{p*100:.0f}%' for p in up)}" + (f"+分歧{extra*100:.0f}%" if extra else "")
        results.append((hits, label))

# 简单阈值+分歧
for thresh in [0.06, 0.07, 0.08]:
    for pen in [0.25, 0.30, 0.35]:
        for extra in [0, 0.15, 0.20]:
            hits = adjust({'ul':[thresh],'up':[pen],'extra':extra})
            label = f"回升>{thresh*100:.0f}%→{pen*100:.0f}%" + (f"+分歧{extra*100:.0f}%" if extra else "")
            results.append((hits, label))

# 降水调整组合
for dm in [0.30, 0.40, 0.50]:
    hits = adjust({'ul':[0.08],'up':[0.30],'dm':dm,'extra':0.15})
    results.append((hits, f"回升>8%→30%+分歧15%+降水max{dm*100:.0f}%"))

for dm in [0.30, 0.40, 0.50]:
    hits = adjust({'ul':[0.06],'up':[0.25],'dm':dm,'extra':0.15})
    results.append((hits, f"回升>6%→25%+分歧15%+降水max{dm*100:.0f}%"))

# 排序
results.sort(key=lambda x: -x[0])
print(f'\n{"策略":<55} {"命中":<6} {"%"}\n{"-"*65}')
best = results[0][0]
for h, label in results:
    marker = ' ★★★' if h == best else (' ★★' if h >= cur_hits else '')
    print(f'{label:<55} {h}/{total:<4} {h/total*100:.1f}%{marker}')
print(f'\n当前逻辑: {cur_hits}/{total} = {cur_hits/total*100:.1f}%')
print(f'基准(无调整): {base_hits}/{total} = {base_hits/total*100:.1f}%')
