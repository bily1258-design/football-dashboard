"""测试不同分歧权重β值的回测命中率"""
import json, math, sys

def implied_from_odds(w, d, l):
    if w <= 0 or d <= 0 or l <= 0:
        return None, None, None
    iw, id_, il = 1.0 / w, 1.0 / d, 1.0 / l
    total = iw + id_ + il
    return iw / total, id_ / total, il / total

def test_beta(data, beta, home_adj=0.01, smooth_alpha=0.01):
    """用给定β测试，返回命中数和总数"""
    hits = 0
    total = 0
    for m in data:
        imp_w, imp_d, imp_l = m['imp_w'], m['imp_d'], m['imp_l']
        open_w, open_d, open_l = m['open']
        
        # 开盘隐含概率
        oiw, oid_, oil = implied_from_odds(open_w, open_d, open_l)
        if oiw is None:
            continue
        
        # 当前隐含概率
        ciw, cid_, cil = imp_w, imp_d, imp_l
        
        # 分歧信号 = 当前隐含 - 开盘隐含
        div_w = ciw - oiw
        div_d = cid_ - oid_
        div_l = cil - oil
        
        # 贝叶斯平滑基础
        sw = imp_w * (1 - smooth_alpha) + smooth_alpha / 3 + home_adj
        sd = imp_d * (1 - smooth_alpha) + smooth_alpha / 3 - home_adj * 0.3
        sl = imp_l * (1 - smooth_alpha) + smooth_alpha / 3 - home_adj * 0.3
        
        # 分歧调整
        sw += beta * div_w
        sd += beta * div_d
        sl += beta * div_l
        
        st = sw + sd + sl
        model_w, model_d, model_l = sw/st, sd/st, sl/st
        
        # 取最大概率方向
        probs = [('home', model_w), ('draw', model_d), ('away', model_l)]
        direction = max(probs, key=lambda x: x[1])[0]
        
        if direction == m['actual']:
            hits += 1
        total += 1
    
    return hits, total

# 加载数据
d = json.load(open('docs/data/results.json'))
matches = []
for m in d['matches']:
    s = m.get('score', '')
    parts = s.split('-')
    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        sh, sa = int(parts[0]), int(parts[1])
        actual = 'home' if sh > sa else ('draw' if sh == sa else 'away')
    else:
        actual = ''
    
    cmp = m.get('comparison', {}) or {}
    p = cmp.get('pinnacle', {}) or {}
    open_odds = p.get('open', [])
    
    if open_odds and len(open_odds) == 3 and actual:
        matches.append({
            'imp_w': m['implied_win'],
            'imp_d': m['implied_draw'],
            'imp_l': m['implied_loss'],
            'open': open_odds,
            'actual': actual
        })

print(f"=== 回测样本: {len(matches)} 场 \
(含开盘赔率+已有比分) ===\n")

print(f"{'β':>6} | {'命中':>4} | {'总数':>4} | {'命中率':>7} | {'主胜':>4}{'平局':>5}{'客胜':>5} | 说明")
print("-" * 65)

betas = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0]
results = []

for beta in betas:
    hits, total = test_beta(matches, beta)
    rate = hits / total if total else 0
    
    # 统计方向分布
    dirs = {'home': 0, 'draw': 0, 'away': 0}
    for m in matches:
        imp_w, imp_d, imp_l = m['imp_w'], m['imp_d'], m['imp_l']
        oiw, oid_, oil = implied_from_odds(m['open'][0], m['open'][1], m['open'][2])
        div_w = imp_w - oiw
        div_d = imp_d - oid_
        div_l = imp_l - oil
        sw = imp_w * 0.99 + 0.01 / 3 + 0.01 + beta * div_w
        sd = imp_d * 0.99 + 0.01 / 3 - 0.01 * 0.3 + beta * div_d
        sl = imp_l * 0.99 + 0.01 / 3 - 0.01 * 0.3 + beta * div_l
        st = sw + sd + sl
        mw, md, ml = sw/st, sd/st, sl/st
        probs = [('home', mw), ('draw', md), ('away', ml)]
        dirs[max(probs, key=lambda x: x[1])[0]] += 1
    
    results.append((beta, rate, hits, total, dirs))
    note = ""
    if beta == 0.0:
        note = "← 基准：纯平滑，无分歧调整"
    best_hits = max(r[2] for r in results)
    if hits >= best_hits:
        note = "← ★ 最优"

    print(f"{beta:>5.2f} | {hits:>4} | {total:>4} | {rate*100:>6.2f}% | \
{dirs['home']:>4}{dirs['draw']:>5}{dirs['away']:>5} | {note}")

print()
print("结论：β=0.0 = 不使用分歧信号（仅贝叶斯平滑+主场修正）")
print("β越大，分歧信号对模型调整越强")
