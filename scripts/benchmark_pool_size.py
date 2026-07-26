#!/usr/bin/env python3
"""基准测试：不同历史池大小下相似匹配的比分预测质量"""
import json
import sys
import os
import logging
import sqlite3
from math import sqrt, exp
from datetime import datetime
from collections import defaultdict

logging.basicConfig(level=logging.WARNING, format='%(levelname)s | %(message)s')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')
RESULTS_PATH = os.path.join(PROJECT_DIR, 'docs', 'data', 'results.json')

sys.path.insert(0, SCRIPT_DIR)
from team_similarity import (
    load_team_features, _extract_43d_vector, _resolve_team_name,
    _cosine_sim, load_historical_matches,
)

def find_similar_matches_for_bench(
    home_team, away_team, league,
    team_features, historical_matches, top_k=5,
):
    """与 team_similarity.find_similar_matches 完全一致"""
    ht_vec = team_features.get(home_team, {}).get('_vec')
    at_vec = team_features.get(away_team, {}).get('_vec')
    if not ht_vec or not at_vec:
        return []

    scored = []
    for hm in historical_matches:
        if hm['home_team'] == home_team and hm['away_team'] == away_team:
            continue
        if hm['home_team'] == away_team and hm['away_team'] == home_team:
            continue

        h_vec = team_features.get(hm['home_team'], {}).get('_vec')
        a_vec = team_features.get(hm['away_team'], {}).get('_vec')
        if not h_vec or not a_vec:
            continue

        sim_h = max(_cosine_sim(ht_vec, h_vec), 0.0)
        sim_a = max(_cosine_sim(at_vec, a_vec), 0.0)
        combined = sqrt(sim_h * sim_a)

        if hm['league'] and league and \
           (hm['league'] == league or league in hm['league'] or hm['league'] in league):
            combined = min(combined * 1.2, 1.0)

        if hm['date']:
            try:
                match_date = datetime.strptime(hm['date'], '%Y-%m-%d')
                days_ago = (datetime.now() - match_date).days
                if days_ago >= 0:
                    time_weight = 1.0 if days_ago < 90 else exp(-(days_ago - 90) / 365)
                    combined *= time_weight
            except ValueError:
                pass

        scored.append({
            'home_team': hm['home_team'],
            'away_team': hm['away_team'],
            'league': hm['league'],
            'date': hm['date'],
            'score': hm['score'],
            'similarity': combined,
        })

    scored.sort(key=lambda x: -x['similarity'])
    seen_pairs = set()
    deduped = []
    for s in scored:
        pair = (s['home_team'], s['away_team'])
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(s)
    return deduped[:top_k]


def parse_score(score_str):
    """解析 X-Y 比分，返回 (home_goals, away_goals) 或 None"""
    if not score_str or '-' not in score_str:
        return None
    parts = score_str.split('-')
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0].strip())
        a = int(parts[1].strip())
        return (h, a)
    except ValueError:
        return None


def result_type(h, a):
    """返回 H/D/A"""
    if h > a:
        return 'H'
    elif h < a:
        return 'A'
    return 'D'


def evaluate_pool(results_matches, team_features, pool_size):
    """用给定池大小跑一遍全部得分，返回评估指标"""
    historical = load_historical_matches(DB_PATH, limit=pool_size)
    if not historical:
        return None

    known_teams = set(team_features.keys())
    result_correct = 0
    result_total = 0
    goal_diff_sum = 0
    goal_diff_count = 0
    total_goals_errors = []
    no_actual = 0
    no_match = 0
    matched = 0

    for m in results_matches:
        actual_score = m.get('score', '') or m.get('reference_score', '')
        if not actual_score:
            no_actual += 1
            continue

        actual_parsed = parse_score(actual_score)
        if not actual_parsed:
            no_actual += 1
            continue

        home = m.get('home_team', '')
        away = m.get('away_team', '')
        league = m.get('league', '') or m.get('event', '')

        resolved_home = _resolve_team_name(home, known_teams)
        resolved_away = _resolve_team_name(away, known_teams)

        similar = find_similar_matches_for_bench(
            resolved_home, resolved_away, league,
            team_features, historical, top_k=5,
        )
        if not similar:
            no_match += 1
            continue

        matched += 1
        top1 = similar[0]
        top1_score = parse_score(top1['score'])
        if not top1_score:
            no_match += 1
            continue

        actual_h, actual_a = actual_parsed
        sim_h, sim_a = top1_score

        # 结果预测（H/D/A）是否一致
        if result_type(actual_h, actual_a) == result_type(sim_h, sim_a):
            result_correct += 1
        result_total += 1

        # 总进球差绝对值
        total_goals_errors.append(abs((actual_h + actual_a) - (sim_h + sim_a)))

        # 主客进球差（abs(home_diff) + abs(away_diff)）
        goal_diff_sum += abs(actual_h - sim_h) + abs(actual_a - sim_a)
        goal_diff_count += 1

    avg_goal_err = goal_diff_sum / goal_diff_count if goal_diff_count else 0
    avg_total_goals_err = sum(total_goals_errors) / len(total_goals_errors) if total_goals_errors else 0
    result_acc = result_correct / result_total if result_total else 0

    return {
        'pool': pool_size,
        'matched': matched,
        'no_actual': no_actual,
        'no_match': no_match,
        'result_acc': result_acc,
        'avg_goal_err': avg_goal_err,
        'avg_total_goals_err': avg_total_goals_err,
        'result_correct': result_correct,
        'result_total': result_total,
    }


def main():
    # 加载球队特征（43维，固定）
    print("加载球队特征...")
    team_features = load_team_features(RESULTS_PATH)
    print(f"  球队: {len(team_features)}")

    # 加载有比分的比赛
    with open(RESULTS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    all_matches = data.get('matches', [])
    # 只取有 real score 的
    scored_matches = [m for m in all_matches if m.get('score', '') or m.get('reference_score', '')]
    print(f"  有比分比赛: {len(scored_matches)}/{len(all_matches)}")

    pool_sizes = [10000, 8000, 5000, 3000, 2000, 1000, 500]

    results = []
    for ps in pool_sizes:
        print(f"\n测试池大小: {ps}")
        r = evaluate_pool(scored_matches, team_features, ps)
        if r:
            results.append(r)
            print(f"  匹配: {r['matched']}场, 结果准确率: {r['result_acc']:.1%}, "
                  f"平均进球误差: {r['avg_total_goals_err']:.2f}, "
                  f"主客分开误差: {r['avg_goal_err']:.2f}")

    # 最终对比表
    print("\n" + "=" * 90)
    print(f"{'池大小':>8} | {'匹配':>5} | {'结果准确率':>10} | {'总进球误差':>10} | {'主客分开误差':>10}")
    print("-" * 90)
    for r in sorted(results, key=lambda x: x['result_acc'], reverse=True):
        print(f"{r['pool']:>8} | {r['matched']:>5} | {r['result_acc']:>9.1%} | "
              f"{r['avg_total_goals_err']:>9.2f} | {r['avg_goal_err']:>10.2f}")
    print("=" * 90)


if __name__ == '__main__':
    main()
