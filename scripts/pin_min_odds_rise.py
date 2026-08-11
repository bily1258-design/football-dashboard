#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取规律赛事: 平博初盘最小赔率(热门方) → 即时升水的场次
例: 初盘 [1.50, 3.80, 6.00] 最小=主胜1.50 → 即时 1.60 (升水+0.10)
口径: comparison 字段 (主源平博 odds_source=pinnacle 时 open/current 均为平博)
过滤: 初盘热门方向与即时热门方向反转的坏数据(如主客错位)跳过
用法: python3 scripts/pin_min_odds_rise.py [最小升水阈值] [输出场数]
默认: 阈值0.05, 显示前100场(按升水降序)
"""
import json
import os
import sys
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, 'docs', 'data', 'results.json')

MIN_RISE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
MAX_SHOW = int(sys.argv[2]) if len(sys.argv) > 2 else 100

SIDES = ['主胜', '平局', '客胜']


def load_data():
    with open(RESULTS, encoding='utf-8') as f:
        return json.load(f)


def main():
    data = load_data()
    matches = data.get('matches', [])
    hits = []
    skipped_bad = 0
    skipped_nonpin = 0

    for m in matches:
        comp = m.get('comparison') or {}
        if comp.get('source') != 'pinnacle':
            skipped_nonpin += 1
            continue
        op = comp.get('open')
        cur = comp.get('current')
        if not op or not cur:
            continue
        if not all(isinstance(x, (int, float)) and x > 1 for x in op):
            continue
        if not all(isinstance(x, (int, float)) and x > 1 for x in cur):
            continue

        # 初盘热门方向 (最小赔率)
        idx = op.index(min(op))
        # 过滤坏数据: 即时热门方向与初盘相反 → 主客错位/数据异常
        if cur.index(min(cur)) != idx:
            skipped_bad += 1
            continue

        open_v = op[idx]
        cur_v = cur[idx]
        rise = round(cur_v - open_v, 2)
        if rise >= MIN_RISE:
            hits.append({
                'm': m,
                'side': SIDES[idx],
                'open': open_v,
                'cur': cur_v,
                'rise': rise,
            })

    hits.sort(key=lambda h: -h['rise'])

    print(f"平博初盘最小赔率(热门方)→即时升水 ({len(hits)}场, 阈值+{MIN_RISE}, 跳过坏数据{skipped_bad}场/非平博主源{skipped_nonpin}场)")
    print("=" * 92)
    print(f"{'日期时间':<16}{'联赛':<12}{'对阵':<32}{'热门方':<6}{'初→即':<14}{'升水'}")
    print("-" * 92)
    for h in hits[:MAX_SHOW]:
        m = h['m']
        mt = (m.get('match_time') or m.get('date') or '')[:16]
        league = (m.get('league') or m.get('event') or '')[:11]
        teams = f"{m.get('home_team','')} vs {m.get('away_team','')}"[:31]
        print(f"{mt:<16}{league:<12}{teams:<32}{h['side']:<6}"
              f"{h['open']:.2f}→{h['cur']:.2f}{'':<8}+{h['rise']:.2f}")
    print("-" * 92)

    if hits:
        dist = collections.Counter()
        for h in hits:
            r = h['rise']
            if r < 0.10:
                dist['0.05~0.09'] += 1
            elif r < 0.20:
                dist['0.10~0.19'] += 1
            elif r < 0.50:
                dist['0.20~0.49'] += 1
            else:
                dist['≥0.50'] += 1
        print("升水幅度分布:", dict(dist))
        print(f"未开赛场次: {sum(1 for h in hits if not h['m'].get('score'))} / {len(hits)}")


if __name__ == '__main__':
    main()
