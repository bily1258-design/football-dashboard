#!/usr/bin/env python3
"""昨日客胜价值清单复盘生成器
输出: 昨日窗口(昨天12:00 → 今天11:59)符合条件场次的命中复盘表 + 平博/HKJC初即盘明细
规则与 away_value_picks.py 一致: best_value.outcome==away 且 EV>0.5 且 HKJC客胜赔率 3-6
用法: python3 scripts/yesterday_review.py
"""
import json
import datetime

D = json.load(open('docs/data/results.json'))
MS = D['matches']

def fmt3(arr):
    if not arr or len(arr) < 3:
        return '-'
    return f"{arr[0]}/{arr[1]}/{arr[2]}"

def parse_dt(s):
    """'2026-08-11 01:00' -> datetime; 只有日期则视为当天12:00"""
    s = (s or '').strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M')
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d') + datetime.timedelta(hours=12)
    except ValueError:
        return None

def main():
    now = datetime.datetime.now()
    # 昨日窗口: 昨天12:00 → 今天11:59
    win_end = now.replace(hour=11, minute=59, second=0, microsecond=0)
    win_start = win_end - datetime.timedelta(days=1)
    win_label = f"{win_start.strftime('%m-%d %H:%M')}~{win_end.strftime('%m-%d %H:%M')}"
    rows = []
    for m in MS:
        mt = parse_dt(m.get('match_time') or m.get('date'))
        if not mt or not (win_start <= mt <= win_end):
            continue
        bv = m.get('best_value') or {}
        pc = (m.get('pin_comparison') or {})
        cur = pc.get('current') or []
        if bv.get('outcome') != 'away' or bv.get('ev', 0) <= 0.5:
            continue
        if len(cur) < 3 or not (3 <= cur[2] <= 6):
            continue
        score = m.get('score') or ''
        hit = None
        if score:
            try:
                h, a = map(int, score.replace(' ', '').split('-'))
                hit = '✅客胜' if a > h else ('平' if a == h else '❌主胜')
            except Exception:
                hit = score
        comp = m.get('comparison') or {}
        rows.append({
            'league': m.get('event', ''), 'home': m.get('home_team', ''),
            'away': m.get('away_team', ''), 'score': score or '未开赛',
            'hit': hit or '未开赛', 'odds': cur[2],
            'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3(pc.get('open')), 'hkjc_cur': fmt3(cur),
        })
    rows.sort(key=lambda x: -x['ev'])

    if not rows:
        print(f"昨日窗口({win_label})无符合条件场次")
        return

    wins = sum(1 for r in rows if r['hit'] == '✅客胜')
    print(f"📋 昨日窗口({win_label})客胜价值清单复盘 — {len(rows)}场 | 命中 {wins}/{len(rows)} ({wins/len(rows)*100:.0f}%)")
    print("=" * 92)
    print(f"{'联赛':<10}{'对阵':<26}{'比分':<8}{'结果':<8}{'HKJC客胜':<9}{'EV':<6}")
    print("-" * 92)
    for r in rows:
        pair = f"{r['home']} vs {r['away']}"
        print(f"{r['league']:<10}{pair:<26}{r['score']:<8}{r['hit']:<8}{r['odds']:<9}{r['ev']:.2f}")

    print("=" * 92)
    print("平博/HKJC 初盘→即时 明细 (主/平/客):")
    for r in rows:
        print(f"{r['home']} vs {r['away']} {r['score']} {r['hit']}")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']} | EV {r['ev']:.2f}")

    # 盈亏估算 (每注1单位, HKJC即时客胜赔率)
    profit = 0.0
    for r in rows:
        if r['hit'] == '✅客胜':
            profit += r['odds'] - 1
        else:
            profit -= 1
    print("=" * 92)
    print(f"盈亏估算(每注1单位): {profit:+.2f} 单位")

if __name__ == '__main__':
    main()
