"""精确测试: 模型分歧针对性惩罚 + B3激进分级"""
import json

d = json.load(open('docs/data/results.json'))

def parse_score(s):
    parts = s.split('-')
    if len(parts)==2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        sh,sa=int(parts[0]),int(parts[1])
        return 'home' if sh>sa else ('draw' if sh==sa else 'away')
    return None

samples=[]
for m in d['matches']:
    s_val=m.get('score','')
    actual=parse_score(s_val)
    cmp=m.get('comparison',{})
    if not actual or not cmp.get('open') or not cmp.get('current'): continue
    o=cmp['open']; c=cmp['current']
    if not(o[0]>1 and o[1]>1 and o[2]>1 and c[0]>1 and c[1]>1 and c[2]>1): continue
    total_cur=1.0/c[0]+1.0/c[1]+1.0/c[2]
    imp_w=(1.0/c[0])/total_cur; imp_d=(1.0/c[1])/total_cur; imp_l=(1.0/c[2])/total_cur
    div_w=(c[0]-o[0])/o[0]; div_d=(c[1]-o[1])/o[1]; div_l=(c[2]-o[2])/o[2]
    
    mw=m.get('model_win',0); md=m.get('model_draw',0); ml=m.get('model_loss',0)
    mt=mw+md+ml; mw/=mt; md/=mt; ml/=mt if mt else 1
    lw=m.get('lgbm_win',0); ld=m.get('lgbm_draw',0); ll=m.get('lgbm_loss',0)
    lt=lw+ld+ll; lw/=lt; ld/=lt; ll/=lt if lt else 1
    
    sorted_m=sorted([('home',mw),('draw',md),('away',ml)],key=lambda x:-x[1])
    model_1st=sorted_m[0][0]; model_mid=sorted_m[1][0]
    lgbm_pred=max([('home',lw),('draw',ld),('away',ll)],key=lambda x:x[1])[0]
    
    samples.append({'actual':actual,'imp_w':imp_w,'imp_d':imp_d,'imp_l':imp_l,
        'div_w':div_w,'div_d':div_d,'div_l':div_l,'cur':c,'open':o,
        'model_1st':model_1st,'model_mid':model_mid,
        'lgbm_pred':lgbm_pred,'lgbm_conf':max(lw,ld,ll)})

print(f"=== 回测样本: {len(samples)} 场 ===\n")

S=0.01; H=0.01

def base_prob(s):
    sw=s['imp_w']*(1-S)+S/3+H; sd=s['imp_d']*(1-S)+S/3-H*0.3; sl=s['imp_l']*(1-S)+S/3-H*0.3
    st=sw+sd+sl; return sw/st, sd/st, sl/st

def run_test(fn, desc):
    h,t=fn(samples)
    flag=" ★" if h/t*100>=54.6 else ""
    print(f"  {desc:<40}| {h:>2}/{t:<3}| {h/t*100:>6.2f}%{flag}")
    return h/t

def baseline(samples):
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s)
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print(f"{'方法':<42}| {'命中':>5}| {'命中率':>7}")
print("-" * 62)
b=run_test(baseline,"基准(纯平滑)")

# B3激进分级
def b3_tier(samples):
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs=[pw,pd,pl]
        for i,d_ in enumerate([s['div_w'],s['div_d'],s['div_l']]):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,d_))
                distrust=0
                if d>0.03: distrust=0.10
                if d>0.05: distrust=0.20
                if d>0.10: distrust=0.40
                if d>0.20: distrust=0.65
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

run_test(b3_tier,"B3 激进分级 3/5/10/20%")

# ===== 新: 模型分歧时增强惩罚 =====
def b3_disagree_boost(samples, boost):
    """B3分级 + 模型分歧时额外boost惩罚"""
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs=[pw,pd,pl]
        disagree=(s['lgbm_pred']!=s['model_1st'])
        for i,d_ in enumerate([s['div_w'],s['div_d'],s['div_l']]):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,d_))
                distrust=0
                if d>0.03: distrust=0.10
                if d>0.05: distrust=0.20
                if d>0.10: distrust=0.40
                if d>0.20: distrust=0.65
                if disagree and d>0:
                    distrust=min(0.95, distrust+boost)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print()
print("--- B3 + 模型分歧额外惩罚 ---")
for b in [0.05,0.08,0.10,0.12,0.15,0.20,0.25,0.30]:
    run_test(lambda s,bb=b: b3_disagree_boost(s,bb), f"B3+分歧+{b:.0%}")

# ===== 新: 分歧时不做B3惩罚，专门处理分歧场 =====
def disagree_special(samples):
    """
    模型分歧的12场特殊处理:
    - LGBM方向回升 → 大幅惩罚
    - 其他方向随便动 → 中度惩罚
    """
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs=[pw,pd,pl]
        disagree=(s['lgbm_pred']!=s['model_1st'])
        
        for i,d_ in enumerate([s['div_w'],s['div_d'],s['div_l']]):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,d_))
                if not disagree:
                    # 正常用B3
                    distrust=0
                    if d>0.03: distrust=0.10
                    if d>0.05: distrust=0.20
                    if d>0.10: distrust=0.40
                    if d>0.20: distrust=0.65
                else:
                    # 模型分歧: 所有方向都惩罚
                    distrust=0
                    if d>0.03: distrust=0.15
                    if d>0.05: distrust=0.30
                    if d>0.10: distrust=0.50
                    if d>0.20: distrust=0.75
                    elif d<0:
                        distrust=abs(d)/0.03*0.2  # 小幅降水也轻微惩罚
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)
run_test(disagree_special,"分歧场特殊处理(全惩罚)")

# ===== 新: 分歧时只看LGBM方向回升 =====
def disagree_only_lgbm_rise(samples):
    """分歧场: 重点惩罚LGBM方向的回升"""
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs=[pw,pd,pl]
        disagree=(s['lgbm_pred']!=s['model_1st'])
        lgbm_idx={'home':0,'draw':1,'away':2}[s['lgbm_pred']]
        
        for i,d_ in enumerate([s['div_w'],s['div_d'],s['div_l']]):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,d_))
                distrust=0
                if d>0.03: distrust=0.10
                if d>0.05: distrust=0.20
                if d>0.10: distrust=0.40
                if d>0.20: distrust=0.65
                # 分歧场且是LGBM方向且回升→加倍
                if disagree and i==lgbm_idx and d>0:
                    distrust=min(0.95,distrust*2)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)
run_test(disagree_only_lgbm_rise,"分歧场→LGBM方向回升×2")

# ===== 新: 分歧时用模型方向做参考 =====
def disagree_model_ref(samples, boost=0.10):
    """分歧时: 分别看两个模型的推荐方向"""
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs=[pw,pd,pl]
        lgbm_pred=s['lgbm_pred']; model_1st=s['model_1st']
        disagree=(lgbm_pred!=model_1st)
        
        for i,d_ in enumerate([s['div_w'],s['div_d'],s['div_l']]):
            if s['cur'][i]>=7.0: distrust=0.85
            else:
                d=max(-1,min(1,d_))
                # baseline B3
                distrust=0
                if d>0.03: distrust=0.10
                if d>0.05: distrust=0.20
                if d>0.10: distrust=0.40
                if d>0.20: distrust=0.65
                
                if disagree and d>0:
                    # 任何方向回升，且模型打架 → 额外惩罚
                    distrust=min(0.95,distrust+boost)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

for b in [0.03,0.05,0.08,0.10,0.15]:
    run_test(lambda s,bb=b: disagree_model_ref(s,bb), f"分歧+全方向+{b:.0%}")

print()
print("--- 针对12场分歧场的精准策略 ---")
# 逐场分析分歧场
disagree_matches=[s for s in samples if s['lgbm_pred']!=s['model_1st']]
print(f"  模型分歧: {len(disagree_matches)}场")
for s in disagree_matches:
    pw,pd,pl=base_prob(s)
    pred=max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]
    ok="✓" if pred==s['actual'] else "✗"
    div_str=f"W:{s['div_w']*100:+.0f}% D:{s['div_d']*100:+.0f}% L:{s['div_l']*100:+.0f}%"
    print(f"  {ok} LGBM:{s['lgbm_pred']:<5}模型:{s['model_1st']:<5}  "
          f"实际:{s['actual']:<5} 基础预测:{pred:<5}  {div_str}")

# 寻找分歧场的模式
print()
print("--- 分歧场规律 ---")
# 分歧场中, 任何方向有回升的场次
for s in disagree_matches:
    divs=[s['div_w'],s['div_d'],s['div_l']]
    rising=[i for i,d in enumerate(divs) if d>0.05]
    dirs=['W','D','L']
    rising_str=','.join(f"{dirs[i]}+{divs[i]*100:.0f}%" for i in rising) if rising else "无"
    print(f"  回升方向: {rising_str}")
