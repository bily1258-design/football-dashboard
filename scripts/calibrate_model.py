#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_model.py — 基于1.4万+历史数据校准泊松预测模型

三步校准：
1. 联赛分层参数：从实际数据反推每个联赛的BASE_TOTAL_GOALS/HOME_ADV/SKILL_FACTOR
2. Isotonic regression：校准概率输出，消除模型偏差
3. EV计算：用赔率×校准概率-1计算真实EV方向

输出：calibration_params.json → 供 predict_from_odds.py 使用

用法:
  python scripts/calibrate_model.py --db data/football.db
  python scripts/calibrate_model.py --db data/football.db --dry-run
"""

import json
import math
import os
import re
import sqlite3
import argparse
from collections import defaultdict


def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_match_probs(lam_h, lam_a, max_goals=10):
    pw = pd = pl = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(lam_h, h) * poisson_pmf(lam_a, a)
            if h > a: pw += p
            elif h == a: pd += p
            else: pl += p
    return pw, pd, pl


def estimate_lambdas(imp_w, imp_d, imp_l, base_goals=2.4, home_adv=0.15, skill_factor=0.6):
    diff = imp_w - imp_l
    ratio_h = (1 + diff * skill_factor) / 2
    lam_h = max(0.3, min(4.0, base_goals * ratio_h * (1 + home_adv)))
    lam_a = max(0.3, min(4.0, base_goals * (1 - ratio_h)))
    return lam_h, lam_a


def implied_from_odds(odds_w, odds_d, odds_l):
    if not odds_w or not odds_d or not odds_l or odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None
    raw_w = 1.0 / odds_w
    raw_d = 1.0 / odds_d
    raw_l = 1.0 / odds_l
    total = raw_w + raw_d + raw_l
    return raw_w / total, raw_d / total, raw_l / total


def load_data(db_path):
    """加载已开奖记录，返回league→records映射"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT league, home_team, away_team, 
               odds_win, odds_draw, odds_loss,
               implied_prob_w, implied_prob_d, implied_prob_l,
               actual_outcome, prediction, prediction_prob,
               final_win, final_draw, final_loss,
               poisson_win, poisson_draw, poisson_loss,
               pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
               avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
               source, best_direction_cn
        FROM poisson_predictions
        WHERE actual_outcome IS NOT NULL AND actual_outcome != ''
    """)
    records = [dict(r) for r in c.fetchall()]
    conn.close()

    by_league = defaultdict(list)
    for r in records:
        by_league[r['league']].append(r)
    return by_league, records


def compute_league_params(records):
    """从实际赛果反推联赛参数"""
    if len(records) < 30:
        return None

    total = len(records)
    home_wins = sum(1 for r in records if r['actual_outcome'].startswith('主胜'))
    draws = sum(1 for r in records if r['actual_outcome'].startswith('平局'))
    away_wins = sum(1 for r in records if r['actual_outcome'].startswith('客胜'))

    actual_hw_rate = home_wins / total
    actual_d_rate = draws / total
    actual_aw_rate = away_wins / total

    # 平均总进球
    total_goals = []
    for r in records:
        m = re.search(r'(\d+)-(\d+)', r['actual_outcome'])
        if m:
            total_goals.append(int(m.group(1)) + int(m.group(2)))
    avg_total_goals = sum(total_goals) / len(total_goals) if total_goals else 2.4

    # 主场优势 = 实际主胜率 - 客胜率
    home_adv = actual_hw_rate - actual_aw_rate

    # 用implied概率做网格搜索找最优skill_factor和poisson_weight
    best_params = None
    best_hit = -1

    for skill_factor in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        for poisson_weight in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
            implied_weight = 1.0 - poisson_weight
            hits = 0
            for r in records:
                p_w = r.get('implied_prob_w', 0) or 0
                p_d = r.get('implied_prob_d', 0) or 0
                p_l = r.get('implied_prob_l', 0) or 0
                if not p_w:
                    continue

                lam_h, lam_a = estimate_lambdas(p_w, p_d, p_l,
                                                avg_total_goals, home_adv, skill_factor)
                pp_w, pp_d, pp_l = poisson_match_probs(lam_h, lam_a)

                f_w = poisson_weight * pp_w + implied_weight * p_w
                f_d = poisson_weight * pp_d + implied_weight * p_d
                f_l = poisson_weight * pp_l + implied_weight * p_l

                pred = '主胜' if f_w >= f_d and f_w >= f_l else ('客胜' if f_l >= f_d else '平局')
                actual = r['actual_outcome'][:2]
                if pred == actual:
                    hits += 1

            if hits > best_hit:
                best_hit = hits
                best_params = {
                    'base_total_goals': round(avg_total_goals, 2),
                    'home_adv': round(home_adv, 3),
                    'skill_factor': skill_factor,
                    'poisson_weight': poisson_weight,
                    'implied_weight': round(implied_weight, 2),
                    'hit_rate': round(hits / len(records), 4) if records else 0,
                    'sample_size': total,
                }

    return best_params


def isotonic_regression(pairs):
    """
    简单isotonic regression: pairs = [(predicted_prob, actual_hit: 0/1), ...]
    返回校准映射: sorted breakpoints [(pred, calibrated), ...]
    """
    if not pairs:
        return []

    # 按predicted prob排序，分桶计算
    pairs.sort(key=lambda x: x[0])
    n_bins = min(20, len(pairs) // 50)
    if n_bins < 3:
        return []

    bin_size = len(pairs) // n_bins
    calibration = []

    for i in range(n_bins):
        start = i * bin_size
        end = start + bin_size if i < n_bins - 1 else len(pairs)
        bucket = pairs[start:end]
        avg_pred = sum(p for p, _ in bucket) / len(bucket)
        avg_actual = sum(a for _, a in bucket) / len(bucket)
        calibration.append((round(avg_pred, 3), round(avg_actual, 3)))

    # 确保单调递增（PAVA）
    for i in range(1, len(calibration)):
        if calibration[i][1] < calibration[i-1][1]:
            # 合并
            avg_p = (calibration[i][0] + calibration[i-1][0]) / 2
            avg_a = (calibration[i][1] + calibration[i-1][1]) / 2
            calibration[i] = (round(avg_p, 3), round(avg_a, 3))
            calibration[i-1] = calibration[i]

    return calibration


def compute_ev_direction(r, calib_map=None):
    """计算真实EV方向：赔率×校准概率-1"""
    odds_w = r.get('odds_win', 0) or 0
    odds_d = r.get('odds_draw', 0) or 0
    odds_l = r.get('odds_loss', 0) or 0

    f_w = r.get('final_win', 0) or 0
    f_d = r.get('final_draw', 0) or 0
    f_l = r.get('final_loss', 0) or 0

    if not odds_w or not f_w:
        return r.get('best_direction_cn', '') or r.get('prediction', '')

    # 校准概率
    if calib_map:
        f_w = calibrate_prob(f_w, calib_map)
        f_d = calibrate_prob(f_d, calib_map)
        f_l = calibrate_prob(f_l, calib_map)
        s = f_w + f_d + f_l
        if s > 0:
            f_w, f_d, f_l = f_w/s, f_d/s, f_l/s

    # EV = probability × odds - 1
    ev_w = f_w * odds_w - 1
    ev_d = f_d * odds_d - 1
    ev_l = f_l * odds_l - 1

    if ev_w >= ev_d and ev_w >= ev_l:
        return '主胜'
    elif ev_l >= ev_d:
        return '客胜'
    else:
        return '平局'


def calibrate_prob(prob, calib_map):
    """用校准映射插值校准概率"""
    if not calib_map:
        return prob
    # 找到prob所在的区间
    for i in range(len(calib_map) - 1):
        if calib_map[i][0] <= prob <= calib_map[i+1][0]:
            # 线性插值
            t = (prob - calib_map[i][0]) / (calib_map[i+1][0] - calib_map[i][0]) if calib_map[i+1][0] != calib_map[i][0] else 0
            return calib_map[i][1] + t * (calib_map[i+1][1] - calib_map[i][1])
    # 超出范围用边界
    if prob < calib_map[0][0]:
        return calib_map[0][1]
    return calib_map[-1][1]


def main():
    parser = argparse.ArgumentParser(description="校准泊松预测模型")
    parser.add_argument('--db', required=True, help='数据库路径')
    parser.add_argument('--dry-run', action='store_true', help='只分析不保存')
    parser.add_argument('--update-db', action='store_true', help='更新DB中记录的best_direction_cn和概率')
    args = parser.parse_args()

    print('📊 加载数据...')
    by_league, all_records = load_data(args.db)
    print(f'  总计 {len(all_records)} 条已开奖记录, {len(by_league)} 个联赛')

    # Step 1: 联赛分层参数
    print('\n🔧 Step 1: 联赛分层参数优化')
    league_params = {}
    global_params = {
        'base_total_goals': 2.4,
        'home_adv': 0.15,
        'skill_factor': 0.6,
        'poisson_weight': 0.5,
        'implied_weight': 0.5,
    }

    for league in sorted(by_league.keys()):
        records = by_league[league]
        if len(records) >= 30:
            params = compute_league_params(records)
            if params:
                league_params[league] = params
                old_hit = sum(1 for r in records if r['prediction'] == r['actual_outcome'][:2]) / len(records)
                delta = params['hit_rate'] - old_hit
                sign = '+' if delta >= 0 else ''
                print(f'  {league} ({len(records)}场): '
                      f'goals={params["base_total_goals"]}, '
                      f'hadv={params["home_adv"]}, '
                      f'skill={params["skill_factor"]}, '
                      f'pw={params["poisson_weight"]}  '
                      f'命中率 {old_hit:.1%} → {params["hit_rate"]:.1%} ({sign}{delta:.1%})')

    # Step 2: Isotonic regression（全局 + 按联赛）
    print('\n📈 Step 2: 概率校准 (Isotonic Regression)')
    # 全局校准
    all_pairs = []
    for r in all_records:
        prob = r.get('prediction_prob', 0) or 0
        hit = 1 if r['prediction'] == r['actual_outcome'][:2] else 0
        if prob > 0:
            all_pairs.append((prob, hit))

    global_calib = isotonic_regression(all_pairs)
    if global_calib:
        print('  全局校准曲线:')
        for pred, actual in global_calib:
            delta = actual - pred
            print(f'    预测 {pred:.1%} → 实际 {actual:.1%} (偏差 {delta:+.1%})')

    # Step 3: EV方向重算
    print('\n🎯 Step 3: EV方向重算')
    ev_hit_before = 0
    ev_hit_after = 0
    prob_hit = 0
    total = 0

    for r in all_records:
        if not r.get('odds_win') or not r.get('final_win'):
            continue
        total += 1
        actual = r['actual_outcome'][:2]

        # 概率最高方向命中
        prob_dir = r.get('prediction', '')
        if prob_dir == actual:
            prob_hit += 1

        # 旧EV方向
        old_dir = r.get('best_direction_cn', '') or prob_dir
        if old_dir == actual:
            ev_hit_before += 1

        # 新EV方向（用校准概率）
        new_dir = compute_ev_direction(r, global_calib)
        if new_dir == actual:
            ev_hit_after += 1

    print(f'  概率最高命中: {prob_hit}/{total} = {prob_hit/total:.1%}')
    print(f'  EV方向命中(旧): {ev_hit_before}/{total} = {ev_hit_before/total:.1%}')
    print(f'  EV方向命中(校准后): {ev_hit_after}/{total} = {ev_hit_after/total:.1%}')

    # 信心分层统计
    print('\n📊 信心分层命中率 (校准后)')
    confidence_bins = defaultdict(lambda: {'total': 0, 'ev_hit': 0, 'prob_hit': 0})
    for r in all_records:
        if not r.get('odds_win') or not r.get('final_win'):
            continue
        prob = r.get('prediction_prob', 0) or 0
        actual = r['actual_outcome'][:2]

        if prob >= 0.55:
            level = '≥55%'
        elif prob >= 0.50:
            level = '50-55%'
        elif prob >= 0.45:
            level = '45-50%'
        elif prob >= 0.40:
            level = '40-45%'
        else:
            level = '<40%'

        confidence_bins[level]['total'] += 1
        if compute_ev_direction(r, global_calib) == actual:
            confidence_bins[level]['ev_hit'] += 1
        if r.get('prediction', '') == actual:
            confidence_bins[level]['prob_hit'] += 1

    for level in ['≥55%', '50-55%', '45-50%', '40-45%', '<40%']:
        b = confidence_bins[level]
        if b['total']:
            print(f'  {level}: {b["total"]}场, '
                  f'EV命中={b["ev_hit"]/b["total"]:.1%}, '
                  f'概率命中={b["prob_hit"]/b["total"]:.1%}')

    # 保存校准参数
    output = {
        'global_params': global_params,
        'league_params': league_params,
        'global_calibration': global_calib,
        'generated_at': __import__('datetime').datetime.now().isoformat(),
        'sample_size': len(all_records),
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, '..', 'data', 'calibration_params.json')

    if not args.dry_run:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f'\n💾 校准参数已保存: {output_path}')

    # 更新DB
    if args.update_db and not args.dry_run:
        print('\n🔄 更新DB中的EV方向...')
        conn = sqlite3.connect(args.db)
        c = conn.cursor()
        updated = 0
        for r in all_records:
            if not r.get('odds_win') or not r.get('final_win'):
                continue
            new_dir = compute_ev_direction(r, global_calib)
            c.execute("""
                UPDATE poisson_predictions SET best_direction_cn = ?
                WHERE date = ? AND home_team = ? AND away_team = ?
            """, (new_dir, r['date'], r['home_team'], r['away_team']))
            updated += 1
        conn.commit()
        conn.close()
        print(f'  更新了 {updated} 条记录的EV方向')

    print('\n✅ 校准完成')


if __name__ == '__main__':
    main()
