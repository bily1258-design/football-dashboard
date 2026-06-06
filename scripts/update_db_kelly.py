#!/usr/bin/env python3
"""
update_db_kelly.py — 重新计算数据库中所有记录的 Kelly 指数

Kelly 公式（与 daily_report.py:1551 calc_kelly_with_fallback 一致）：
  - 概率优先级：fusion_win/draw/loss > 0  ->  用 fusion
                  否则                     ->  降级用 final_win/draw/loss
  - 赔率优先级：odds_win/draw/loss > 1.01  ->  用实际赔率
                  否则                     ->  用 1/prob 反推隐含赔率
  - 凯利：(b*prob - q) / b  * 0.5  （半凯利，6位小数）
  - 负值钳制为 0

调用：
  python scripts/update_db_kelly.py
  python scripts/update_db_kelly.py --db data/football.db
"""

import os
import sys
import sqlite3
import argparse

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
DEFAULT_DB = os.path.join(SCRIPT_DIR, "data", "football.db")


def calc_kelly_with_fallback(prob: float, odds: float) -> float:
    """
    半凯利指数：赔率缺失/无效时用概率反推隐含赔率
    与 daily_report.py:1551 完全一致
    """
    if not prob or prob <= 0:
        return 0.0
    if odds and odds > 1.01:
        b = odds - 1.0
    else:
        # 赔率缺失时用 1/prob 反推
        implied_odds = 1.0 / prob
        b = implied_odds - 1.0
    q = 1.0 - prob
    if b > 0:
        kelly = (b * prob - q) / b
        return round(max(0, kelly * 0.5), 6)
    return 0.0


def ensure_columns(cursor):
    """确保 kelly_win/draw/loss 列存在（防御性）"""
    cursor.execute("PRAGMA table_info(poisson_predictions)")
    cols = [r[1] for r in cursor.fetchall()]
    for col in ('kelly_win', 'kelly_draw', 'kelly_loss'):
        if col not in cols:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} REAL")
            print(f"  + 添加列: {col}")


def update_kelly(db_path: str):
    """
    主流程：读所有记录 -> 算三向 Kelly -> UPDATE 回 DB
    """
    if not os.path.exists(db_path):
        print(f"  DB 不存在: {db_path}")
        return 0, 0, {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ensure_columns(cursor)
    conn.commit()

    cursor.execute("""
        SELECT id, fusion_win, fusion_draw, fusion_loss,
               final_win, final_draw, final_loss,
               odds_win, odds_draw, odds_loss
        FROM poisson_predictions
    """)
    rows = cursor.fetchall()
    total = len(rows)
    print(f"  总记录数: {total}")

    updated = 0
    errors = 0
    source_fusion = 0
    source_final = 0
    no_prob = 0

    for row in rows:
        try:
            # 概率优先级：fusion > final
            if row['fusion_win'] and row['fusion_win'] > 0:
                prob_w, prob_d, prob_l = row['fusion_win'], row['fusion_draw'], row['fusion_loss']
                source_fusion += 1
            elif row['final_win'] and row['final_win'] > 0:
                prob_w, prob_d, prob_l = row['final_win'], row['final_draw'], row['final_loss']
                source_final += 1
            else:
                # 无任何模型概率 -> Kelly 全 0
                prob_w = prob_d = prob_l = 0
                no_prob += 1

            kelly_w = calc_kelly_with_fallback(prob_w, row['odds_win'] or 0)
            kelly_d = calc_kelly_with_fallback(prob_d, row['odds_draw'] or 0)
            kelly_l = calc_kelly_with_fallback(prob_l, row['odds_loss'] or 0)

            cursor.execute("""
                UPDATE poisson_predictions
                SET kelly_win = ?, kelly_draw = ?, kelly_loss = ?
                WHERE id = ?
            """, (kelly_w, kelly_d, kelly_l, row['id']))

            updated += 1

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  错误 id={row['id']}: {str(e)[:80]}")

    conn.commit()
    conn.close()

    return updated, errors, {
        'fusion': source_fusion,
        'final': source_final,
        'no_prob': no_prob,
    }


def main():
    parser = argparse.ArgumentParser(description='重算 DB 中所有记录的 Kelly 指数（修复 Kelly 链条硬断）')
    parser.add_argument('--db', type=str, default=DEFAULT_DB, help='DB 路径（默认 data/football.db）')
    args = parser.parse_args()

    print("=" * 60)
    print("Kelly 指数数据库更新")
    print("公式: 半凯利 (prob*odds-1)/(odds-1) * 0.5")
    print("概率优先级: fusion > final")
    print("赔率优先级: odds > 1.01 -> 用实际；否则 1/prob 反推")
    print("=" * 60)
    print(f"DB: {args.db}")

    updated, errors, stats = update_kelly(args.db)

    print(f"\n  更新完成: {updated} 条 (错误: {errors})")
    if stats:
        print(f"   - 用 fusion 概率: {stats['fusion']}")
        print(f"   - 降级用 final:   {stats['final']}")
        print(f"   - 无模型概率(全0): {stats['no_prob']}")
    print("=" * 60)


if __name__ == '__main__':
    main()
