"""方案B: 分级离散惩罚 + 模型与LGBM分歧惩罚"""
import json

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
    
    div_w = (c[0]-o[0])/o[0]; div_d = (c[1]-o[1])/o[1]; div_l = (c[2]-o[2])/o[2]
    
    # 模型概率
    mw = m.get('model_win',0); md = m.get('model_draw',0); ml = m.get('model_loss',0)
    mt = mw+md+ml; mw/=mt; md/=mt; ml/=mt if mt else 1
    
    # LGBM概率
    lw = m.get('lgbm_win',0); ld = m.get('lgbm_draw',0); ll = m.get('lgbm_loss',0)
    lt = lw+ld+ll; lw/=lt; ld/=lt; ll/=lt if lt else 1
    
    # 模型方向（中值备选）
    sorted_m = sorted([('home',mw),('draw',md),('away',ml)], key=lambda x:-x[1])
    model_1st = sorted_m[0][0]  # 模型最大值
    model_mid = sorted_m[1][0]  # 模型中值（备选）
    
    # LGBM方向（主推）
    lgbm_pred = max([('home',lw),('draw',ld),('away',ll)], key=lambda x:x[1])[0]
    
    samples.append({'actual':actual, 'imp_w':imp_w,'imp_d':imp_d,'imp_l':imp_l,
        'div_w':div_w,'div_d':div_d,'div_l':div_l,'cur':c,'open':o,
        'mw':mw,'md':md,'ml':ml,
        'lw':lw,'ld':ld,'ll':ll,
        'model_1st':model_1st,'model_mid':model_mid,
        'lgbm_pred':lgbm_pred,
        'home_team':m.get('home_team',''),'away_team':m.get('away_team','')})

print(f"=== 回测样本: {len(samples)} 场 ===\n")

S=0.01; H=0.01

def base_prob(s):
    sw=s['imp_w']*(1-S)+S/3+H; sd=s['imp_d']*(1-S)+S/3-H*0.3; sl=s['imp_l']*(1-S)+S/3-H*0.3
    st=sw+sd+sl; return sw/st, sd/st, sl/st

def run_test(fn, desc):
    h,t = fn(samples)
    flag = " ★" if h/t*100 > 53.5 else ""
    print(f"  {desc:<36}| {h:>2}/{t:<3}| {h/t*100:>6.2f}%{flag}")
    return h/t

# ========== 基准 ==========
def baseline(samples):
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print(f"{'方法':<38}| {'命中':>5}| {'命中率':>7}")
print("-"*60)
b = run_test(baseline, "基准(纯平滑)")

# ========== 方案B: 分级离散惩罚 ==========
def tiered_penalty(samples, tiers):
    """
    tiers = [(threshold, distrust), ...] 按升序排列
    例如 [(0.03, 0.05), (0.05, 0.15), (0.10, 0.30), (0.20, 0.50)]
    """
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        for i in range(3):
            if s['cur'][i]>=7.0:
                distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                if d>0: # 回升
                    distrust=0
                    for th,p in tiers:
                        if d>th: distrust=p
                        else: break
                elif d<0: # 降水
                    distrust=0  # still trust falling odds
                else:
                    distrust=0
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print()
print("═══ 方案B: 分级离散惩罚（多种分级方案） ═══")

# B1: 温和分级
tiers_gentle = [(0.03, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.40)]
run_test(lambda s: tiered_penalty(s, tiers_gentle), "B1 温和 3/5/10/20%")

# B2: 中阶分级
tiers_med = [(0.03, 0.08), (0.05, 0.15), (0.10, 0.30), (0.20, 0.55)]
run_test(lambda s: tiered_penalty(s, tiers_med), "B2 中阶 3/5/10/20%")

# B3: 激进分级
tiers_agg = [(0.03, 0.10), (0.05, 0.20), (0.10, 0.40), (0.20, 0.65)]
run_test(lambda s: tiered_penalty(s, tiers_agg), "B3 激进 3/5/10/20%")

# B4: 更细分级6档
tiers_fine = [(0.01, 0.02), (0.03, 0.06), (0.05, 0.12), (0.08, 0.20), (0.15, 0.35), (0.25, 0.55)]
run_test(lambda s: tiered_penalty(s, tiers_fine), "B4 细6档 1/3/5/8/15/25%")

# B5: 再细 5档
tiers_fine2 = [(0.02, 0.03), (0.05, 0.10), (0.08, 0.18), (0.15, 0.35), (0.25, 0.50)]
run_test(lambda s: tiered_penalty(s, tiers_fine2), "B5 5档 2/5/8/15/25%")

# B6: 只3档, 高阈值
tiers_3 = [(0.03, 0.05), (0.10, 0.25), (0.20, 0.50)]
run_test(lambda s: tiered_penalty(s, tiers_3), "B6 3档 3/10/20%")

# B7: 2档 简单
tiers_2 = [(0.03, 0.06), (0.10, 0.30)]
run_test(lambda s: tiered_penalty(s, tiers_2), "B7 2档 3/10%")

# B8: 回升降水双向分级
def tiered_bidirectional(samples):
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        for i in range(3):
            if s['cur'][i]>=7.0:
                distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                if d>0.03:
                    if d>0.20: distrust=0.55
                    elif d>0.10: distrust=0.30
                    elif d>0.05: distrust=0.15
                    else: distrust=0.08
                elif d<-0.03:
                    if d<-0.20: distrust=0  # 大降水信任
                    elif d<-0.10: distrust=0
                    elif d<-0.05: distrust=0
                    else: distrust=0
                elif d<0:
                    distrust=0
                else: distrust=0
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)
run_test(tiered_bidirectional, "B8 回升分级/降水信任")

# ========== 新: 模型与LGBM分歧惩罚 ==========
print()
print("═══ 模型vsLGBM分歧惩罚 ═══")

def ml_disagree(samples, penalty_when_diff=0.20):
    """
    当模型(LGBM主推)和模型(贝叶斯中值)方向不一致时，
    说明模型自己不确定 → 增加惩罚
    """
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        
        # 检查分歧: LGBM主推 vs 模型中值备选
        lgbm_d = s['lgbm_pred']
        model_mid = s['model_mid']
        disagree = (lgbm_d != model_mid)
        
        for i in range(3):
            if s['cur'][i]>=7.0:
                distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                if d>0.03:
                    distrust = min(0.95, d*1.5)
                    # 如果模型内部有分歧，惩罚加倍
                    if disagree: distrust = min(0.95, distrust * 1.5)
                elif d<-0.03:
                    distrust=0
                elif d<0:
                    distrust = abs(d)/0.03*0.5
                else: distrust=0
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

for p in [0.1, 0.2, 0.3, 0.5, 1.0]:
    run_test(lambda s,pen=p: ml_disagree(s,pen), f"C1 模型分歧惩罚×{p:.1f}")

# C2: 更加直接: 当LGBM和模型最大值不一致时, 直接降整体置信度
def direct_disagree_boost(samples):
    """当两个模型打架时，分歧惩罚力度翻倍"""
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        
        lgbm_d=s['lgbm_pred']
        model_1st=s['model_1st']
        disagree=(lgbm_d!=model_1st)
        
        for i in range(3):
            if s['cur'][i]>=7.0:
                distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                distrust=0
                if d>0.03:
                    if d>0.20: distrust=0.40 if not disagree else 0.60
                    elif d>0.10: distrust=0.20 if not disagree else 0.35
                    elif d>0.05: distrust=0.10 if not disagree else 0.18
                    else: distrust=0.05 if not disagree else 0.08
                elif d<0:
                    distrust=abs(d)/0.03*0.3
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)
run_test(direct_disagree_boost, "C2 分级+分歧翻倍")

# C3: 直接基于 LGBM vs 模型 分歧深度惩罚所有方向
def disagree_apply_anyway(samples, base_penalty=0.08):
    """LGBM和模型方向不同→无条件增加对回升方向的惩罚"""
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        lgbm_d=s['lgbm_pred']; model_1st=s['model_1st']
        disagree=(lgbm_d!=model_1st)
        
        for i in range(3):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                distrust=0
                if d>0.03:
                    if d>0.20: distrust=0.45
                    elif d>0.10: distrust=0.25
                    elif d>0.05: distrust=0.12
                    else: distrust=0.05
                    if disagree: distrust=min(0.95, distrust+base_penalty)
                elif d<-0.03: distrust=0
                elif d<0: distrust=abs(d)/0.03*0.25
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

for p in [0.05, 0.08, 0.10, 0.15, 0.20]:
    run_test(lambda s,pen=p: disagree_apply_anyway(s,pen), f"C3 分歧+无条件+{p:.0%}")

# ========== 组合: 方案B + 模型LGBM分歧 ==========
print()
print("═══ 组合: 最佳分级 + 模型分歧 ═══")

def combined_best_tiered(samples, base_tiers=None, disagree_boost=0.10):
    """使用最佳分级方案 + 模型分歧惩罚"""
    if base_tiers is None:
        base_tiers = [(0.03, 0.08), (0.05, 0.15), (0.10, 0.30), (0.20, 0.55)]
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        probs=[pw,pd,pl]
        divs=[s['div_w'],s['div_d'],s['div_l']]
        lgbm_d=s['lgbm_pred']; model_1st=s['model_1st']
        disagree=(lgbm_d!=model_1st)
        
        for i in range(3):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,divs[i]))
                if d>0:
                    distrust=0
                    for th,p in base_tiers:
                        if d>th: distrust=p
                        else: break
                    if disagree: distrust = min(0.95, distrust + disagree_boost)
                elif d<0: distrust=0
                else: distrust=0
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

# 组合B2中阶分级 + 不同分歧惩罚
run_test(lambda s: combined_best_tiered(s, tiers_med, 0.05), "D1 B2+分歧+0.05")
run_test(lambda s: combined_best_tiered(s, tiers_med, 0.10), "D2 B2+分歧+0.10")
run_test(lambda s: combined_best_tiered(s, tiers_med, 0.15), "D3 B2+分歧+0.15")
run_test(lambda s: combined_best_tiered(s, tiers_med, 0.20), "D4 B2+分歧+0.20")

# 组合B1温和 + 分歧
run_test(lambda s: combined_best_tiered(s, tiers_gentle, 0.10), "D5 B1温和+分歧+0.10")
run_test(lambda s: combined_best_tiered(s, tiers_gentle, 0.15), "D6 B1温和+分歧+0.15")

# 组合B5(5档) + 分歧
run_test(lambda s: combined_best_tiered(s, tiers_fine2, 0.08), "D7 B5(5档)+分歧+0.08")
run_test(lambda s: combined_best_tiered(s, tiers_fine2, 0.12), "D8 B5(5档)+分歧+0.12")

# ========== 查看模型分歧数据 ==========
print()
print("--- 模型vsLGBM分歧统计 ---")
disagree_count=0
for s in samples:
    if s['lgbm_pred']!=s['model_1st']:
        disagree_count+=1
print(f"  LGBM主推 ≠ 模型最大值: {disagree_count}/{len(samples)} = {disagree_count/len(samples)*100:.1f}%")

# 分歧时的正确率
agree_hits=0; agree_total=0
disagree_hits=0; disagree_total=0
for s in samples:
    pw,pd,pl=base_prob(s)
    pred=max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]
    if s['lgbm_pred']==s['model_1st']:
        agree_total+=1
        if pred==s['actual']: agree_hits+=1
    else:
        disagree_total+=1
        if pred==s['actual']: disagree_hits+=1

print(f"  模型一致时准确率: {agree_hits/max(1,agree_total)*100:.1f}% ({agree_hits}/{agree_total})")
print(f"  模型分歧时准确率: {disagree_hits/max(1,disagree_total)*100:.1f}% ({disagree_hits}/{disagree_total})")
