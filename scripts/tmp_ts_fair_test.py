#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用截至比赛日的无前视 TS 重测主主客+TS>=40% 策略 (W32/W33)"""
import json, sqlite3
import numpy as np
from scipy.stats import poisson as _poiss
from datetime import datetime, timedelta

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

# 一次性载入 DB 全部已完赛记录
conn = sqlite3.connect(DB)
rows = conn.execute("""
    SELECT home_team, away_team, reference_score, date
    FROM poisson_predictions
    WHERE reference_score IS NOT NULL AND reference_score != '' AND date IS NOT NULL
""").fetchall()
conn.close()
parsed = []
for r in rows:
    parts = r[2].split('-')
    if len(parts) != 2:
        continue
    try:
        hg, ag = int(parts[0]), int(parts[1])
    except ValueError:
        continue
    parsed.append((r[0], r[1], hg, ag, r[3]))

def build_model_for_date(cutoff_date):
    """截至 cutoff_date (含) 的模型"""
    team_scored, team_conceded = {}, {}
    home_goals, away_goals = [], []
    for home, away, hg, ag, d in parsed:
        if d and d > cutoff_date:
            continue
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

# 载入 results.json
with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data

def week_of(dt):
    # 周一为一周开始
    monday = dt - timedelta(days=dt.weekday())
    return monday

def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')

def is_home_win(score):
    try:
        h, a = score.split('-')
        return int(h) > int(a)
    except Exception:
        return None

# 策略: 主主客 = model主胜 + lgbm主胜 + TS客胜(ts_loss最大), 且 ts_loss>=0.40
# 需要 score 的场次 (已完赛)
# W32 = 8/3-8/9, W33 = 8/10-8/16
print("=" * 80)
print("策略重测: 主主客 (model主+lgbm主+TS客) + TS>=40%")
print("对比: results.json 存的原TS (有前视) vs 截至比赛日重建TS (无前视)")
print("=" * 80)

def select(sub, use_fair=False):
    """返回选中场次列表; use_fair=True 时用无前视TS重算"""
    out = []
    for m in sub:
        if not (m.get('model_win') is not None and m.get('lgbm_win') is not None):
            continue
        if direction(m['model_win'], m.get('model_draw') or 0, m.get('model_loss') or 0) != '主':
            continue
        if direction(m['lgbm_win'], m.get('lgbm_draw') or 0, m.get('lgbm_loss') or 0) != '主':
            continue
        if not use_fair:
            if m.get('ts_loss') is not None and direction(m.get('ts_win') or 0, m.get('ts_draw') or 0, m['ts_loss']) == '客' and m['ts_loss'] >= 0.40:
                out.append(m)
        else:
            dt = m['match_time'][:10]
            mdl = build_model_for_date(dt)
            if mdl is None:
                continue
            ts = ts_pred(mdl, m.get('home_team',''), m.get('away_team',''))
            if ts is None:
                continue
            if direction(ts[0], ts[1], ts[2]) == '客' and ts[2] >= 0.40:
                out.append((m, ts))
    return out

for label, lo, hi in [("W32", '2026-08-03', '2026-08-09'), ("W33", '2026-08-10', '2026-08-16')]:
    sub = [m for m in matches if m.get('match_time') and lo <= m['match_time'][:10] <= hi and m.get('score')]
    n = len(sub)
    orig = select(sub, False)
    fair = select(sub, True)
    def roi(items):
        if not items:
            return 0.0, 0, 0.0
        stake = 0.0
        ret = 0.0
        wins = 0
        for it in items:
            m = it[0] if isinstance(it, tuple) else it
            odds = float(m.get('odds_win') or 0)
            stake += 1.0
            if is_home_win(m.get('score')):
                ret += odds
                wins += 1
        return wins / len(items), (ret - stake) / stake, len(items)
    ro, wc, nn = roi(orig)
    rf, wf, nf = roi(fair)
    print(f"\n{label} ({lo}~{hi}, 已完赛 {n} 场):")
    print(f"  原始TS (前视): {nn} 场 胜率 {ro:.1%} ROI {wc:+.1%}")
    print(f"  无前视TS:      {nf} 场 胜率 {rf:.1%} ROI {wf:+.1%}")
    if fair:
        flips = 0
        for m, ts in fair:
            orig_dir = direction(m.get('ts_win') or 0, m.get('ts_draw') or 0, m.get('ts_loss') or 0)
            new_dir = direction(ts[0], ts[1], ts[2])
            if orig_dir != new_dir:
                flips += 1
        print(f"  方向翻转: {flips}/{nf}")

# 也测全 8 月
print("\n" + "=" * 80)
print("8月全月 (8/1-8/16)")
print("=" * 80)
sub = [m for m in matches if m.get('match_time') and '2026-08-01' <= m['match_time'][:10] <= '2026-08-16' and m.get('score')]
orig = select(sub, False)
fair = select(sub, True)
def roi2(items):
    stake = 0.0; ret = 0.0; wins = 0
    for it in items:
        m = it[0] if isinstance(it, tuple) else it
        odds = float(m.get('odds_win') or 0)
        stake += 1.0
        if is_home_win(m.get('score')):
            ret += odds; wins += 1
    if not items:
        return 0, 0, 0
    return wins/len(items), (ret-stake)/stake, len(items)
ro, wc, nn = roi2(orig)
rf, wf, nf = roi2(fair)
print(f"原始TS: {nn} 场 胜率 {ro:.1%} ROI {wc:+.1%}")
print(f"无前视: {nf} 场 胜率 {rf:.1%} ROI {wf:+.1%}")
