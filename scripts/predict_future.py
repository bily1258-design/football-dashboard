#!/usr/bin/env python3
"""predict_future.py — 从 DB 已有 Pinnacle 赔率反推泊松预测，补填未来比赛"""

import sqlite3, math, argparse, os, json
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_DB = os.path.join(REPO_DIR, 'data', 'football.db')
CALIB_PATH = os.path.join(REPO_DIR, 'data', 'calibration_params.json')

LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0

def load_calibration():
    if not os.path.exists(CALIB_PATH):
        return {}, {}, []
    with open(CALIB_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return (data.get('league_params', {}),
            data.get('global_params', {}),
            data.get('global_calibration', []))

def calibrate_prob(prob, calib_map):
    if not calib_map:
        return prob
    for i in range(len(calib_map) - 1):
        lo_p, lo_a = calib_map[i]
        hi_p, hi_a = calib_map[i+1]
        if lo_p <= prob <= hi_p:
            t = (prob - lo_p) / max(hi_p - lo_p, 1e-9)
            return lo_a + t * (hi_a - lo_a)
    if prob < calib_map[0][0]:
        return calib_map[0][1]
    return calib_map[-1][1]

def confidence_tier(prob):
    if prob >= 0.55: return 'high'
    elif prob >= 0.50: return 'medium'
    elif prob >= 0.45: return 'low'
    return 'very_low'

def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def poisson_match_probs(lam_h, lam_a, max_goals=10):
    p_h = [poisson_pmf(lam_h, k) for k in range(max_goals + 1)]
    p_a = [poisson_pmf(lam_a, k) for k in range(max_goals + 1)]
    p_w = p_d = p_l = 0.0
    for k in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = p_h[k] * p_a[j]
            if k > j: p_w += p
            elif k == j: p_d += p
            else: p_l += p
    return p_w, p_d, p_l

def implied_from_odds(odds_w, odds_d, odds_l):
    if not odds_w or not odds_d or not odds_l:
        return None, None, None
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None
    raw_w, raw_d, raw_l = 1.0/odds_w, 1.0/odds_d, 1.0/odds_l
    total = raw_w + raw_d + raw_l
    return raw_w/total, raw_d/total, raw_l/total

def estimate_lambdas(imp_w, imp_d, imp_l, league_params, league):
    lp = league_params.get(league, {})
    base_goals = lp.get('base_total_goals', 2.4) / 2
    home_adv = lp.get('home_adv', 0.15)
    skill_factor = lp.get('skill_factor', 0.6)
    denom = max(imp_w + imp_l, 0.01)
    share_h = imp_w / denom
    skill_adj = skill_factor * (share_h - 0.5)
    lam_h = base_goals + home_adv + skill_adj
    lam_a = base_goals - home_adv - skill_adj
    lam_h = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_h))
    lam_a = max(LAMBDA_MIN, min(LAMBDA_MAX, lam_a))
    return round(lam_h, 3), round(lam_a, 3)

def main():
    parser = argparse.ArgumentParser(description='从Pinnacle赔率反推泊松预测，补填未来比赛')
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'), help='目标日期')
    args = parser.parse_args()

    league_params, _, calib_map = load_calibration()
    
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT p.* FROM poisson_predictions p
        WHERE p.date = ? AND p.poisson_win IS NULL
        AND (p.pinnacle_close_w > 0 OR p.bet365_close_w > 0 OR p.ms_close_w > 0)
    """, (args.date,))
    rows = cur.fetchall()
    print(f"待补预测: {len(rows)} 场 (日期={args.date})")

    updated = 0
    for row in rows:
        odds_w = row['pinnacle_close_w'] or row['bet365_close_w'] or row['ms_close_w'] or 0
        odds_d = row['pinnacle_close_d'] or row['bet365_close_d'] or row['ms_close_d'] or 0
        odds_l = row['pinnacle_close_l'] or row['bet365_close_l'] or row['ms_close_l'] or 0
        
        if not odds_w or not odds_d or not odds_l:
            continue

        imp_w, imp_d, imp_l = implied_from_odds(odds_w, odds_d, odds_l)
        if imp_w is None:
            continue

        league = row['league'] or ''
        lam_h, lam_a = estimate_lambdas(imp_w, imp_d, imp_l, league_params, league)
        p_pois_w, p_pois_d, p_pois_l = poisson_match_probs(lam_h, lam_a)

        f_w = 0.5 * p_pois_w + 0.5 * imp_w
        f_d = 0.5 * p_pois_d + 0.5 * imp_d
        f_l = 0.5 * p_pois_l + 0.5 * imp_l
        s = f_w + f_d + f_l
        f_w, f_d, f_l = f_w/s, f_d/s, f_l/s

        if f_w >= f_d and f_w >= f_l:
            prediction = '主胜'
            pred_prob = f_w
        elif f_l >= f_d:
            prediction = '客胜'
            pred_prob = f_l
        else:
            prediction = '平局'
            pred_prob = f_d

        cal_prob = calibrate_prob(pred_prob, calib_map)
        cal_tier = confidence_tier(cal_prob)

        cur.execute("""
            UPDATE poisson_predictions SET
                prediction = ?, prediction_prob = ?,
                odds_win = ?, odds_draw = ?, odds_loss = ?,
                poisson_win = ?, poisson_draw = ?, poisson_loss = ?,
                final_win = ?, final_draw = ?, final_loss = ?,
                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                home_lambda = ?, away_lambda = ?,
                best_direction_cn = ?,
                confidence_tier = ?, calibrated_prob = ?
            WHERE id = ?
        """, (
            prediction, round(pred_prob, 3),
            round(odds_w, 2), round(odds_d, 2), round(odds_l, 2),
            round(p_pois_w, 3), round(p_pois_d, 3), round(p_pois_l, 3),
            round(f_w, 3), round(f_d, 3), round(f_l, 3),
            round(imp_w, 3), round(imp_d, 3), round(imp_l, 3),
            lam_h, lam_a, prediction,
            cal_tier, round(cal_prob, 3),
            row['id']
        ))
        updated += 1

    conn.commit()
    conn.close()
    print(f"✅ 已补 {updated} 场预测")

if __name__ == '__main__':
    main()
