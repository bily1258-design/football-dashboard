"""全面对比不同分歧干预方法"""
import json, math

d = json.load(open('docs/data/results.json'))

# Prepare samples: matches with score + opening odds
def parse_score(s):
    parts = s.split('-')
    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        sh, sa = int(parts[0]), int(parts[1])
        return 'home' if sh > sa else ('draw' if sh == sa else 'away')
    return None

samples = []
for m in d['matches']:
    s = m.get('score', '')
    actual = parse_score(s)
    cmp = m.get('comparison', {})
    if not actual or not cmp.get('open') or not cmp.get('current'):
        continue
    o = cmp['open']
    c = cmp['current']
    if not (o[0] > 1 and o[1] > 1 and o[2] > 1 and c[0] > 1 and c[1] > 1 and c[2] > 1):
        continue
    total_cur = 1.0/c[0] + 1.0/c[1] + 1.0/c[2]
    imp_w = (1.0/c[0]) / total_cur
    imp_d = (1.0/c[1]) / total_cur
    imp_l = (1.0/c[2]) / total_cur
    
    total_open = 1.0/o[0] + 1.0/o[1] + 1.0/o[2]
    open_w = (1.0/o[0]) / total_open
    open_d = (1.0/o[1]) / total_open
    open_l = (1.0/o[2]) / total_open
    
    div_w = (c[0] - o[0]) / o[0]
    div_d = (c[1] - o[1]) / o[1]
    div_l = (c[2] - o[2]) / o[2]
    
    pdiv_w = imp_w - open_w
    pdiv_d = imp_d - open_d
    pdiv_l = imp_l - open_l
    
    samples.append({
        'actual': actual,
        'imp_w': imp_w, 'imp_d': imp_d, 'imp_l': imp_l,
        'open_w': open_w, 'open_d': open_d, 'open_l': open_l,
        'div_w': div_w, 'div_d': div_d, 'div_l': div_l,
        'pdiv_w': pdiv_w, 'pdiv_d': pdiv_d, 'pdiv_l': pdiv_l,
        'cur': c, 'open': o,
    })

print(f"=== 回测样本: {len(samples)} 场 (含初即盘+比分) ===")
print(f"初盘平均边际: {sum(1.0/s['open'][0]+1.0/s['open'][1]+1.0/s['open'][2]-1 for s in samples)/len(samples)*100:.1f}%")
print(f"即时盘平均边际: {sum(1.0/s['cur'][0]+1.0/s['cur'][1]+1.0/s['cur'][2]-1 for s in samples)/len(samples)*100:.1f}%\n")

SMOOTH_ALPHA = 0.01
HOME_ADJ = 0.01

def baseline(samples):
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        probs = [('home', sw/st), ('draw', sd/st), ('away', sl/st)]
        if max(probs, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

def threshold_distrust(samples, thresh=0.10):
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        pw, pd, pl = sw/st, sd/st, sl/st
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if d > thresh:
                    distrust = min(0.95, d * 1.5)
                elif d < -thresh:
                    distrust = 0
                elif d < 0:
                    distrust = abs(d) / thresh * 0.50
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = [('home', pw), ('draw', pd), ('away', pl)]
        if max(pred, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

def beta_additive(samples, beta, use_odds=True):
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        if use_odds:
            sw += beta * s['div_w']
            sd += beta * s['div_d']
            sl += beta * s['div_l']
        else:
            sw += beta * s['pdiv_w']
            sd += beta * s['pdiv_d']
            sl += beta * s['pdiv_l']
        st = sw + sd + sl
        probs = [('home', sw/st), ('draw', sd/st), ('away', sl/st)]
        if max(probs, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

def soft_sigmoid(samples, strength):
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        pw, pd, pl = sw/st, sd/st, sl/st
        probs = [pw, pd, pl]
        for i, d in enumerate([s['div_w'], s['div_d'], s['div_l']]):
            if s['cur'][i] >= 7.0:
                penalty = 0.85
            else:
                penalty = 1.0 / (1.0 + math.exp(-strength * d * 10))
            probs[i] *= max(0.05, 1 - penalty)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = [('home', pw), ('draw', pd), ('away', pl)]
        if max(pred, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

def prob_softmax(samples, temp_base):
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        pw, pd, pl = sw/st, sd/st, sl/st
        max_div = max(abs(s['div_w']), abs(s['div_d']), abs(s['div_l']))
        temp = temp_base / (1 + max_div * 5)
        temp = max(0.1, min(1.0, temp))
        probs = [pw**(1/temp), pd**(1/temp), pl**(1/temp)]
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = [('home', pw), ('draw', pd), ('away', pl)]
        if max(pred, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

def combined_best(samples, beta_odds, beta_pdiv, threshold_type='odds'):
    """Combine beta additive + threshold distrust"""
    hits = 0
    for s in samples:
        sw = s['imp_w'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = s['imp_d'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = s['imp_l'] * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        # Beta additive
        if threshold_type == 'odds':
            sw += beta_odds * s['div_w']; sd += beta_odds * s['div_d']; sl += beta_odds * s['div_l']
        else:
            sw += beta_pdiv * s['pdiv_w']; sd += beta_pdiv * s['pdiv_d']; sl += beta_pdiv * s['pdiv_l']
        st = sw + sd + sl
        pw, pd, pl = sw/st, sd/st, sl/st
        # Then threshold distrust on top
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if d > 0.10:
                    distrust = min(0.95, d * 1.5)
                elif d < -0.10:
                    distrust = 0
                elif d < 0:
                    distrust = abs(d) / 0.10 * 0.50
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        pred = [('home', pw), ('draw', pd), ('away', pl)]
        if max(pred, key=lambda x: x[1])[0] == s['actual']:
            hits += 1
    return hits, len(samples)

# ====== RESULTS ======
print(f"{'方法':<30} | {'命中':>4} | {'总数':>4} | {'命中率':>8} | {'提升':>8}")
print("-" * 70)

b_hits, b_total = baseline(samples)
b_rate = b_hits / b_total
print(f"{'基准(纯平滑无分歧)':<30} | {b_hits:>4} | {b_total:>4} | {b_rate*100:>6.2f}% | {'---':>8}")

t_hits, t_total = threshold_distrust(samples)
t_rate = t_hits / t_total
imp = (t_rate - b_rate) / b_rate * 100
print(f"{'阈值±10%(现行)':<30} | {t_hits:>4} | {t_total:>4} | {t_rate*100:>6.2f}% | {imp:+>6.2f}%")

print()
print("═══ 一、赔率分歧 add β（赔率变化%直接加概率） ═══")
best_ob = (0, 0, 0)
for beta in [0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0]:
    h, t = beta_additive(samples, beta, use_odds=True)
    rate = h / t
    imp = (rate - b_rate) / b_rate * 100
    better = " ★" if rate > t_rate else ""
    print(f"  odds_div×β={beta:<5.2f}{'':<12}| {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%{better}")
    if rate > best_ob[2]:
        best_ob = (beta, h, rate)

print()
print("═══ 二、概率分歧 add β（隐含概率变化直接加概率） ═══")
best_pb = (0, 0, 0)
for beta in [0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]:
    h, t = beta_additive(samples, beta, use_odds=False)
    rate = h / t
    imp = (rate - b_rate) / b_rate * 100
    better = " ★" if rate > t_rate else ""
    print(f"  prob_div×β={beta:<5.2f}{'':<12}| {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%{better}")
    if rate > best_pb[2]:
        best_pb = (beta, h, rate)

print()
print("═══ 三、阈值变体（调整±%阈值） ═══")
best_tv = (0, 0, 0)
for thresh in [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
    h, t = threshold_distrust(samples, thresh)
    rate = h / t
    imp = (rate - b_rate) / b_rate * 100
    better = " ★" if rate > t_rate else ""
    print(f"  阈值±{thresh*100:.0f}%{'':<18}          | {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%{better}")
    if rate > best_tv[2]:
        best_tv = (thresh, h, rate)

print()
print("═══ 四、Sigmoid连续惩罚（无硬阈值） ═══")
best_sg = (0, 0, 0)
for strength in [0.5, 1.0, 2.0, 3.0, 5.0, 10.0]:
    h, t = soft_sigmoid(samples, strength)
    rate = h / t
    imp = (rate - b_rate) / b_rate * 100
    better = " ★" if rate > t_rate else ""
    print(f"  sigmoid s={strength:<5.1f}{'':<13}| {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%{better}")
    if rate > best_sg[2]:
        best_sg = (strength, h, rate)

print()
print("═══ 五、Softmax变温（分歧大→温度低→概率sharp） ═══")
best_st = (0, 0, 0)
for tb in [0.3, 0.5, 0.7, 1.0]:
    h, t = prob_softmax(samples, tb)
    rate = h / t
    imp = (rate - b_rate) / b_rate * 100
    better = " ★" if rate > t_rate else ""
    print(f"  温度 t={tb:<5.1f}{'':<19}      | {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%{better}")
    if rate > best_st[2]:
        best_st = (tb, h, rate)

print()
print("═══ 六、β+阈值叠加（β先把赔率变化拉进去，再用阈值惩罚） ═══")
# Try best beta + current threshold
best_beta_odds, _, _ = best_ob
best_beta_pdiv, _, _ = best_pb
h, t = combined_best(samples, best_beta_odds, best_beta_pdiv, 'odds')
rate = h / t
imp = (rate - b_rate) / b_rate * 100
print(f"  赔率β={best_beta_odds:.2f}+阈值±10%{'':<9}| {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%")

h, t = combined_best(samples, best_beta_odds, best_beta_pdiv, 'prob')
rate = h / t
imp = (rate - b_rate) / b_rate * 100
print(f"  概率β={best_beta_pdiv:.2f}+阈值±10%{'':<9}| {h:>4} | {t:>4} | {rate*100:>6.2f}% | {imp:+>6.2f}%")

print()
print("══════════════════════════════════════════")
print(f"基准命中: {b_rate*100:.1f}% ({b_hits}/{b_total})")
print(f"现行法(±10%阈值): {t_rate*100:.1f}% ({t_hits}/{t_total})")
print(f"最佳赔率分歧β: β={best_ob[0]:.2f}, {best_ob[2]*100:.1f}%")
print(f"最佳概率分歧β: β={best_pb[0]:.2f}, {best_pb[2]*100:.1f}%")
print(f"最佳阈值: ±{best_tv[0]*100:.0f}%, {best_tv[2]*100:.1f}%")
print(f"最佳Sigmoid: s={best_sg[0]:.1f}, {best_sg[2]*100:.1f}%")
