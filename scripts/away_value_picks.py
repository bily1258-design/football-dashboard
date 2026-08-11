#!/usr/bin/env python3
"""客胜价值投注清单生成器 + 三方一致·客客客 高胜率板块
规则A(经HKJC真实赔率回测验证): best_value.outcome==away 且 EV>0.5 且 HKJC客胜赔率 3-6
规则B(2026-08-10 HKJC口径回测, 唯一正ROI方向): model==lgbm==ts 三方一致指客(客客客)
   ★高置信标注: 客赔<2.0 且 TS平局概率<25%(剔除填充值0.241)
   客客客全组合 HKJC口径 98场 66.3% ROI+11.1%; 客客客+客赔<2.0+TS平<25% 53场 77.4% ROI+13.0%
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

def argmax3(w, dr, l):
    m = max(w, dr, l)
    return '主' if m == w else ('平' if m == dr else '客')

def hkjc_cur(m):
    """HKJC即时赔率 [主,平,客]; 缺失返回 None"""
    pc = m.get('pin_comparison') or {}
    cur = pc.get('current')
    if not cur or len(cur) < 3:
        return None
    return cur

def is_hw_avoid(m):
    """⚡高权重避雷: ⚡>=1.14 且 模型==TS 同向 (历史命中率仅35%, 2026-08-11验证, 追踪中)"""
    w = m.get('importance_weight', 0) or 0
    if w < 1.14:
        return False
    md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
    tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
    return md == tsd

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

    def in_window(m):
        """默认只输出窗口内及未来未开赛(可投注)场次"""
        if show_all:
            return True
        if m.get('score'):
            return False  # 已开赛, 跳过
        mt = parse_dt(m.get('match_time') or m.get('date'))
        if mt is None or mt < win_start:
            return False  # 窗口开始前的已过场次, 跳过
        return True

    # ========== 规则A: 客胜价值投注 ==========
    rows = []
    for m in MS:
        bv = m.get('best_value')
        if not bv or bv.get('outcome') != 'away' or bv.get('ev', 0) <= 0.5:
            continue
        cur = hkjc_cur(m)
        if not cur or not (3 <= cur[2] <= 6):
            continue
        if not in_window(m):
            continue
        mt = parse_dt(m.get('match_time') or m.get('date'))
        comp = m.get('comparison') or {}  # 平博 Pinnacle
        rows.append({
            'date': m.get('date', ''), 'mt': mt, 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2], 'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
            # 参考赔率: 平博开/即, HKJC开/即 (均为 主/平/客 三元组)
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'avoid': is_hw_avoid(m),  # ⚡高权重避雷
        })
    rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))

    print(f"①客胜价值投注清单 ({'全部' if show_all else '今日窗口(' + win_label + ')内及未来未开赛可投'} {len(rows)}场) 规则: 客胜+EV>0.5+赔率3-6")
    print("=" * 92)
    for r in rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        av = ' ⚠️⚡避雷' if r.get('avoid') else ''
        print(f"{t} [{r['league']}] {r['home']} vs {r['away']}{av}")
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

    # ========== 规则B: 三方一致·客客客 高胜率 ==========
    rows_b = []
    for m in MS:
        if not in_window(m):
            continue
        md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = argmax3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        if not (md == ld == tsd == '客'):
            continue  # 三方一致且指客
        cur = hkjc_cur(m)
        if not cur:
            continue
        mt = parse_dt(m.get('match_time') or m.get('date'))
        ts_draw = m.get('ts_draw', 0)
        is_fill = abs(ts_draw - 0.241) < 0.001  # TS填充值污染剔除
        star = (cur[2] < 2.0 and ts_draw < 0.25 and not is_fill)
        comp = m.get('comparison') or {}
        rows_b.append({
            'date': m.get('date', ''), 'mt': mt, 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2], 'ts_draw': ts_draw, 'star': star,
            'lgbm_prob': max(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0)),
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'avoid': is_hw_avoid(m),  # ⚡高权重避雷
        })
    # 高置信优先, 再按时间
    rows_b.sort(key=lambda x: (not x['star'], x['mt'] or datetime.datetime.max))

    print()
    print("=" * 92)
    print(f"②三方一致·客客客 ({'全部' if show_all else '今日窗口内及未来未开赛可投'} {len(rows_b)}场) 规则: model=LGBM=TS均指客 | ★=客赔<2.0且TS平<25%")
    print("=" * 92)
    for r in rows_b:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        star = " ★" if r['star'] else ""
        av = ' ⚠️⚡避雷' if r.get('avoid') else ''
        print(f"{t} [{r['league']}] {r['home']} vs {r['away']}{star}{av}")
        print(f"   HKJC客胜 {r['odds']} | LGBM客概率 {r['lgbm_prob']*100:.0f}% | TS平 {r['ts_draw']*100:.0f}%")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not rows_b:
        if show_all:
            print("(全部场次无符合条件者)")
        else:
            print(f"(今日窗口 {win_label} 内及未来无未开赛三方一致客场次)")

    # ⚡高权重避雷汇总
    av_total = [r for r in rows if r.get('avoid')] + [r for r in rows_b if r.get('avoid')]
    if av_total:
        print()
        print("=" * 92)
        print(f"⚠️⚡ 高权重避雷 (⚡≥1.14 且 模型==TS同向, 历史命中仅35%): 共{len(av_total)}场, 慎跟")
        for r in av_total:
            t = r['mt'].strftime('%m-%d %H:%M') if r.get('mt') else r.get('date', '')
            print(f"   {t} [{r.get('league','')}] {r.get('home','')} vs {r.get('away','')} ⚡{r.get('avoid') and '≥1.14' or ''}")

if __name__ == '__main__':
    main()
