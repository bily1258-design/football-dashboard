#!/usr/bin/env python3
"""results.json 字段缺失/空值审计。

口径要点（踩过的坑）：
- score 只有在形如 "2 - 1" 时才算「有比分」；"推迟"/"取消"/空串一律算无比分，
  否则会把推迟场次误报成「有 score 无 hit」（假象）。
- hit 只有在 score 为真比分时才有意义，无比分场次 hit 空不算缺失。
- league 字段恒空（未使用，真正在用的是 event），warning 空 = 无预警，
  beidan_no/jingcai_no 空 = 非北单/竞彩，ah_* 仅当日新抓场次才有 —— 均属常态。

用法：python3 scripts/audit_fields.py [--json]
"""
import json
import os
import re
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, 'docs', 'data', 'results.json')

SCORE_RE = re.compile(r'^\s*\d+\s*-\s*\d+\s*$')

# (字段, 空值定义说明, 是否常态)
FIELDS = [
    ('event', '无赛事名', '常态(偶发)'),
    ('score', '无真比分(空/推迟/取消)', '待判(见下方分类)'),
    ('hit', '无命中标记', '仅当有真比分时才算缺失'),
    ('beidan_no', '非北单场次', '常态'),
    ('jingcai_no', '非竞彩场次', '常态'),
    ('ah_handicap', '当日未抓亚盘', '常态(不回填)'),
    ('ah_open_handicap', '当日未抓亚盘', '常态(不回填)'),
    ('similar_matches', '键缺失', '异常'),
    ('total_goals_top3', 'λ不可用', '异常'),
]


def is_empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    if isinstance(v, (int, float, bool)):
        return False  # 0 是有效值
    return False


def real_score(v):
    return isinstance(v, str) and bool(SCORE_RE.match(v))


def main():
    as_json = '--json' in sys.argv
    d = json.load(open(RESULTS, encoding='utf-8'))
    ms = d['matches']
    n = len(ms)

    table = []
    for key, note, verdict in FIELDS:
        miss = sum(1 for m in ms if key not in m)
        emp = sum(1 for m in ms if key in m and is_empty(m[key]))
        if key == 'score':
            emp = sum(1 for m in ms if not real_score(m.get('score')))
        if key == 'hit':
            # 只有「有真比分」的场次，hit 空才算缺失
            emp = sum(1 for m in ms if real_score(m.get('score')) and is_empty(m.get('hit')))
        table.append({'field': key, 'missing': miss, 'empty': emp,
                      'note': note, 'verdict': verdict})

    # 明细分类
    score_empty = [m for m in ms if not real_score(m.get('score'))]
    no_score_status = Counter(str(m.get('score') or '(空)') for m in score_empty)
    hit_gap = [m for m in ms if real_score(m.get('score')) and is_empty(m.get('hit'))]
    tg_empty = [m for m in ms if 'total_goals_top3' in m and is_empty(m['total_goals_top3'])]
    sm_miss = [m for m in ms if 'similar_matches' not in m]
    ev_empty = [m for m in ms if is_empty(m.get('event'))]

    out = {
        'total': n,
        'generated_at': d.get('generated_at'),
        'fields': table,
        'detail': {
            'score_empty_by_value': dict(no_score_status),
            'hit_gap': [(m.get('date'), m.get('home_team'), m.get('away_team')) for m in hit_gap],
            'total_goals_top3_empty': [(m.get('date'), m.get('home_team'), m.get('away_team')) for m in tg_empty],
            'similar_matches_missing': [(m.get('date'), m.get('home_team'), m.get('away_team')) for m in sm_miss],
            'event_empty': [(m.get('date'), m.get('home_team'), m.get('away_team')) for m in ev_empty],
        },
    }

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    print(f"results.json 字段审计  分母 {n}  生成于 {d.get('generated_at')}")
    print(f"{'字段':<20}{'缺失':>8}{'空值':>8}  判定")
    for r in table:
        print(f"{r['field']:<20}{r['missing']:>8}{r['empty']:>8}  {r['verdict']}  ({r['note']})")
    print('\n-- score 空值构成 --')
    for k, v in no_score_status.most_common():
        print(f"  {k}: {v}")
    if hit_gap:
        print(f'\n-- 有真比分但 hit 空 ({len(hit_gap)}) --')
        for m in hit_gap:
            print(f"  {m.get('date')} {m.get('home_team')}-{m.get('away_team')} score={m.get('score')}")
    for label, lst in (('total_goals_top3 空', tg_empty), ('similar_matches 缺键', sm_miss)):
        if lst:
            print(f'\n-- {label} ({len(lst)}) --')
            for m in lst:
                print(f"  {m.get('date')} {m.get('home_team')}-{m.get('away_team')}")


if __name__ == '__main__':
    main()
