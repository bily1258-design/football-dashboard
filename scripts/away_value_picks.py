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

# ===== 2026-08-14 投注簿挖掘 (1840场已结算): 甜点区/避雷规则 =====
# 甜点区: 客胜 2.5-4 赔率 + EV<0.5 + edge<10% → 历史胜率 27-43%, +38单位
# 避雷: edge>=15% 败率90.6% | kelly>=15% 败率89.8% 利润负 | EV>=2.0 败率93%
def is_sweet(m):
    """🎯甜点区: 客胜 + HKJC客赔 2.5-4 + 0<EV<0.5 + edge<0.10 (低EV中赔率温和低估)"""
    bv = m.get('best_value') or {}
    if bv.get('outcome') != 'away':
        return False
    ev = bv.get('ev', 0)
    if not (0 < ev < 0.5):
        return False
    if (bv.get('edge') or 0) >= 0.10:
        return False
    cur = hkjc_cur(m)
    if not cur or not (2.5 <= cur[2] < 4):
        return False
    return True

def avoid_reasons(m, bv=None):
    """返回避雷原因列表(可多个); 空=不避雷"""
    reasons = []
    if is_hw_avoid(m):
        reasons.append('⚡高权重')
    bv = bv or (m.get('best_value') or {})
    if (bv.get('edge') or 0) >= 0.15:
        reasons.append('edge≥15%')
    if (bv.get('kelly') or 0) >= 0.15:
        reasons.append('kelly≥15%')
    if (bv.get('ev') or 0) >= 2.0:
        reasons.append('EV≥2')
    return reasons

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
    # --md <path>: 完整清单同时写入 markdown (GitHub Pages 渲染), 微信只推摘要+链接
    md_path = None
    if '--md' in sys.argv:
        i = sys.argv.index('--md')
        if i + 1 < len(sys.argv):
            md_path = sys.argv[i + 1]
    md_file = None
    if md_path:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(md_path)), exist_ok=True)
        md_file = open(md_path, 'w', encoding='utf-8')
        md_file.write(f"# 📋 今日高置信方向投注清单\n\n> 生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | 完整明细\n\n```text\n")
        class _Tee:
            def __init__(self, *streams):
                self.streams = streams
            def write(self, s):
                for st in self.streams:
                    st.write(s)
            def flush(self):
                for st in self.streams:
                    st.flush()
        sys.stdout = _Tee(sys.__stdout__, md_file)

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

    # ========== 规则A: 高置信方向投注 ==========
    # 2026-08-17 新规格: M模型(model)与LGBM方向一致(主/平/客任一) 且 对应方向一方概率>44.9%
    rows = []
    for m in MS:
        if not in_window(m):
            continue
        md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = argmax3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        if md != ld:
            continue  # M模型与LGBM方向一致(同指主/平/客)
        if md == '主':
            mv, lv = m.get('model_win', 0), m.get('lgbm_win', 0)
        elif md == '平':
            mv, lv = m.get('model_draw', 0), m.get('lgbm_draw', 0)
        else:
            mv, lv = m.get('model_loss', 0), m.get('lgbm_loss', 0)
        if not (mv > 0.449 or lv > 0.449):
            continue  # 其中一方概率>44.9%
        cur = hkjc_cur(m)
        mt = parse_dt(m.get('match_time') or m.get('date'))
        comp = m.get('comparison') or {}  # 平博 Pinnacle
        tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        tsp = max(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        bv = m.get('best_value') or {}
        rows.append({
            'date': m.get('date', ''), 'mt': mt, 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2] if cur else None,
            'dir': md, 'model_prob': mv, 'lgbm_prob': lv, 'ev': bv.get('ev', 0),
            # 参考赔率: 平博开/即, HKJC开/即 (均为 主/平/客 三元组)
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'ts_dir': tsd, 'ts_prob': tsp,  # TS最大概率及方向
            'avoid': is_hw_avoid(m),  # ⚡高权重避雷
            'av_reasons': avoid_reasons(m, bv),  # 扩展避雷原因
        })
    rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))

    # ========== 规则C: 🎯甜点区 (2026-08-14 投注簿挖掘, 历史胜率27-43%/+38单位) ==========
    # 注意: 甜点区 EV<0.5, 与规则A(EV>0.5)互补, 必须独立扫描全量场次
    sweet_rows = []
    for m in MS:
        if not in_window(m):
            continue
        bv = m.get('best_value') or {}
        if bv.get('outcome') != 'away' or not (0 < bv.get('ev', 0) < 0.5):
            continue
        if (bv.get('edge') or 0) >= 0.10:
            continue
        cur = hkjc_cur(m)
        if not cur or not (2.5 <= cur[2] < 4):
            continue
        mt = parse_dt(m.get('match_time') or m.get('date'))
        comp = m.get('comparison') or {}
        sweet_rows.append({
            'date': m.get('date', ''), 'mt': mt, 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2], 'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'av_reasons': avoid_reasons(m, bv),
        })
    sweet_rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))
    print(f"①🎯甜点区客胜 ({len(sweet_rows)}场) 规则: 客胜+HKJC客赔2.5-4+0<EV<0.5+edge<10% | 历史回测: 43.2%胜率(44场+16.2) / 32.7%(101场+10.4)")
    print("=" * 92)
    for r in sweet_rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        print(f"{t} [{r['league']}] {r['home']} vs {r['away']} 🎯")
        print(f"   HKJC客胜 {r['odds']} | 模型概率 {r['prob']*100:.0f}% | EV {r['ev']:.2f}")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not sweet_rows:
        print("(今日窗口内及未来无甜点区场次)")

    print()
    print(f"②高置信方向投注 ({'全部' if show_all else '今日窗口(' + win_label + ')内及未来未开赛可投'} {len(rows)}场) 规则: model=LGBM方向一致(主/平/客) 且 一方概率>44.9% (2026-08-17新规格)")
    print("=" * 92)
    for r in rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        tag = ''
        if r.get('sweet'):
            tag = ' 🎯甜点'
        elif r.get('av_reasons'):
            tag = ' 🚫避雷(' + ','.join(r['av_reasons']) + ')'
        elif r.get('avoid'):
            tag = ' ⚠️⚡避雷'
        print(f"{t} [{r['league']}] {r['home']} vs {r['away']} →{r['dir']}{tag}")
        print(f"   {r['dir']}概率: model {r['model_prob']*100:.0f}% | LGBM {r['lgbm_prob']*100:.0f}% | EV {r['ev']:.2f} | TS {r['ts_dir']}{r['ts_prob']*100:.0f}%")
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
    print(f"③三方一致·客客客 ({'全部' if show_all else '今日窗口内及未来未开赛可投'} {len(rows_b)}场) 规则: model=LGBM=TS均指客 | ★=客赔<2.0且TS平<25%")
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

    # 避雷汇总 (⚡高权重 + 扩展避雷)
    av_total = [r for r in rows if r.get('av_reasons')] + [r for r in rows_b if r.get('avoid')]
    if av_total:
        print()
        print("=" * 92)
        print(f"⚠️🚫 避雷汇总 (⚡高权重/edge≥15%/kelly≥15%/EV≥2, 历史败率87-93%): 共{len(av_total)}场, 慎跟")
        for r in av_total:
            t = r['mt'].strftime('%m-%d %H:%M') if r.get('mt') else r.get('date', '')
            why = ','.join(r.get('av_reasons') or ['⚡高权重'])
            print(f"   {t} [{r.get('league','')}] {r.get('home','')} vs {r.get('away','')} 🚫{why}")

    if md_file:
        sys.stdout.write("```\n")
        sys.stdout.flush()
        sys.stdout = sys.__stdout__  # 先恢复, 避免解释器退出时flush已关闭文件
        md_file.close()

if __name__ == '__main__':
    main()
