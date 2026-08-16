#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化TS前视偏差: 全量team_model vs 截至比赛日team_model"""
import json, sqlite3, os, math
import numpy as np
from scipy.stats import poisson as _poiss

DB = 'data/football.db'

def _poisson_1x2(lh, la, max_g=10):
    if lh <= 0 or la <= 0:
        return None
    w = d = l_ = 0.0
    for hg in range(max_g + 1):
        ph = _poiss.pmf(hg, lh)
        for ag in range(max_g + 1):
            pa = _poiss.pmf(ag, la)
            prob = ph * pa
            if hg > ag:
                w += prob
            elif hg == ag:
                d += prob
            else:
                l_ += prob
    total = w + d + l_
    return [w / total, d / total, l_ / total]

def build_model(cutoff_date=None):
    """cutoff_date: 只使用 match_time < cutoff_date 的比赛"""
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT home_team, away_team, reference_score, date
        FROM poisson_predictions
        WHERE reference_score IS NOT NULL AND reference_score != '' AND date IS NOT NULL
    """).fetchall()
    conn.close()
    team_scored, team_conceded = {}, {}
    home_goals, away_goals = [], []
    for r in rows:
        mt = r[3]
        if cutoff_date and mt and mt >= cutoff_date:
            continue
        parts = r[2].split('-')
        if len(parts) != 2:
            continue
        try:
            hg, ag = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        home, away = r[0], r[1]
        home_goals.append(hg); away_goals.append(ag)
        team_scored.setdefault(home, []).append(hg)
        team_conceded.setdefault(home, []).append(ag)
        team_scored.setdefault(away, []).append(ag)
        team_conceded.setdefault(away, []).append(hg)
    if not team_scored:
        return None
    league_avg = (float(np.mean(home_goals)) + float(np.mean(away_goals))) / 2.0
    strengths = {}
    for team in team_scored:
        n_s = len(team_scored[team]); n_c = len(team_conceded[team])
        avg_s = float(np.mean(team_scored[team])); avg_c = float(np.mean(team_conceded[team]))
        lam_s = min(n_s / (n_s + 10), 0.9); lam_c = min(n_c / (n_c + 10), 0.9)
        attack = 1.0 + lam_s * (avg_s - league_avg) / max(league_avg, 0.1)
        defense = 1.0 + lam_c * (avg_c - league_avg) / max(league_avg, 0.1)
        strengths[team] = {'attack': max(round(attack, 4), 0.2), 'defense': max(round(defense, 4), 0.2)}
    return {'strengths': strengths, 'league_avg': round(league_avg, 4)}

def ts_pred(model, home, away, home_adv=1.08):
    if not model:
        return None
    s = model['strengths']
    ha = s.get(home, {'attack': 1.0, 'defense': 1.0})
    aa = s.get(away, {'attack': 1.0, 'defense': 1.0})
    exp_h = ha['attack'] * aa['defense'] * model['league_avg'] * home_adv
    exp_a = aa['attack'] * ha['defense'] * model['league_avg']
    return _poisson_1x2(exp_h, exp_a)

# 全量模型 (现状 = 前视)
full_model = build_model(None)
print(f'全量模型: {len(full_model["strengths"])}队 league_avg={full_model["league_avg"]}')

# 加载 results.json
with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data

# 抽样: 6月和8月各取一些场次, 对比 full vs cutoff 模型
import random
random.seed(42)
diffs = []
for m in random.sample([x for x in matches if x.get('match_time') and x.get('ts_win') is not None], 200):
    mt = m['match_time'][:10]
    cutoff = mt
    # 截至比赛日(不含当日)的模型
    cutoff_model = build_model(cutoff + ' 00:00:00') if cutoff else None
    if not cutoff_model:
        continue
    full_ts = ts_pred(full_model, m.get('home_team',''), m.get('away_team',''))
    cut_ts = ts_pred(cutoff_model, m.get('home_team',''), m.get('away_team',''))
    if full_ts is None or cut_ts is None:
        continue
    d_w = abs(full_ts[0] - cut_ts[0])
    d_d = abs(full_ts[1] - cut_ts[1])
    d_l = abs(full_ts[2] - cut_ts[2])
    maxd = max(d_w, d_d, d_l)
    # 方向翻转? 全量模型与截至模型的主胜方向是否一致
    dir_full = max(range(3), key=lambda i: full_ts[i])
    dir_cut = max(range(3), key=lambda i: cut_ts[i])
    flips = (dir_full != dir_cut)
    diffs.append((mt, m.get('home_team',''), m.get('away_team',''), full_ts, cut_ts, maxd, flips))

print(f'抽样: {len(diffs)} 场')
flips = [d for d in diffs if d[6]]
print(f'方向翻转: {len(flips)} 场 ({len(flips)/len(diffs):.1%})')
if flips:
    for d in flips[:8]:
        print(f'  {d[0]} {d[1]} vs {d[2]} 全量={d[3]} 截至={d[4]}')
# 平均最大偏差
avg_maxd = np.mean([d[5] for d in diffs])
p90 = np.percentile([d[5] for d in diffs], 90)
print(f'平均最大单点偏差: {avg_maxd:.4f}, P90: {p90:.4f}')

# 按月看方向翻转率
from collections import defaultdict
by_month = defaultdict(list)
for d in diffs:
    by_month[d[0][:7]].append(d)
for mo in sorted(by_month):
    mm = by_month[mo]
    f = sum(1 for x in mm if x[6])
    print(f'{mo}: {len(mm)}场 翻转{len([1 for x in mm if x[6]])} ({f/len(mm):.1%}) 平均偏差{np.mean([x[5] for x in mm]):.4f}')
