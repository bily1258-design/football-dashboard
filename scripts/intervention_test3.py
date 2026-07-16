"""干预方法深度测试 v3: LGBM对齐 + 组合策略"""
import json, statistics

d = json.load(open('docs/data/results.json'))

def parse_score(s):
    parts = s.split('-')
    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        sh, sa = int(parts[0]), int(parts[1])
        return 'home' if sh > sa else ('draw' if sh == sa else 'away')
    return None

samples = []
idx_map = []  # map sample index -> match index in d['matches']
for mi, m in enumerate(d['matches']):
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
    
    div_w = (c[0]-o[0])/o[0]; div_d = (c[1]-o[1])/o[1]; div_l = (c[2]-o[2])/o[2]
    
    # LGBM probabilities (from results.json fields)
    mw = m.get('model_win', 0); md = m.get('model_draw', 0); ml = m.get('model_loss', 0)
    m_total = mw + md + ml
    mw_norm = mw/m_total if m_total > 0 else 1/3
    md_norm = md/m_total if m_total > 0 else 1/3
    ml_norm = ml/m_total if m_total > 0 else 1/3
    
    lw = m.get('lgbm_win', 0); ld = m.get('lgbm_draw', 0); ll = m.get('lgbm_loss', 0)
    l_total = lw + ld + ll
    lw_norm = lw/l_total if l_total > 0 else 1/3
    ld_norm = ld/l_total if l_total > 0 else 1/3
    ll_norm = ll/l_total if l_total > 0 else 1/3
    
    # LGBM predicted direction
    lgbm_pred = max([('home',lw_norm),('draw',ld_norm),('away',ll_norm)], key=lambda x:x[1])[0]
    lgbm_conf = max(lw_norm, ld_norm, ll_norm)
    
    # Model direction
    model_pred = max([('home',mw_norm),('draw',md_norm),('away',ml_norm)], key=lambda x:x[1])[0]
    
    samples.append({'actual': actual, 'imp_w': imp_w, 'imp_d': imp_d, 'imp_l': imp_l,
        'div_w': div_w, 'div_d': div_d, 'div_l': div_l, 'cur': c, 'open': o,
        'lgbm_pred': lgbm_pred, 'lgbm_conf': lgbm_conf,
        'model_pred': model_pred,
        'home_team': m.get('home_team',''), 'away_team': m.get('away_team',''),
        'score': m.get('score','')})

print(f"=== 回测样本: {len(samples)} 场 ===\n")

S = 0.01; H = 0.01

def base_prob(s):
    sw = s['imp_w']*(1-S)+S/3+H; sd = s['imp_d']*(1-S)+S/3-H*0.3; sl = s['imp_l']*(1-S)+S/3-H*0.3
    st = sw+sd+sl; return sw/st, sd/st, sl/st

def run_test(fn, desc):
    h, t = fn(samples)
    print(f"  {desc:<34}| {h:>3}/{t:<3}| {h/t*100:>6.2f}%")
    return h/t

print(f"{'方法':<36}| {'命中':>6}| {'命中率':>7}")
print("-" * 57)

# Baseline
def bl(samples):
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        pred = max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0]
        if pred == s['actual']: hits += 1
    return hits, len(samples)
b = run_test(bl, "基准(纯平滑)")

# LGBM alone (no divergence)
def lgbm_only(samples):
    hits = 0
    for s in samples:
        if s['lgbm_pred'] == s['actual']: hits += 1
    return hits, len(samples)
lgbm_r = run_test(lgbm_only, "LGBM纯预测(无分歧)")

# Best threshold ±3%
def thresh_3(samples):
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        for i, d in enumerate([s['div_w'], s['div_d'], s['div_l']]):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d2 = max(-1, min(1, d))
                if d2 > 0.03:
                    distrust = min(0.95, d2 * 1.5)
                elif d2 < -0.03:
                    distrust = 0
                elif d2 < 0:
                    distrust = abs(d2) / 0.03 * 0.5
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0] == s['actual']: hits += 1
    return hits, len(samples)

print()
print("--- 阈值法 ---")
t3 = run_test(thresh_3, "阈值±3%")

# ===== 新方法: LGBM方向 vs 分歧方向 =====
def lgbm_div_conflict(samples, conflict_penalty=0.3, confirm_boost=0.0):
    """
    关键洞察: 当分歧方向与LGBM矛盾时才重要。
    - LGBM说主胜，但主胜赔率回升(市场不看好) → 惩罚
    - LGBM说主胜，主胜赔率降水(市场看好) → 不管或者稍微加强
    - 非LGBM的方向随便怎么动，不关心
    """
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        
        lgbm_idx = {'home':0, 'draw':1, 'away':2}[s['lgbm_pred']]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        
        probs = [pw, pd, pl]
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if i == lgbm_idx:
                    # This IS the LGBM direction
                    if d > 0.03:  # 回升 → LGBM方向被市场反驳
                        distrust = min(0.95, d * conflict_penalty * 5)
                    elif d < -0.03:  # 降水 → 市场确认LGBM
                        distrust = 0  # 完全信任
                    elif d < 0:
                        distrust = abs(d) / 0.03 * 0.2
                    else:
                        distrust = 0
                else:
                    # Not the LGBM direction — if it's dropping a lot, market is flowing AWAY from non-LGBM
                    # which indirectly CONFIRMS LGBM
                    if d > 0.03:
                        distrust = min(0.95, d * 0.5)  # mild distrust always
                    elif d < -0.03:
                        # Money flowing to a non-LGBM direction — concerning
                        distrust = min(0.95, abs(d) * 0.5)  # but mild
                    elif d < 0:
                        distrust = abs(d) / 0.03 * 0.1
                    else:
                        distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0] == s['actual']: hits += 1
    return hits, len(samples)

# ===== 只关注LGBM方向是否被反驳 =====
def only_lgbm_contradiction(samples, penalty=3.0):
    """
    更干净的版本: 只看LGBM方向的赔率是否回升。
    如果LGBM说主胜但主胜赔率大幅回升 → 这很可能错了。
    """
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        
        lgbm_idx = {'home':0, 'draw':1, 'away':2}[s['lgbm_pred']]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        
        probs = list(base_prob(s))
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if i == lgbm_idx:
                    if d > 0.03:  # LGBM方向回升 = 市场反对LGBM
                        distrust = min(0.95, abs(d) * penalty)
                    elif d < -0.03:  # LGBM方向降水 = 市场支持LGBM
                        distrust = 0
                    else:
                        distrust = 0
                else:
                    distrust = 0  # 其他方向不管
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0] == s['actual']: hits += 1
    return hits, len(samples)

# ===== 联合推荐方向一致性检查 =====
def dual_recommend_confirm(samples, penalty=3.0):
    """
    当LGBM主推和模型中值备选都指向同一方向时，
    且该方向赔率回升 → 惩罚
    且该方向赔率降水 → 信任（不做惩罚）
    """
    hits = 0
    for s in samples:
        pw, pd, pl = base_prob(s)
        probs = [pw, pd, pl]
        divs = [s['div_w'], s['div_d'], s['div_l']]
        
        # LGBM direction (主推)
        lgbm_idx = {'home':0, 'draw':1, 'away':2}[s['lgbm_pred']]
        
        for i in range(3):
            if s['cur'][i] >= 7.0:
                distrust = 0.85
            else:
                d = max(-1, min(1, divs[i]))
                if i == lgbm_idx:
                    if d > 0.03:  # LGBM方向回升
                        distrust = min(0.95, abs(d) * penalty)
                    else:
                        distrust = 0
                else:
                    distrust = 0
            probs[i] *= max(0.05, 1 - distrust)
        t = sum(probs)
        pw, pd, pl = probs[0]/t, probs[1]/t, probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)], key=lambda x:x[1])[0] == s['actual']: hits += 1
    return hits, len(samples)

# ===== 分歧敏感度分析 =====
print()
print("--- LGBM对齐分歧调整 ---")
for p in [1.0, 2.0, 3.0, 5.0, 10.0]:
    run_test(lambda s, pp=p: only_lgbm_contradiction(s, pp), f"LGBM对齐 penalty={p:.0f}×回升")

run_test(lambda s: lgbm_div_conflict(s, 0.3, 0), "LGBM双向调整(矛盾惩罚)")
run_test(lambda s: lgbm_div_conflict(s, 1.0, 0), "LGBM双向(强矛盾)")

print()
print("--- 双推荐方向对齐 ---")
for p in [1.0, 2.0, 3.0, 5.0]:
    run_test(lambda s, pp=p: dual_recommend_confirm(s, pp), f"双推荐一致(p={p:.0f})")

# ===== 分歧 > 0 时的实际效果分析 =====
print()
print("--- 分歧方向与LGBM方向关系 ---")
agree_re = 0  # LGBM correct when divergence agrees
agree_wr = 0  # LGBM wrong when divergence agrees
conf_re = 0   # LGBM correct when divergence contradicts
conf_wr = 0   # LGBM wrong when divergence contradicts
for s in samples:
    lgbm_idx = {'home':0, 'draw':1, 'away':2}[s['lgbm_pred']]
    divs = [s['div_w'], s['div_d'], s['div_l']]
    lgbm_right = (s['lgbm_pred'] == s['actual'])
    
    # Does the divergence agree with LGBM? (LGBM direction is falling = agree)
    if divs[lgbm_idx] < -0.03:
        if lgbm_right: agree_re += 1
        else: agree_wr += 1
    elif divs[lgbm_idx] > 0.03:
        if lgbm_right: conf_re += 1
        else: conf_wr += 1

total_agree = agree_re + agree_wr
total_conf = conf_re + conf_wr
print(f"  LGBM方向降水(市场支持): {total_agree}场 LGBM正确率: {agree_re/max(1,total_agree)*100:.1f}% ({agree_re}/{total_agree})")
print(f"  LGBM方向回升(市场反对): {total_conf}场 LGBM正确率: {conf_re/max(1,total_conf)*100:.1f}% ({conf_re}/{total_conf})")

# Also check: when divergence is large (>10%) in either direction
big_agree = sum(1 for s in samples if ({'home':0,'draw':1,'away':2}[s['lgbm_pred']]==0) and s['div_w'] < -0.1)
print(f"\n  大分歧(>10%): LGBM方向降水: {big_agree}")
