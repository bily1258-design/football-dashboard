#!/usr/bin/env python3
import json, sys, math
sys.path.insert(0, 'scripts')

with open('data/matches_20260713.json') as f:
    data = json.load(f)

def poisson_prob(lam_h, lam_a):
    def pp(lam, k):
        return (math.exp(-lam) * lam**k) / math.factorial(k)
    pois_w = sum(pp(lam_h,i)*pp(lam_a,j) for i in range(6) for j in range(6) if i>j)
    pois_d = sum(pp(lam_h,i)*pp(lam_a,i) for i in range(6))
    pois_l = sum(pp(lam_h,i)*pp(lam_a,j) for i in range(6) for j in range(6) if i<j)
    return pois_w, pois_d, pois_l

print(f"{'主队':10s} {'客队':10s} {'平赔':>5s} {'隐含W':>6s} {'λ主':>5s} {'λ客':>5s} {'泊松W':>6s} {'泊松D':>6s} {'泊松L':>6s} {'差值W':>6s}")
print("-"*80)
for m in data['matches'][:15]:
    o = m.get('pinnacle', m.get('odds_zqdc', {}))
    w, d, l = float(o.get('win',1)), float(o.get('draw',1)), float(o.get('loss',1))
    inv_w, inv_d, inv_l = 1/w, 1/d, 1/l
    tot = inv_w+inv_d+inv_l
    imp_w, imp_d, imp_l = inv_w/tot, inv_d/tot, inv_l/tot
    
    p_home = imp_w/(imp_w+imp_l)
    p_away = imp_l/(imp_w+imp_l)
    lam_h = (2.5 * p_home/(p_home+p_away)) + 0.15 + (p_home-p_away)*0.6
    lam_a = (2.5 * p_away/(p_home+p_away)) - (p_home-p_away)*0.6
    lam_h = max(0.3, min(4.0, lam_h))
    lam_a = max(0.3, min(4.0, lam_a))
    
    pw, pd, pl = poisson_prob(lam_h, lam_a)
    diff = pw - imp_w
    
    print(f"{m['home_team'][:10]:10s} {m['away_team'][:10]:10s} {d:>5.2f} {imp_w:>6.1%} {lam_h:>5.2f} {lam_a:>5.2f} {pw:>6.1%} {pd:>6.1%} {pl:>6.1%} {diff:>+6.1%}")
