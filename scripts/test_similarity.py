#!/usr/bin/env python3
"""
历史相似度比分测试 (numpy优化版)
"""
import json, os, sqlite3, re, sys, time
from math import sqrt
from collections import defaultdict
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

LEAGUE_TIER = {
    '西甲':1.0,'英超':1.0,'意甲':1.0,'德甲':1.0,'法甲':1.0,
    '欧冠杯':1.0,'欧联杯':1.0,'欧协联':1.0,
    '挪超':0.80,'瑞典超':0.80,'芬超':0.80,'丹超':0.80,
    '比甲':0.80,'荷甲':0.80,'葡超':0.80,
}
DEFAULT_TIER = 0.70
SCORE_RE = re.compile(r'(\d+)\s*[-:]\s*(\d+)')

def _get_league_tier(league):
    if not league: return DEFAULT_TIER
    t = LEAGUE_TIER.get(league)
    if t: return t
    for k,v in LEAGUE_TIER.items():
        if k in league or league in k: return v
    return DEFAULT_TIER

def extract_score(s):
    if not s: return None
    m = SCORE_RE.search(s)
    return (int(m.group(1)), int(m.group(2))) if m else None

def get_outcome(h, a):
    return 'H' if h>a else ('D' if h==a else 'A')

def load_features_and_matches(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT home_team, away_team, league, date,
               reference_score, actual_outcome,
               home_lambda, away_lambda,
               home_avg_goals, away_avg_goals,
               home_avg_conceded, away_avg_conceded
        FROM poisson_predictions
        WHERE home_team IS NOT NULL AND home_team != ''
          AND away_team IS NOT NULL AND away_team != ''
    """).fetchall()
    conn.close()

    # Build team features
    team_data = defaultdict(lambda: {'l':[],'gs':[],'gc':[],'t':[]})
    for r in rows:
        league = r[2] or ''
        tier = _get_league_tier(league)
        for idx, team in [(0, r[0]), (1, r[1])]:
            td = team_data[team]
            td['l'].append(r[6+idx] or 0)
            td['gs'].append(r[8+idx] or 0)
            td['gc'].append(r[10+idx] or 0)
            td['t'].append(tier)

    features = {}
    for team, d in team_data.items():
        n = len(d['l'])
        avg_l = sum(d['l'])/n
        features[team] = [avg_l, sum(d['gs'])/n, sum(d['gc'])/n,
                          avg_l - avg_l, sum(d['t'])/n]

    # StandardScaler
    cols = 5
    vals = np.array(list(features.values()))
    means = vals.mean(axis=0)
    stds = vals.std(axis=0)
    stds[stds < 1e-10] = 1.0
    normed = (vals - means) / stds

    team_names = list(features.keys())
    team_vecs = {t: normed[i] for i, t in enumerate(team_names)}

    # Load historical matches with scores
    matches = []
    for r in rows:
        score_str = r[4] or ''
        if not score_str and r[5]:
            m = SCORE_RE.search(r[5])
            if m: score_str = f'{m.group(1)}-{m.group(2)}'
        s = extract_score(score_str)
        if s is None: continue
        ht, at = r[0], r[1]
        if ht not in team_vecs or at not in team_vecs: continue
        matches.append({
            'home_team': ht, 'away_team': at, 'league': r[2] or '',
            'date': r[3] or '', 'score': score_str,
            'home_score': s[0], 'away_score': s[1],
            'outcome': get_outcome(s[0], s[1]),
            'h_vec': team_vecs[ht], 'a_vec': team_vecs[at],
        })

    return team_vecs, matches


def run_test_segment(name, target_matches, pool_matches):
    """Run similarity test using numpy for fast cosine similarity"""
    n = len(target_matches)
    if n == 0:
        return None

    # Pre-extract vectors for pool
    pool_h_vecs = np.array([m['h_vec'] for m in pool_matches])
    pool_a_vecs = np.array([m['a_vec'] for m in pool_matches])
    pool_scores_h = np.array([m['home_score'] for m in pool_matches])
    pool_scores_a = np.array([m['away_score'] for m in pool_matches])
    pool_outcomes = np.array([m['outcome'] for m in pool_matches], dtype='U1')
    pool_leagues = [m['league'] for m in pool_matches]

    score_hit_1 = 0
    score_hit_3 = 0
    outcome_hit_1 = 0
    outcome_hit_3 = 0
    no_similar = 0

    # Precompute dot products for the pool (similarity between all pool teams)
    # pool_h_dots[i][j] = sim(home_vector_of_pool[i], home_vector_of_pool[j])
    pool_h_dots = pool_h_vecs @ pool_h_vecs.T  # N×N
    pool_a_dots = pool_a_vecs @ pool_a_vecs.T  # N×N

    ts = time.time()
    for i, tm in enumerate(target_matches):
        # Find index of this match in pool (exclude self)
        # For simplicity, we find the best 3 that aren't the same teams
        h_vec = tm['h_vec']
        a_vec = tm['a_vec']

        # Cosine similarity of target vs each pool match
        sim_h = np.clip(pool_h_vecs @ h_vec, 0, 1)  # (N,)
        sim_a = np.clip(pool_a_vecs @ a_vec, 0, 1)  # (N,)
        combined = np.sqrt(sim_h * sim_a)  # (N,)

        # Exclude same team pairing
        same_teams = np.array([
            pool_matches[j]['home_team'] == tm['home_team'] and
            pool_matches[j]['away_team'] == tm['away_team']
            for j in range(len(pool_matches))
        ], dtype=bool)

        # League bonus
        league_bonus = np.array([
            1.15 if (pool_leagues[j] and tm['league'] and
                     (pool_leagues[j] == tm['league'] or
                      tm['league'] in pool_leagues[j] or
                      pool_leagues[j] in tm['league']))
            else 1.0
            for j in range(len(pool_matches))
        ])
        combined = np.minimum(combined * league_bonus, 1.0)
        combined[same_teams] = -1  # Exclude self

        # Top 3 indices
        top3_idx = np.argsort(-combined)[:3]
        top3_sims = combined[top3_idx]

        if top3_sims[0] < 0:
            no_similar += 1
            continue

        target_h, target_a = tm['home_score'], tm['away_score']
        target_out = tm['outcome']

        # Top-1 check
        t1 = top3_idx[0]
        if pool_scores_h[t1] == target_h and pool_scores_a[t1] == target_a:
            score_hit_1 += 1
        if pool_outcomes[t1] == target_out:
            outcome_hit_1 += 1

        # Top-3 check
        for idx in top3_idx:
            if top3_sims[idx == top3_idx][0] < 0:
                break
            if pool_scores_h[idx] == target_h and pool_scores_a[idx] == target_a:
                score_hit_3 += 1
                break
        for idx in top3_idx:
            if top3_sims[idx == top3_idx][0] < 0:
                break
            if pool_outcomes[idx] == target_out:
                outcome_hit_3 += 1
                break

        if (i + 1) % 1000 == 0:
            el = time.time() - ts
            print(f"    {name}: {i+1}/{n} ({el:.0f}s)")

    elapsed = time.time() - ts
    tested = n - no_similar
    print(f"\n  ===== {name} (n={n}) =====")
    print(f"  耗时: {elapsed:.0f}s")
    print(f"  有效测试: {tested}")
    print(f"  无相似: {no_similar}")
    print(f"  --- 比分 ---")
    print(f"  Top-1 比分: {score_hit_1}/{tested} = {score_hit_1/tested*100:.2f}%" if tested else "  N/A")
    print(f"  Top-3 比分: {score_hit_3}/{tested} = {score_hit_3/tested*100:.2f}%" if tested else "  N/A")
    print(f"  --- 赛果 ---")
    print(f"  Top-1 赛果: {outcome_hit_1}/{tested} = {outcome_hit_1/tested*100:.2f}%" if tested else "  N/A")
    print(f"  Top-3 赛果: {outcome_hit_3}/{tested} = {outcome_hit_3/tested*100:.2f}%" if tested else "  N/A")

    return {
        'name': name, 'n': n, 'tested': tested,
        's1': score_hit_1/tested*100 if tested else 0,
        's3': score_hit_3/tested*100 if tested else 0,
        'o1': outcome_hit_1/tested*100 if tested else 0,
        'o3': outcome_hit_3/tested*100 if tested else 0,
    }


def main():
    print("=" * 60)
    print("历史相似度比分测试 (v2 numpy版)")
    print("=" * 60)

    print("\n[1/2] 加载特征与对局...")
    features, all_matches = load_features_and_matches(DB_PATH)
    print(f"  球队: {len(features)}, 有比分对局: {len(all_matches)}")

    print("\n[2/2] 分段测试...")
    print("=" * 60)

    # Segments: (name, target slice, pool = entire history)
    segments = [
        ("全量12044场", all_matches),
        ("最近500场",  all_matches[:500]),
        ("最近300场",  all_matches[:300]),
        ("最近200场",  all_matches[:200]),
        ("最近100场",  all_matches[:100]),
    ]

    results = []
    for name, targets in segments:
        r = run_test_segment(name, targets, all_matches)
        if r: results.append(r)

    print("\n" + "=" * 60)
    print("最终对比汇总")
    print("=" * 60)
    hdr = f"{'分段':<18} {'场次':<8} {'Top-1比分':<12} {'Top-3比分':<12} {'Top-1赛果':<12} {'Top-3赛果':<12}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['name']:<18} {r['tested']:<8} "
              f"{r['s1']:<10.2f}%  {r['s3']:<10.2f}%  "
              f"{r['o1']:<10.2f}%  {r['o3']:<10.2f}%")


if __name__ == '__main__':
    main()
