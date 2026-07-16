"""深度测试更好的干预方法 v2"""
import json, math, statistics

d = json.load(open('docs/data/results.json'))

def parse_score(s):
    parts = s.split('-')
    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        sh, sa = int(parts[0]), int(parts[1])
        return 'home' if sh > sa else ('draw' if sh == sa else 'away')
    return None

samples = []
for m in d['matches']:
    s_val = m.get('score', '')
    actual = parse_score(s_val)
    cmp = m.get('comparison', {})
    if not actual or not cmp.get('open') or not cmp.get('current'):
        continue
    o = cmp['open']; c = cmp['current']
    if not (o[0]>1 and o[1]>1 and o[2]>1 and c[0]>1 and c[1]>1 and c[2]>1):
        continue
    total_cur = 1.0/c[0]+1.0/c[1]+1.0/c[2]
    imp_w = (1.0/c[0])/total_cur; imp_d = (1.0/c[1])/total_cur; imp_l = (1.0/c[2])/total_cur
    total_open = 1.0/o[0]+1.0/o[1]+1.0/o[2]
    
    div_w = (c[0]-o[0])/o[0]; div_d = (c[1]-o[1])/o[1]; div_l = (c[2]-o[2])/o[2]
    
    samples.append({'actual': actual, 'imp_w': imp_w, 'imp_d': imp_d, 'imp_l': imp_l,
        'div_w': div_w, 'div_d': div_d, 'div_l': div_l, 'cur': c, 'open': o,
        'model_win': m.get('model_win',0), 'model_draw': m.get('model_draw',0), 'model_loss': m.get('model_loss',0),
        'lgbm_win': m.get('lgbm_win',0), 'lgbm_draw': m.get('lgbm_draw',0), 'lgbm_loss': m.get('lgbm_loss',0),
        'home_team': m.get('home_team',''), 'away_team': m.get('away_team',''), 'score': m.get('score','')})

print(f"=== 回测样本: {len(samples)} 场 ===\n")

S = 0.01; H = 0.01

def base_prob(s):
    sw = s['imp_w']*(1-S)+S/3+H; sd = s['imp_d']*(1-S)+S/3-H*0.3; sl = s['imp_l']*(1-S)+S/3-H*0.3
    st = sw+sd+sl; return sw/st, sd/st, sl/st

def run_test(method_fn, desc):
    h, t = method_fn(samples)
    print(f"  {desc:<32}| {h:>3}/{t:<3}| {h/t*100:>6.2f}%")
    return h/t

# Distribution analysis
print("--- 分歧分布 ---")
all_divs = [abs(d) for s in samples for d in [s['div_w'], s['div_d'], s['div_l']]]
print(f"  绝对赔率分歧 | 均值={statistics.mean(all_divs):.3f} 中值={sorted(all_divs)[len(all_divs)//2]:.3f}  std={statistics.stdev(all_divs):.3f}")
print(f"  <1%: {sum(1 for d in all_divs if d<0.01)}  <3%: {sum(1 for d in all_divs if d<0.03)}  <5%: {sum(1 for d in all_divs if d<0.05)}")
print(f"  <10%: {sum(1 for d in all_divs if d<0.1)}  >=10%: {sum(1 for d in all_divs if d>=0.1)}")

rising = sum(1 for s in samples for d in [s['div_w'],s['div_d'],s['div_l']] if d > 0.05)
falling = sum(1 for s in samples for d in [s['div_w'],s['div_d'],s['div_l']] if d < -0.05)
print(f"  回升>5%: {rising} 次, 降水>5%: {falling} 次\n")

def baseline(samples):
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

def threshold_any(samples, thresh=0.03, rise_penalty_mul=1.5, fall_penalty_mul=0.5):
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if d > thresh:
                    distrust = min(0.95, d * rise_penalty_mul)
                elif d < -thresh:
                    distrust = 0
                elif d < 0:
                    distrust = abs(d) / thresh * fall_penalty_mul
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

# ====== 新方法: 直接对隐含概率做delta调整 ======
# Instead of distrust multiplier, directly shift the implied probability towards/away
def delta_shift(samples, shift_factor=0.3):
    """
    不惩罚概率，而是直接让概率向/背离分歧方向移动。
    如果方向A降水(市场钱砸向A)，A的概率增加shift_factor * |div|。
    如果方向A回升(市场从A撤退)，A的概率减少shift_factor * |div|。
    """
    hits = 0
    for s in samples:
        sw = s['imp_w']*(1-S)+S/3+H; sd = s['imp_d']*(1-S)+S/3-H*0.3; sl = s['imp_l']*(1-S)+S/3-H*0.3
        st = sw+sd+sl
        pw, pd, pl = sw/st, sd/st, sl/st
        
        divs = [s['div_w'], s['div_d'], s['div_l']]
        probs = [pw, pd, pl]
        
        for i in range(3):
            d = max(-1, min(1, divs[i]))
            # shift_factor * d: if d>0 (回升), probs decrease; if d<0 (降水), probs increase
            probs[i] += shift_factor * d  # d is negative for 降水, so this adds
            probs[i] = max(0.001, probs[i])
        
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

# ====== 新方法: 直接关注分歧最大的方向 ======
def max_divergence_focus(samples, penalty=0.15):
    """
    分歧最大的方向（无论回升/降水），降低其概率。
    原理：过大分歧说明市场对该方向最不确定。
    """
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        abs_divs = [abs(d) for d in divs]
        max_idx = abs_divs.index(max(abs_divs))
        
        # Penalize the direction with max divergence only
        if abs_divs[max_idx] > 0.03:
            probs[max_idx] *= (1 - penalty)
            t = sum(probs)
            pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

# ====== 新方法: 离散化多级惩罚 ======
def tiered_penalty(samples):
    """多级离散惩罚，不是线性"""
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if d > 0.20: distrust = 0.80   # 大回升
                elif d > 0.10: distrust = 0.50  # 中回升
                elif d > 0.05: distrust = 0.25  # 小回升
                elif d > 0.03: distrust = 0.10  # 微回升
                elif d < -0.10: distrust = 0    # 大降水
                elif d < -0.05: distrust = 0    # 中降水
                elif d < -0.03: distrust = 0.10 # 微降水也小打
                elif d < 0: distrust = abs(d)/0.03*0.10
                else: distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

# ====== 新方法: 对抗过拟合——滚动窗口 ======
# (Use the most recent data for testing, older for training)
def windowed_approach(samples):
    """先用前70%数据找最佳阈值，再测后30%"""
    split = int(len(samples) * 0.7)
    train = samples[:split]
    test = samples[split:]
    
    # Find best threshold on train set
    best_t = 0.03; best_h = 0
    for thresh in [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15]:
        h, _ = threshold_any(train, thresh)
        if h > best_h:
            best_h = h; best_t = thresh
    
    # Run on test set with best threshold
    h, t = threshold_any(test, best_t)
    return h, t, best_t

# ====== 新方法: 分歧>模型概率越大的方向越不可信 ======
def div_vs_model_gap(samples):
    """当div和模型概率都指向同一方向时，降水分歧强化信心，回升分歧惩罚"""
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        m_probs = [s['model_win']/max(0.01,s['model_win']+s['model_draw']+s['model_loss']),
                   s['model_draw']/max(0.01,s['model_win']+s['model_draw']+s['model_loss']),
                   s['model_loss']/max(0.01,s['model_win']+s['model_draw']+s['model_loss'])]
        
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                # 只有分歧和模型概率方向一致时才调整
                base_idx = max(enumerate(probs), key=lambda x:x[1])[0]
                high_model = m_probs[i] > 0.33  # model assigns >33% to this
                
                if d > 0.03:  # 回升
                    distrust = min(0.95, abs(d) * 2.0 if high_model else abs(d) * 0.5)
                elif d < -0.03:  # 降水
                    distrust = 0  # 信任
                elif d < 0:
                    distrust = abs(d) / 0.03 * 0.30
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)

# Run
b_rate = run_test(lambda s: baseline(s), "基准(纯平滑)")
print()
print("--- 阈值变体（调整判别灵敏度） ---")
run_test(lambda s: threshold_any(s, 0.03), "阈值±3%")
run_test(lambda s: threshold_any(s, 0.01), "阈值±1% (最敏感)")
run_test(lambda s: threshold_any(s, 0.02), "阈值±2%")
run_test(lambda s: threshold_any(s, 0.05), "阈值±5%")
run_test(lambda s: threshold_any(s, 0.03, rise_penalty_mul=2.5), "阈值±3%+强惩罚(×2.5)")
run_test(lambda s: threshold_any(s, 0.03, rise_penalty_mul=1.0, fall_penalty_mul=0.3), "阈值±3%+温和")

print()
print("--- 新型干预方法 ---")
run_test(delta_shift, "Delta偏移(shift=0.3)")
run_test(lambda s: delta_shift(s, 0.5), "Delta偏移(shift=0.5)")
run_test(lambda s: delta_shift(s, 0.1), "Delta偏移(shift=0.1)")
run_test(max_divergence_focus, "最大分歧方向压制")
run_test(tiered_penalty, "多级离散惩罚")
run_test(div_vs_model_gap, "模型概率对齐调整")

print()
print("--- 滑动窗口验证 (前70%训练选最佳阈值 → 后30%测试) ---")
h, t, best_t = windowed_approach(samples)
print(f"  最佳阈值={best_t:.2f}, 测试集: {h}/{t} = {h/t*100:.1f}%")
