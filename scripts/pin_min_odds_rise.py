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
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BASE, 'docs', 'data', 'results.json')

MIN_RISE = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != '--daily' else 0.05
MAX_SHOW = int(sys.argv[2]) if len(sys.argv) > 2 else 100

SIDES = ['主胜', '平局', '客胜']


def load_data():
    with open(RESULTS, encoding='utf-8') as f:
        return json.load(f)


def collect_hits(matches, min_rise):
    """返回 [(match, idx, rise)] 已过滤坏数据"""
    hits = []
    for m in matches:
        comp = m.get('comparison') or {}
        if comp.get('source') != 'pinnacle':
            continue
        op = comp.get('open')
        cur = comp.get('current')
        if not op or not cur:
            continue
        if not all(isinstance(x, (int, float)) and x > 1 for x in op):
            continue
        if not all(isinstance(x, (int, float)) and x > 1 for x in cur):
            continue
        idx = op.index(min(op))
        if cur.index(min(cur)) != idx:
            continue
        rise = cur[idx] - op[idx]
        if rise >= min_rise:
            hits.append((m, idx, rise))
    return hits


def parse_dt(s):
    try:
        return datetime.datetime.strptime((s or '')[:16], '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return None


def fmt_hit(h):
    m, idx, rise = h
    mt = (m.get('match_time') or '')[:16]
    league = (m.get('event') or m.get('league') or '')[:8]
    home = m.get('home_team', '')[:10]
    away = m.get('away_team', '')[:10]
    op = m['comparison']['open'][idx]
    cur = m['comparison']['current'][idx]
    return f"{mt} [{league}] {home} vs {away} | {SIDES[idx]} {op:.2f}→{cur:.2f} +{rise:.2f}"


def daily_report(matches, min_rise=0.10):
    """每日微信推送格式: 今日窗口可投 + 近3天已完赛参考"""
    now = datetime.datetime.now()
    win_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    win_end = (now + datetime.timedelta(days=1)).replace(hour=11, minute=59, second=59, microsecond=0)
    hits = collect_hits(matches, 0.01)

    def in_range(r):
        """只保留 0.01~0.19 区间升幅 (2026-08-15 用户要求)"""
        return 0.01 <= round(r, 2) <= 0.19

    today = []
    for h in hits:
        if not in_range(h[2]):
            continue
        t = parse_dt(h[0].get('match_time'))
        if t and win_start <= t <= win_end and not h[0].get('score'):
            today.append(h)
    today.sort(key=lambda h: -h[2])

    ref = []
    for h in hits:
        t = parse_dt(h[0].get('match_time'))
        if t and t >= now - datetime.timedelta(days=3) and h[0].get('score') and in_range(h[2]):
            ref.append(h)
    ref.sort(key=lambda h: -h[2])

    lines = []
    lines.append("📊 平博初盘最小赔率→即时升水 (仅0.01~0.19)")
    lines.append(f"🕐 窗口: {win_start.strftime('%m-%d %H:%M')} ~ {win_end.strftime('%m-%d %H:%M')} (今日可投)")
    lines.append("")
    lines.append(f"🔴 今日窗口内可投 ({len(today)}场)")
    if today:
        for h in today:
            lines.append(fmt_hit(h))
    else:
        lines.append("(无)")
    lines.append("")
    lines.append(f"🟡 近3天已完赛全部升水场次 (0.01起, {len(ref)}场)")
    if ref:
        for h in ref[:12]:
            m, idx, rise = h
            lines.append(fmt_hit(h) + f" | 比分 {m.get('score','?')}")
        if len(ref) > 12:
            lines.append(f"... 共{len(ref)}场")
        # 区间开出率统计
        import re as _re
        def _ps(s):
            s = (s or '').strip().replace(' ', '')
            mm = _re.match(r'^(\d+)[-:](\d+)$', s)
            return (int(mm.group(1)), int(mm.group(2))) if mm else None
        bins = [(0.01, 0.10), (0.10, 0.20)]
        stats = []
        for lo, hi in bins:
            sel = []
            for h in ref:
                m, idx, rise = h
                if not (lo <= rise < hi):
                    continue
                sc = _ps(m.get('score'))
                if not sc:
                    continue
                hg, ag = sc
                res = 0 if hg > ag else (1 if hg == ag else 2)
                sel.append(idx == res)
            if sel:
                n = len(sel)
                hit = sum(sel)
                stats.append(f"{lo:.2f}~{hi:.2f}:{hit}/{n}={hit/n*100:.0f}%")
        lines.append("区间开出率: " + " ".join(stats))
    else:
        lines.append("(无)")
    return "\n".join(lines)


def main():
    data = load_data()
    matches = data.get('matches', [])
    if len(sys.argv) > 1 and sys.argv[1] == '--daily':
        print(daily_report(matches))
        return
    hits = collect_hits(matches, MIN_RISE)

    print(f"平博初盘最小赔率(热门方)→即时升水 ({len(hits)}场, 阈值+{MIN_RISE})")
    print("=" * 92)
    print(f"{'日期时间':<16}{'联赛':<12}{'对阵':<32}{'热门方':<6}{'初→即':<14}{'升水'}")
    print("-" * 92)
    for h in hits[:MAX_SHOW]:
        m, idx, rise = h
        mt = (m.get('match_time') or m.get('date') or '')[:16]
        league = (m.get('league') or m.get('event') or '')[:11]
        teams = f"{m.get('home_team','')} vs {m.get('away_team','')}"[:31]
        op = m['comparison']['open'][idx]
        cur = m['comparison']['current'][idx]
        print(f"{mt:<16}{league:<12}{teams:<32}{SIDES[idx]:<6}"
              f"{op:.2f}→{cur:.2f}{'':<8}+{rise:.2f}")
    print("-" * 92)

    if hits:
        dist = collections.Counter()
        for h in hits:
            r = h[2]
            if r < 0.10:
                dist['0.05~0.09'] += 1
            elif r < 0.20:
                dist['0.10~0.19'] += 1
            elif r < 0.50:
                dist['0.20~0.49'] += 1
            else:
                dist['≥0.50'] += 1
        print("升水幅度分布:", dict(dist))
        print(f"未开赛场次: {sum(1 for h in hits if not h[0].get('score'))} / {len(hits)}")


if __name__ == '__main__':
    main()
