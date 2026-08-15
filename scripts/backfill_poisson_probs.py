#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_poisson_probs.py — 回填 poisson_predictions.poisson_win/draw/loss

问题根因：ai_analysis.py 算完 model_win/draw/loss 后从不写回 DB，
导致 fit_calibrator() 永远拿不到有效概率(全 NULL) → cal_probs 从未存在
→ 账本所有 EV 一直用 LGBM 原始概率计算。

修复：从 docs/data/results.json 读历史场次的 model 概率 + score，
按 fid=match_id 回写 DB。跑完后 fit_calibrator 即可工作。
"""
import json
import os
import sqlite3
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs')
DB_PATH = os.path.join(DATA_DIR, 'football.db')
RESULTS_PATH = os.path.join(DOCS_DIR, 'data', 'results.json')


def main():
    with open(RESULTS_PATH, encoding='utf-8') as f:
        data = json.load(f)
    matches = data.get('matches', data) if isinstance(data, dict) else data

    conn = sqlite3.connect(DB_PATH)
    updated = 0
    skipped_no_score = 0
    skipped_no_prob = 0
    skipped_no_match = 0

    for m in matches:
        fid = str(m.get('fid', ''))
        if not fid:
            continue
        mw, md, ml = m.get('model_win'), m.get('model_draw'), m.get('model_loss')
        score = m.get('score')
        if not (isinstance(mw, (int, float)) and isinstance(md, (int, float)) and isinstance(ml, (int, float))):
            skipped_no_prob += 1
            continue
        if not score or '-' not in str(score):
            skipped_no_score += 1
            continue
        # 只回填概率缺失的行（保留已有值）
        cur = conn.execute(
            'UPDATE poisson_predictions SET poisson_win=?, poisson_draw=?, poisson_loss=? '
            'WHERE match_id=? AND poisson_win IS NULL',
            (round(float(mw), 6), round(float(md), 6), round(float(ml), 6), fid),
        )
        if cur.rowcount > 0:
            updated += 1
        else:
            exists = conn.execute(
                'SELECT 1 FROM poisson_predictions WHERE match_id=?', (fid,)
            ).fetchone()
            if not exists:
                skipped_no_match += 1

    conn.commit()

    # 统计回填后剩余 NULL
    total = conn.execute('SELECT COUNT(*) FROM poisson_predictions').fetchone()[0]
    still_null = conn.execute(
        'SELECT COUNT(*) FROM poisson_predictions WHERE poisson_win IS NULL'
    ).fetchone()[0]
    scored = conn.execute(
        'SELECT COUNT(*) FROM poisson_predictions WHERE reference_score IS NOT NULL AND reference_score != "" '
        'AND poisson_win IS NOT NULL AND poisson_win > 0.01 AND poisson_loss > 0.01'
    ).fetchone()[0]
    conn.close()

    print(f'回填完成: 更新 {updated} 场')
    print(f'  跳过(无model概率): {skipped_no_prob}  跳过(无比分): {skipped_no_score}  未匹配: {skipped_no_match}')
    print(f'  全表 {total} 行, 仍 NULL {still_null} 行')
    print(f'  校准器可用样本(带比分+有效概率): {scored} (需>=100)')


if __name__ == '__main__':
    main()
