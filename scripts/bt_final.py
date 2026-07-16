"""双推荐命中率回测：LGBM主推(max) + 模型中值备选(median)
测试不同赔率分歧惩罚策略对双推荐命中率的影响"""
import json
import itertools
import sys

D = json.load(open('docs/data/results.json'))
matches = [m for m in D['matches'] if m.get('hit','') and m['raw_model_win']>0]
print(f'有结果场次: {len(matches)}')

def parse_result(score):
    parts = score.split('-')
    sh, sa = int(parts[0]), int(parts[1])
    return 'home' if sh > sa else ('draw' if sh == sa else 'away')

# 每个match的基础数据
data = []
for m in matches:
    result = parse_result(m['score'])
    rate = m.get('odds_source','pinnacle')
    
    # 开盘赔率
    open_odds = [m['open_win_pin'], m['open_draw_pin'], m['open_loss_pin']]
    # 当前赔率
    cur_odds = [m['odds_win'], m['odds_draw'], m['odds_loss']]
    # LGBM概率
    lgbm_probs = [m['lgbm_win'], m['lgbm_draw'], m['lgbm_loss']]
    # 原始模型概率(调整前)
    raw_probs = [m['raw_model_win'], m['raw_model_draw'], m['raw_model_loss']]
    # 分歧值
    if all(o>1 for o in open_odds):
        divs = [(c - o)/o for c, o in zip(cur_odds, open_odds)]
    else:
        divs = None
    
    data.append({
        'result': result,
        'lgbm_probs': lgbm_probs,
        'raw_probs': raw_probs,
        'open_odds': open_odds,
        'cur_odds': cur_odds,
        'divs': divs,
        'rate': rate,
    })

# ─── 工具函数 ──────────────────────────────────────
def max_direction(w, d, l):
    """返回方向名(en), 概率"""
    vals = [w, d, l]
    names = ['home', 'draw', 'away']
    idx = vals.index(max(vals))
    return names[idx], vals[idx]

def middle_direction(w, d, l):
    """返回中值方向(en), 概率"""
    vals = [w, d, l]
    names = ['home', 'draw', 'away']
    sorted_idx = sorted(range(3), key=lambda i: vals[i])
    idx = sorted_idx[1]  # 中间值
    return names[idx], vals[idx]

def check_double_hit(result, lgbm_probs, model_probs):
    """双推荐是否命中：LGBM主推 OR 模型中值备选"""
    lgbm_dir, _ = max_direction(*lgbm_probs)
    model_dir, _ = middle_direction(*model_probs)
    return result == lgbm_dir or result == model_dir

# ─── 1. 基准：完全不调整 ────────────────────────────────
base_hits = 0
for d in data:
    if check_double_hit(d['result'], d['lgbm_probs'], d['raw_probs']):
        base_hits += 1
total = len(data)
print(f'\n{"="*60}')
print(f'基准(不调整模型概率): {base_hits}/{total} = {base_hits/total*100:.1f}%')

# ─── 2. 当前逻辑(±10%单阈值) ──────────────────────────
print(f'\n{"="*60}')
print(f'当前逻辑(±10% 回升降水规则):')
current_hits = 0
# 使用当前 model_win/draw/loss 中的值(这些已经是调整后的)
for m in matches:
    model_probs = [m['model_win'], m['model_draw'], m['model_loss']]
    lgbm_probs = [m['lgbm_win'], m['lgbm_draw'], m['lgbm_loss']]
    if check_double_hit(parse_result(m['score']), lgbm_probs, model_probs):
        current_hits += 1
print(f'  {current_hits}/{total} = {current_hits/total*100:.1f}%')

# ─── 3. 参数扫描 ────────────────────────────────────
def distrust_adjust(raw_probs, divs, cur_odds, params):
    """应用任意的赔付分歧调整策略"""
    upturn_levels = params.get('upturn_levels', [0.10, 0.05, 0.03])
    upturn_penalties = params.get('upturn_penalties', [0.20, 0.10, 0.05])
    drought_penalty_max = params.get('drought_penalty_max', 0.50)
    drought_levels = params.get('drought_levels', [0.10, 0.05])
    drought_penalties = params.get('drought_penalties', [0.40, 0.25])
    lgbm_disagree_extra = params.get('lgbm_disagree_extra', 0)
    seven_up_penalty = params.get('seven_up_penalty', 0.85)
    # 分歧检测用LGBM方向 vs 模型最大值方向
    disagree_fn = params.get('disagree_fn', None)
    
    open_w, open_d, open_l = params.get('open_odds', [0,0,0])
    lgbm_probs = params.get('lgbm_probs', [0,0,0])
    
    if divs is None:
        return raw_probs
    
    probs = list(raw_probs)
    
    # 分歧检测
    lgbm_dir_en, _ = max_direction(*lgbm_probs)
    model_max_en, _ = max_direction(*probs)
    lgbm_disagree = (lgbm_dir_en != model_max_en)
    
    for i in range(3):
        if cur_odds[i] >= 7.0:
            distrust = seven_up_penalty
        else:
            d = divs[i]
            if d > 0:
                distrust = 0
                for level, pen in sorted(zip(upturn_levels, upturn_penalties), key=lambda x: -x[0]):
                    if d > level:
                        distrust = pen
                        break
                # 分歧额外惩罚
                if lgbm_disagree and lgbm_disagree_extra > 0:
                    distrust = min(0.95, distrust + lgbm_disagree_extra)
            elif d < 0:
                ad = abs(d)
                distrust = 0
                # 降水惩罚（分级）
                for level, pen in sorted(zip(drought_levels, drought_penalties), key=lambda x: -x[0]):
                    if ad > level:
                        distrust = pen
                        break
                # 如果没命中分级，用旧逻辑的小幅降水连续惩罚
                if distrust == 0 and ad <= 0.10:
                    distrust = ad / 0.10 * drought_penalty_max
            else:
                distrust = 0
        probs[i] *= max(0.05, 1 - distrust)
    
    t = sum(probs)
    return [p/t for p in probs]

# ─── 策略搜索 ──────────────────────────────────────
print(f'\n{"="*60}')
print(f'策略搜索（双推荐命中率）:')
print(f'{"="*60}')

strategies = []

# 策略A: 调整回升阈值 + 保持原降水规则
for upturn_thresh in [0.10, 0.08, 0.07, 0.05, 0.04]:
    hits = 0
    for d in data:
        if d['divs'] is None:
            continue
        params = {
            'upturn_levels': [upturn_thresh],
            'upturn_penalties': [0.30],
            'drought_levels': [0.10],
            'drought_penalties': [0.40],
            'drought_penalty_max': 0.50,
            'lgbm_disagree_extra': 0,
            'lgbm_probs': d['lgbm_probs'],
        }
        adj = distrust_adjust(d['raw_probs'], d['divs'], d['cur_odds'], params)
        if check_double_hit(d['result'], d['lgbm_probs'], adj):
            hits += 1
    strategies.append({
        'label': f'A-回升>{upturn_thresh*100:.0f}%→0.30',
        'hits': hits,
        'pct': hits/total*100,
    })

# 策略B: 分级回升(多档) + 原降水
for levels, penalties in [
    ([0.20, 0.10], [0.65, 0.30]),
    ([0.15, 0.08, 0.04], [0.60, 0.30, 0.10]),
    ([0.20, 0.10, 0.05], [0.60, 0.35, 0.15]),
    ([0.15, 0.07, 0.03], [0.55, 0.30, 0.10]),
]:
    hits = 0
    for d in data:
        if d['divs'] is None:
            continue
        params = {
            'upturn_levels': levels,
            'upturn_penalties': penalties,
            'drought_levels': [0.10],
            'drought_penalties': [0.40],
            'drought_penalty_max': 0.50,
            'lgbm_disagree_extra': 0,
            'lgbm_probs': d['lgbm_probs'],
        }
        adj = distrust_adjust(d['raw_probs'], d['divs'], d['cur_odds'], params)
        if check_double_hit(d['result'], d['lgbm_probs'], adj):
            hits += 1
    label_str = '+'.join([f'>{l*100:.0f}%→{p*100:.0f}%' for l,p in zip(levels, penalties)])
    strategies.append({'label': f'B-分级{label_str}', 'hits': hits, 'pct': hits/total*100})

# 策略C: 分歧额外惩罚
strategy_C_params = [
    (0.10, 0.25),
    (0.08, 0.25),
    (0.07, 0.20),
    (0.05, 0.20),
    (0.08, 0.15),
    (0.05, 0.15),
]
for upturn_thresh, extra in strategy_C_params:
    hits = 0
    for d in data:
        if d['divs'] is None:
            continue
        params = {
            'upturn_levels': [upturn_thresh],
            'upturn_penalties': [0.30],
            'drought_levels': [0.10],
            'drought_penalties': [0.40],
            'drought_penalty_max': 0.50,
            'lgbm_disagree_extra': extra,
            'lgbm_probs': d['lgbm_probs'],
        }
        adj = distrust_adjust(d['raw_probs'], d['divs'], d['cur_odds'], params)
        if check_double_hit(d['result'], d['lgbm_probs'], adj):
            hits += 1
    strategies.append({
        'label': f'C-回升>{upturn_thresh*100:.0f}%+分歧+{extra*100:.0f}%',
        'hits': hits,
        'pct': hits/total*100,
    })

# 策略D: 降水惩罚调整
for drought_max in [0.50, 0.40, 0.30, 0.20, 0]:
    hits = 0
    for d in data:
        if d['divs'] is None:
            continue
        params = {
            'upturn_levels': [0.10],
            'upturn_penalties': [0.30],
            'drought_levels': [0.10],
            'drought_penalties': [0.40 if drought_max>0 else 0],
            'drought_penalty_max': drought_max,
            'lgbm_disagree_extra': 0,
            'lgbm_probs': d['lgbm_probs'],
        }
        adj = distrust_adjust(d['raw_probs'], d['divs'], d['cur_odds'], params)
        if check_double_hit(d['result'], d['lgbm_probs'], adj):
            hits += 1
    strategies.append({
        'label': f'D-降水罚max={drought_max*100:.0f}%',
        'hits': hits,
        'pct': hits/total*100,
    })

# 打印结果排序
strategies.sort(key=lambda s: (-s['hits'], -s['pct']))
print(f'\n{"策略":<45} {"命中":<6} {"%"}\n{"-"*60}')
for s in strategies[:20]:
    marker = ' ★' if s['hits'] >= current_hits else ''
    print(f'{s["label"]:<45} {s["hits"]}/{total:<4} {s["pct"]:.1f}%{marker}')
print(f'\n当前逻辑: {current_hits}/{total} = {current_hits/total*100:.1f}%')
print(f'基准(无调整): {base_hits}/{total} = {base_hits/total*100:.1f}%')
