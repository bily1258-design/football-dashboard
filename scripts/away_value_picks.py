#!/usr/bin/env python3
"""客胜价值投注清单生成器
规则(经HKJC真实赔率回测验证): best_value.outcome==away 且 EV>0.5 且 HKJC客胜赔率 3-6
「今日窗口」= 今天12:00 → 明天11:59(跨自然日); 默认只输出窗口内及未来未开赛(可投注)场次
附注: 平博(Pinnacle)与HKJC的初盘/即时赔率(主/平/客三元组, 参考用)
用法: python3 scripts/away_value_picks.py [--all]  # --all 输出全部, 默认只输出窗口内及未来未开赛
"""
import json
import sys
import datetime

D = json.load(open('docs/data/results.json'))
MS = D['matches']

def fmt3(arr):
    """三元组 [主,平,客] -> 格式化字符串"""
    if not arr or len(arr) < 3:
        return '-'
    return f"{arr[0]}/{arr[1]}/{arr[2]}"

def parse_dt(s):
    """'2026-08-11 01:00' -> datetime; 只有日期则视为当天12:00(窗口边界用)"""
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
    show_all = '--all' in sys.argv
    now = datetime.datetime.now()
    # 今日窗口: 今天12:00 → 明天11:59
    win_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    win_end = win_start + datetime.timedelta(days=1) - datetime.timedelta(minutes=1)
    win_label = f"{win_start.strftime('%m-%d %H:%M')}~{win_end.strftime('%m-%d %H:%M')}"
    rows = []
    for m in MS:
        bv = m.get('best_value')
        if not bv or bv.get('outcome') != 'away' or bv.get('ev', 0) <= 0.5:
            continue
        pc = m.get('pin_comparison') or {}
        cur = pc.get('current')
        if not cur or len(cur) < 3 or not (3 <= cur[2] <= 6):
            continue
        mt = parse_dt(m.get('match_time') or m.get('date'))
        if not show_all:
            if m.get('score'):
                continue  # 已开赛, 跳过
            if mt is None or mt < win_start:
                continue  # 窗口开始前的已过场次, 跳过
        comp = m.get('comparison') or {}  # 平博 Pinnacle
        rows.append({
            'date': m.get('date', ''), 'mt': mt, 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2], 'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
            # 参考赔率: 平博开/即, HKJC开/即 (均为 主/平/客 三元组)
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3(pc.get('open')), 'hkjc_cur': fmt3(cur),
        })
    rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))

    print(f"客胜价值投注清单 ({'全部' if show_all else '今日窗口(' + win_label + ')内及未来未开赛可投'} {len(rows)}场) 规则: 客胜+EV>0.5+赔率3-6")
    print("=" * 92)
    for r in rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        print(f"{t} [{r['league']}] {r['home']} vs {r['away']}")
        print(f"   HKJC客胜 {r['odds']} | 模型概率 {r['prob']*100:.0f}% | EV {r['ev']:.2f}")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not rows:
        if show_all:
            print("(全部场次无符合条件者)")
        else:
            print(f"(今日窗口 {win_label} 内及未来无未开赛可投场次)")

    # 窗口内汇总(含已开赛, 供复盘)
    in_win = [r for r in rows if r['mt'] and win_start <= r['mt'] <= win_end]
    if in_win and not show_all:
        print("=" * 92)
        print(f"今日窗口({win_label})内可投注场次: {len(in_win)}场")

if __name__ == '__main__':
    main()
