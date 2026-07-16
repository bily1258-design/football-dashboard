"""确定最优参数: B3分级 + 模型分歧惩罚"""
import json

d = json.load(open('docs/data/results.json'))

def parse_score(s):
    parts=s.split('-')
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
        'div_w':div_w,'div_d':div_d,'div_l':div_l,'cur':c,
        'model_1st':model_1st,'lgbm_pred':lgbm_pred})

print(f"=== 回测样本: {len(samples)} 场 ===\n")

S=0.01; H=0.01

def base_prob(s):
    sw=s['imp_w']*(1-S)+S/3+H; sd=s['imp_d']*(1-S)+S/3-H*0.3; sl=s['imp_l']*(1-S)+S/3-H*0.3
    st=sw+sd+sl; return sw/st, sd/st, sl/st

def run_test(fn,desc):
    h,t=fn(samples)
    flag=" ★" if h/t*100>=55.6 else ""
    print(f"  {desc:<42}| {h:>2}/{t:<3}| {h/t*100:>6.2f}%{flag}")
    return h,t

def baseline(s):
    hits=0
    for si in s:
        pw,pd,pl=base_prob(si)
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==si['actual']: hits+=1
    return hits,len(s)

print(f"{'方法':<44}| {'命中':>5}| {'命中率':>7}")
print("-"*66)
b,_=run_test(baseline,"基准(纯平滑)")

# B3 + 分歧惩罚 - 精细调参
def b3_disagree(samples, boost):
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
print("═══ B3 + 分歧惩罚精细调参 ═══")
for b in [0.18,0.20,0.22,0.25,0.27,0.28,0.30,0.32,0.35,0.40]:
    run_test(lambda s,bb=b: b3_disagree(s,bb), f"B3+分歧+{bb:.0%}")

# 对分歧场: 所有方向都惩罚(不仅仅是回升方向)
def b3_disagree_all(samples, boost):
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
                if disagree:  # 分歧场全部方向加罚
                    distrust=min(0.95, distrust+boost)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print()
print("═══ B3 + 分歧场全方向惩罚 ═══")
for b in [0.03,0.05,0.08,0.10,0.12,0.15]:
    run_test(lambda s,bb=b: b3_disagree_all(s,bb), f"B3+全方向+{bb:.0%}")

# 对分歧场: 只看LGBM方向(方向一致无影响)
def b3_disagree_lgbm_only(samples, multiplier):
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
                if disagree and i==lgbm_idx and d>0:
                    distrust=min(0.95, distrust*multiplier)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

print()
print("═══ B3 + 分歧场LGBM方向加倍 ═══")
for m in [1.5,2.0,2.5,3.0,4.0]:
    run_test(lambda s,mm=m: b3_disagree_lgbm_only(s,mm), f"LGBM方向×{mm:.1f}")

# 完整 B3 分级 + LGBM方向回升率×分歧惩罚 - 引入"分歧方向"概念
def b3_smart_disagree(samples):
    """
    分歧场: 惩罚回升方向和LGBM方向不一致的方向
    即: LGBM说home但D/L回升 → D/L被惩罚 (LGBM没选的方向回升=市场不认同LGBM)
    """
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
                if disagree and d>0 and i!=lgbm_idx:
                    # 分歧场: LGBM没选的方向在回升 → 狠罚
                    distrust=min(0.95, distrust*3)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)
run_test(b3_smart_disagree,"B3+非LGBM方向回升×3")

# 新想法: 分歧场中两个模型的不同方向都得不到足够的数据支持
def b3_disagree_strong_punish(samples, boost):
    """
    分歧场: 
    - LGBM方向回升 → 最大惩罚
    - 非LGBM方向回升 → 惩罚 (市场在反对LGBM) 
    """
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
                if disagree and d>0:
                    if i==lgbm_idx:
                        distrust=min(0.95, distrust+boost*2)  # LGBM方向再加倍
                    else:
                        distrust=min(0.95, distrust+boost)    # 非LGBM方向
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw,pd,pl=probs[0]/t,probs[1]/t,probs[2]/t
        if max([('home',pw),('draw',pd),('away',pl)],key=lambda x:x[1])[0]==s['actual']: hits+=1
    return hits,len(samples)

for b in [0.08,0.10,0.12,0.15,0.20]:
    run_test(lambda s,bb=b: b3_disagree_strong_punish(s,bb), f"B3+非LGBM+{bb:.0%}")

# 最终检查: B3+分歧+25% 在分歧场中具体怎么改的
print()
print("═══ B3+分歧+25% 对分歧场的影响 ═══")
def b3_disagree_detailed(samples):
    hits=0
    for s in samples:
        pw,pd,pl=base_prob(s); probs_o=[pw,pd,pl]; probs=[pw,pd,pl]
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
                if disagree and d>0:
                    distrust=min(0.95,distrust+0.25)
            probs[i]*=max(0.05,1-distrust)
        t=sum(probs); pw_new,pd_new,pl_new=probs[0]/t,probs[1]/t,probs[2]/t
        pred_new=max([('home',pw_new),('draw',pd_new),('away',pl_new)],key=lambda x:x[1])[0]
        
        if disagree:
            pw_old,pd_old,pl_old=probs_o[0],probs_o[1],probs_o[2]
            pred_old=max([('home',pw_old),('draw',pd_old),('away',pl_old)],key=lambda x:x[1])[0]
            ok_old="✓" if pred_old==s['actual'] else "✗"
            ok_new="✓" if pred_new==s['actual'] else "✗"
            change="→" if pred_old!=pred_new else "="
            div_str=f"W:{s['div_w']*100:+.0f} D:{s['div_d']*100:+.0f} L:{s['div_l']*100:+.0f}"
            if pred_new==s['actual']:
                print(f"  ✅ {pred_old}→{pred_new}  {div_str}  [{s['lgbm_pred']}/{s['model_1st']}]")
            else:
                print(f"  ❌ {pred_old}→{pred_new}  {div_str}  [{s['lgbm_pred']}/{s['model_1st']}]  实际:{s['actual']}")
        
        if pred_new==s['actual']: hits+=1
    return hits,len(samples)

b3_disagree_detailed(samples)
