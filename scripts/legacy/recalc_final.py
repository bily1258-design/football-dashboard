#!/usr/bin/env python3
"""recalc_final.py — 用 DB 存的 lambda+implied 按 conf=0.5 重算 final_win/draw/loss
逻辑跟 verify_conf.py + daily_report.py:bayesian_adjustment 一致
"""
import sqlite3, math, os, sys

DB_PATH = 'data/football.db'
CONFIDENCE = 0.5  # 贝叶斯：conf×泊松 + (1-conf)×市场

def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def poisson_match_probs(lam_h, lam_a, max_goals=10):
    p_h = [poisson_pmf(lam_h, k) for k in range(max_goals + 1)]
    p_a = [poisson_pmf(lam_a, k) for k in range(max_goals + 1)]
    pw = pd_ = pl = 0.0
    for k in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = p_h[k] * p_a[j]
            if k > j:   pw += p
            elif k == j: pd_ += p
            else:        pl += p
    return pw, pd_, pl

def main():
    if not os.path.exists(DB_PATH):
        print(f'❌ DB 不存在: {DB_PATH}'); sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date, home_team, away_team,
               home_lambda, away_lambda,
               implied_prob_w, implied_prob_d, implied_prob_l,
               final_win, final_draw, final_loss
        FROM poisson_predictions
        WHERE home_lambda > 0 AND away_lambda > 0
          AND implied_prob_w > 0 AND implied_prob_d > 0 AND implied_prob_l > 0
    """)
    rows = cur.fetchall()
    print(f'📊 读到 {len(rows)} 场有完整 lambda+implied')
    updated = changed = 0
    for r in rows:
        (id_, date, home, away,
         lam_h, lam_a, imp_w, imp_d, imp_l,
         old_fw, old_fd, old_fl) = r
        pw, pd_, pl = poisson_match_probs(lam_h, lam_a)
        nfw = CONFIDENCE * pw  + (1 - CONFIDENCE) * imp_w
        nfd = CONFIDENCE * pd_ + (1 - CONFIDENCE) * imp_d
        nfl = CONFIDENCE * pl  + (1 - CONFIDENCE) * imp_l
        s = nfw + nfd + nfl
        if s > 0:
            nfw, nfd, nfl = nfw/s, nfd/s, nfl/s
        if (old_fw is None or abs(nfw - old_fw) > 0.001 or
            abs(nfd - old_fd) > 0.001 or
            abs(nfl - old_fl) > 0.001):
            changed += 1
        cur.execute("""
            UPDATE poisson_predictions
            SET final_win=?, final_draw=?, final_loss=?
            WHERE id=?
        """, (round(nfw, 3), round(nfd, 3), round(nfl, 3), id_))
        updated += 1
    conn.commit()
    conn.close()
    print(f'✅ 重算 {updated} 场')
    print(f'📈 {changed} 场数值有变化（剩下来是 0.7 跟 0.5 算出来恰好接近的）')

if __name__ == '__main__':
    main()
