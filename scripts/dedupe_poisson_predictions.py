#!/usr/bin/env python3
"""去重 poisson_predictions（同一 match_id 多行）+ 补缺失唯一索引。

根因：sync_results_to_db.py 用 `INSERT OR IGNORE` 防重，但表上**从没有 match_id 唯一约束**
→ OR IGNORE 永远不生效 → 每次 rebuild/backfill 都追加整批副本
（实测 2026-07-23 rebuild 365 行 / 200 个 fid、2026-07-24 over_backfill 7717 行 / 7706 个 fid）。

本脚本：① 每个 fid 只留一行（择优）② 用其余副本补它的空字段
③ 建 `idx_pp_match_id` 部分唯一索引，让 INSERT OR IGNORE 真正生效（根治，防回流）。

用法：python3 scripts/dedupe_poisson_predictions.py [--apply]   （默认 dry-run）
"""
import os
import re
import sqlite3
import sys
from collections import Counter

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

FILL_IF_ZERO = ['odds_win', 'odds_draw', 'odds_loss',
                'hkjc_close_w', 'hkjc_close_d', 'hkjc_close_l',
                'pinnacle_close_w', 'pinnacle_close_d', 'pinnacle_close_l',
                'pinnacle_open_w', 'pinnacle_open_d', 'pinnacle_open_l',
                'hkjc_open_w', 'hkjc_open_d', 'hkjc_open_l',
                'bet365_close_w', 'bet365_close_d', 'bet365_close_l',
                'william_close_w', 'william_close_d', 'william_close_l',
                'ms_close_w', 'ms_close_d', 'ms_close_l',
                'liji_close_w', 'liji_close_d', 'liji_close_l']
FILL_IF_NULL = ['poisson_win', 'poisson_draw', 'poisson_loss',
                'lgbm_win', 'lgbm_draw', 'lgbm_loss']
FILL_IF_EMPTY = ['reference_score', 'actual_outcome']


def norm_score(s):
    """比分归一化用于投票：'2-1' / '2 - 1' → '2-1'；非法返回 None。"""
    m = re.search(r'(\d+)\s*-\s*(\d+)', s or '')
    return f'{m.group(1)}-{m.group(2)}' if m else None


def pick_keep(rows):
    """择优：比分取「非空值里的多数票」，同票优先规范格式 'H - A'、再取最新 created_at;
    保留下来的行 = 持该比分的行里信息最全的一行（再平手取最大 id）。"""
    scored = [r for r in rows if norm_score(r['reference_score'])]
    keep_score = None
    if scored:
        votes = Counter(norm_score(r['reference_score']) for r in scored)
        top = max(votes.values())
        tied = [s for s, c in votes.items() if c == top]
        if len(tied) == 1:
            keep_score = tied[0]
        else:  # 同票：规范格式优先，再比最新
            best = None
            for r in scored:
                if norm_score(r['reference_score']) not in tied:
                    continue
                canon = ' ' in (r['reference_score'] or '')
                key = (canon, r['created_at'] or '', r['id'])
                if best is None or key > best[0]:
                    best = (key, norm_score(r['reference_score']))
            keep_score = best[1]

    def completeness(r):
        n = sum(1 for c in FILL_IF_ZERO if (r[c] or 0))
        n += sum(1 for c in FILL_IF_NULL if r[c] is not None)
        n += sum(1 for c in FILL_IF_EMPTY if (r[c] or '').strip())
        return n

    pool = [r for r in rows if norm_score(r['reference_score']) == keep_score] if keep_score else rows
    keep = max(pool, key=lambda r: (completeness(r), r['created_at'] or '', r['id']))
    return keep, keep_score


def main():
    apply = '--apply' in sys.argv
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cols = [d[1] for d in cur.execute('PRAGMA table_info(poisson_predictions)')]

    dups = cur.execute("""SELECT match_id, COUNT(*) n FROM poisson_predictions
                          WHERE match_id IS NOT NULL AND match_id <> ''
                          GROUP BY match_id HAVING n > 1 ORDER BY n DESC""").fetchall()
    orphan = cur.execute("""SELECT COUNT(*) FROM poisson_predictions
                            WHERE match_id IS NULL OR match_id = ''""").fetchone()[0]
    total = cur.execute('SELECT COUNT(*) FROM poisson_predictions').fetchone()[0]
    print(f'总行 {total}｜重复组 {len(dups)}｜多余行 {sum(d["n"] - 1 for d in dups)}｜无 match_id 行 {orphan}')

    fills = []
    for d in dups:
        fid = d['match_id']
        rows = [dict(r) for r in cur.execute(
            'SELECT * FROM poisson_predictions WHERE match_id=? ORDER BY id', (fid,))]
        keep, score = pick_keep(rows)
        others = [r for r in rows if r['id'] != keep['id']]
        for c in FILL_IF_EMPTY:
            if not (keep[c] or '').strip():
                for o in others:
                    if (o[c] or '').strip():
                        keep[c] = o[c]
                        fills.append((keep['id'], c, o[c]))
                        break
        for c in FILL_IF_NULL:
            if keep[c] is None:
                for o in others:
                    if o[c] is not None:
                        keep[c] = o[c]
                        fills.append((keep['id'], c, o[c]))
                        break
        for c in FILL_IF_ZERO:
            if not (keep[c] or 0):
                for o in others:
                    if o[c]:
                        keep[c] = o[c]
                        fills.append((keep['id'], c, o[c]))
                        break

        if apply:
            sets = ', '.join(f'"{c}"=?' for c in FILL_IF_EMPTY + FILL_IF_NULL + FILL_IF_ZERO)
            cur.execute(f'UPDATE poisson_predictions SET {sets} WHERE id=?',
                        [keep[c] for c in FILL_IF_EMPTY + FILL_IF_NULL + FILL_IF_ZERO] + [keep['id']])
            cur.execute('DELETE FROM poisson_predictions WHERE match_id=? AND id<>?',
                        (fid, keep['id']))
        if len(dups) <= 12 or d['n'] >= 4:
            ids = [r['id'] for r in rows]
            print(f"  fid={fid} n={d['n']} ids={ids} → 留 {keep['id']} 比分={keep['reference_score']!r}"
                  f"{' (投票修正)' if score and norm_score(rows[0]['reference_score']) not in (None, score) else ''}")

    print(f'合并补字段操作 {len(fills)} 处')

    if apply:
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_match_id
                       ON poisson_predictions(match_id)
                       WHERE match_id IS NOT NULL AND match_id <> ''""")
        con.commit()
        left = cur.execute("""SELECT COUNT(*) FROM (SELECT match_id FROM poisson_predictions
                              WHERE match_id IS NOT NULL AND match_id <> ''
                              GROUP BY match_id HAVING COUNT(*) > 1)""").fetchone()[0]
        print(f'✅ 已写盘：总行 {cur.execute("SELECT COUNT(*) FROM poisson_predictions").fetchone()[0]}'
              f'｜剩余重复组 {left}｜索引 '
              f'{[r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type=\'index\' AND tbl_name=\'poisson_predictions\'")]}')
    else:
        print('（dry-run，未写盘；加 --apply 执行）')
    con.close()


if __name__ == '__main__':
    main()
