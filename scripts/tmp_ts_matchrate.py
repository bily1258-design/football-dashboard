#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 TS 队名匹配率: results.json 的队名在 DB strengths 里能找到多少"""
import json, sqlite3
import numpy as np
from scipy.stats import poisson as _poiss

DB = 'data/football.db'
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

league_avg = (float(np.mean(home_goals)) + float(np.mean(away_goals))) / 2.0
strengths = {}
for team in team_scored:
    n_s = len(team_scored[team]); n_c = len(team_conceded[team])
    avg_s = float(np.mean(team_scored[team])); avg_c = float(np.mean(team_conceded[team]))
    lam_s = min(n_s / (n_s + 10), 0.9); lam_c = min(n_c / (n_c + 10), 0.9)
    attack = 1.0 + lam_s * (avg_s - league_avg) / max(league_avg, 0.1)
    defense = 1.0 + lam_c * (avg_c - league_avg) / max(league_avg, 0.1)
    strengths[team] = {'attack': max(round(attack, 4), 0.2), 'defense': max(round(defense, 4), 0.2)}

print(f'DB球队数: {len(strengths)}, league_avg={league_avg:.4f}')

# 默认概率: 两队都缺失时
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
    return [round(w / total, 4), round(d / total, 4), round(l_ / total, 4)]

# 全缺失默认概率 (attack=defense=1.0)
exp_h = 1.0 * 1.0 * league_avg * 1.08
exp_a = 1.0 * 1.0 * league_avg
default_ts = _poisson_1x2(exp_h, exp_a)
print(f'全缺失默认TS: {default_ts} (exp_h={exp_h:.3f}, exp_a={exp_a:.3f})')

# 匹配 results.json 队名
with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data
print(f'results.json 场次: {len(matches)}')

hits = 0
both_miss = 0
one_miss = 0
exact_default = 0
for m in matches:
    h, a = m.get('home_team', ''), m.get('away_team', '')
    h_hit = h in strengths
    a_hit = a in strengths
    if h_hit and a_hit:
        hits += 1
    elif not h_hit and not a_hit:
        both_miss += 1
    else:
        one_miss += 1
    # 是否恰好等于默认值
    ts = [m.get('ts_win'), m.get('ts_draw'), m.get('ts_loss')]
    if ts[0] is not None and abs(ts[0] - default_ts[0]) < 0.0005 and abs(ts[1] - default_ts[1]) < 0.0005:
        exact_default += 1

n = len(matches)
print(f'两队都命中: {hits} ({hits/n:.1%})')
print(f'单队缺失: {one_miss} ({one_miss/n:.1%})')
print(f'两队都缺: {both_miss} ({both_miss/n:.1%})')
print(f'TS恰好=默认值: {exact_default} ({exact_default/n:.1%})')

# 看看两队都缺的样例
cnt = 0
for m in matches:
    h, a = m.get('home_team', ''), m.get('away_team', '')
    if h not in strengths and a not in strengths:
        print(f'  双缺: {m.get("match_time","")[:10]} {h} vs {a}')
        cnt += 1
        if cnt >= 10:
            break
