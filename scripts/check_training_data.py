#!/usr/bin/env python3
"""检查可用于LGBM训练的数据"""
import sqlite3

conn = sqlite3.connect('data/football.db')

# Feature completeness tiers
cur = conn.execute("""
    SELECT 
        CASE 
            WHEN poisson_win IS NOT NULL AND poisson_win > 0 
                 AND implied_prob_w IS NOT NULL AND implied_prob_w > 0 
                 AND pinnacle_open_w IS NOT NULL AND pinnacle_open_w > 1.01 
                 AND had_lambda_h IS NOT NULL AND had_lambda_h > 0 
                THEN 'full (poisson+implied+open+lambda)'
            WHEN poisson_win IS NOT NULL AND poisson_win > 0 
                 AND implied_prob_w IS NOT NULL AND implied_prob_w > 0 
                THEN 'partial+ (poisson+implied)'
            WHEN implied_prob_w IS NOT NULL AND implied_prob_w > 0 
                THEN 'partial (only implied)'
            ELSE 'minimal'
        END as tier,
        COUNT(*) as cnt
    FROM poisson_predictions
    WHERE pinnacle_close_w > 1.01 AND reference_score != ''
    GROUP BY tier
    ORDER BY cnt DESC
""")
print("特征完整度分布（1154条训练样本）：")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# Date range
cur = conn.execute("""
    SELECT MIN(date), MAX(date) 
    FROM poisson_predictions 
    WHERE pinnacle_close_w > 1.01 AND reference_score != ''
""")
print(f"\n时间范围: {cur.fetchone()}")

# Leagues
cur = conn.execute("""
    SELECT league, COUNT(*) as cnt
    FROM poisson_predictions
    WHERE pinnacle_close_w > 1.01 AND reference_score != ''
    GROUP BY league
    ORDER BY cnt DESC
    LIMIT 15
""")
print("\n联赛分布（训练样本）：")
for r in cur.fetchall():
    print(f"  {r[0] or '未知'}: {r[1]}")

# Result distribution
cur = conn.execute("""
    SELECT 
        CASE 
            WHEN CAST(SUBSTR(reference_score, 1, INSTR(reference_score, '-')-1) AS INTEGER) >
                 CAST(SUBSTR(reference_score, INSTR(reference_score, '-')+1) AS INTEGER) THEN '主胜'
            WHEN CAST(SUBSTR(reference_score, 1, INSTR(reference_score, '-')-1) AS INTEGER) =
                 CAST(SUBSTR(reference_score, INSTR(reference_score, '-')+1) AS INTEGER) THEN '平局'
            ELSE '客胜'
        END as result,
        COUNT(*) as cnt
    FROM poisson_predictions
    WHERE pinnacle_close_w > 1.01 AND reference_score != ''
    GROUP BY result
""")
print("\n赛果分布：")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

conn.close()
