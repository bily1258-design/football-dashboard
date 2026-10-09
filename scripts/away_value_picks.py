#!/usr/bin/env python3
"""今日推荐清单生成器 (①高置信方向投注 / ②三方一致·客客客 / ③三方一致·胜胜胜 / 📐低熵核心区 / 📋战术阵容参考)
命名更正(2026-10-09): 老名字「客胜价值投注清单」已不适用 —— 一份清单里现在没有「客胜价值」这一档。
  ① 现行口径 = model 与 LGBM 方向一致(主/平/客) 且 一方概率>44.9% (2026-08-17 新规格), 与 EV 无关
  「客胜价值」口径(best_value.outcome==away 且 EV>0.5 且 HKJC客胜赔率 3-6) 现存唯一使用者 = scripts/yesterday_review.py (昨日复盘), 只统计不选号
  🎯甜点区(客胜 + HKJC客赔 2.5-4 + 0<EV<0.5 + edge<0.10) = 本文件旧规则C, 2026-08-20 用户拍板取消 → 置空不再输出 (见下方「规则C」注释)
  ⚠️ 与「客胜价值」并非同一口径: EV 区间互斥(0<EV<0.5 vs EV>0.5)、赔率区间不同(2.5-4 vs 3-6), 无重叠场次
规则B(2026-08-10 HKJC口径回测, 唯一正ROI方向): model==lgbm==ts 三方一致指客(客客客)
   ★高置信标注: 客赔<2.0 且 TS平局概率<22%(剔除填充值0.241)  [2026-09-01 门限25%→22%]
   历史回测: 客客客全组合 98场 66.3% ROI+11.1%; 客客客+客赔<2.0+TS平<25% 53场 77.4% ROI+13.0%
   2026-09-01 收紧★门限到22% (2933场回测: 命中率83% vs 25%的75.8%, 单注EV +0.25 vs +0.17)
规则D(2026-08-23 新增, 客客客镜像): model==lgbm==ts 三方一致指主(胜胜胜)
   ★高置信标注: 主赔<2.0 且 TS平局概率<22%(剔除填充值0.241)  [2026-09-01 门限25%→22%]
「今日窗口」= 今天12:00 → 明天11:59(跨自然日); 默认只输出窗口内及未来未开赛(可投注)场次
附注: 平博(Pinnacle)与HKJC的初盘/即时赔率(主/平/客三元组, 参考用)
用法: python3 scripts/away_value_picks.py [--all]  # --all 输出全部, 默认只输出窗口内及未来未开赛
"""
import json
import math
import sys
import datetime

D = json.load(open('docs/data/results.json'))
MS = D['matches']

def fmt3(arr):
    """三元组 [主,平,客] -> 格式化字符串"""
    if not arr or len(arr) < 3:
        return '-'
    return f"{arr[0]}/{arr[1]}/{arr[2]}"

def _num3(a, b, c):
    return tuple((x if isinstance(x, (int, float)) else 0) for x in (a, b, c))

def argmax3(w, dr, l):
    w, dr, l = _num3(w, dr, l)
    m = max(w, dr, l)
    return '主' if m == w else ('平' if m == dr else '客')

def ent3(w, dr, l):
    """LGBM 三路概率的香农熵 -Σp·ln p (2026-09-29 低熵区标记用)

    只读标记量, 不参与任何筛选/规则. 熵≤1.075 ⇔ LGBM 最大概率约 ≥0.45.
    2026-09-30 全历史重算(旧注释的 1835场/47场 已过期, 此为现行口径):
      · ①池(HKJC 赔口径): 全池 ROI -8.65%; 熵≤1.075∧赔≥1.8 = 54 场 57.4% ROI+16.5%;
        熵>1.075(1261 场) ROI-12.23%  ← 池内唯一有意义的切分是"高熵整段停"
      · 账本 1355 注: 熵≤1.075 → 326 注 命中66.9% ROI+11.25%;
        熵>1.075 → 1029 注 37.8% ROI-8.57% (逐档全负: 1.075-1.085 -1.8% / 1.085-1.095 -12.9% / >1.095 -6.3%)
      · 走前验证按季度切 4 段: 高熵组 4/4 段为负或零; 低熵组 4/4 段命中 64~69%
      · 控制赔率后仍成立(同赔率带内低熵正、高熵负) → 熵不是赔率的代理, 是独立维度
    结论: 低熵作正收益区(赔≥1.8 才算), 高熵作负期望预警区, 均只标不筛, 不改规则.
    """
    try:
        s = 0.0
        for p in (w, dr, l):
            p = float(p or 0)
            if p > 0:
                s -= p * math.log(p)
        return s
    except Exception:
        return None

def hkjc_dir_odds(cur, d):
    """HKJC即时三元组按方向(主/平/客)取赔率"""
    if not cur or len(cur) < 3:
        return None
    return {'主': cur[0], '平': cur[1], '客': cur[2]}.get(d)

def hkjc_cur(m):
    """HKJC即时赔率 [主,平,客]; 缺失返回 None"""
    pc = m.get('pin_comparison') or {}
    cur = pc.get('current')
    if not cur or len(cur) < 3:
        return None
    return cur

def is_hw_avoid(m):
    """⚡高权重避雷: ⚡>=1.14 且 模型==TS 同向 (2026-08-11 小样本17场判 35%; 2026-09-23 大样本复核 1108 场 48.3% vs 均隐含 50.9% (ROI -7.0%, 略跑输价格), 仅作行内记号)"""
    w = m.get('importance_weight', 0) or 0
    if w < 1.14:
        return False
    md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
    tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
    return md == tsd

# ===== 高命中率联赛 (results.json 2579场回测, n>=20 且方向命中率>=60%, 2026-08-28 重算) =====
# 芬甲83.3% 挪甲76.3% 爱甲73.9% 奥甲71.4% 丹麦超70.4% 英联杯70.2%
# 欧罗巴杯67.3% 国际友谊赛67.3% 日职联66.7% NCAL Cup66.1% 捷甲65.6% 非女杯65.4%
# 欧冠杯65.1% 挪超65.0% 西甲65.0% SPL64.0% 荷甲62.5% 英乙62.5%
# 智利甲62.2% 澳维超60.7% 瑞典超60.0% — 清单中这些联赛加 🟢 标记
# 2026-08-21: 清单直接显示简体(取消繁转), 联赛集合随队名一起转简体
# 2026-08-28 池内回测: 高命中联赛仅作🟢标记, 不作筛选项(池内25场全是odds5.6~20.7
#   value/ruleA冷门, 命中4.0% 反被拖累; 联赛高命中=跟推荐方向低赔, 不适用冷门池)
HIGH_HIT_LEAGUES = {'芬甲', '国际友谊赛', '日职联', '智利甲', '欧冠杯',
                    '挪甲', '挪超', '丹麦超', '欧罗巴杯', '英联杯',
                    '爱甲', '奥甲', 'NCAL Cup', '捷甲', '非女杯', '西甲',
                    'SPL', '荷甲', '英乙', '澳维超', '瑞典超'}

def _mk_t2s():
    try:
        from opencc import OpenCC
        cc = OpenCC('t2s')
        return lambda s: cc.convert(s) if s else s
    except Exception:
        return lambda s: s

t2s = _mk_t2s()  # 繁体 → 简体 (看板直接显示简体, 2026-08-21)

def lg_tag(league):
    return f"{league}🟢" if league in HIGH_HIT_LEAGUES else league


def no_tag(m):
    """场次编号标记: ' #北单74/周三017' (无编号返回空串)"""
    parts = []
    b = str(m.get('beidan_no') or '').strip()
    j = str(m.get('jingcai_no') or '').strip()
    if b:
        parts.append('北单' + b)
    if j:
        parts.append(j)
    return (' #' + '/'.join(parts)) if parts else ''

# ===== 2026-08-28 池内回测定案 (betting_ledger 152场双时点) =====
# ① 平博升水+HKJC(掉水或不变) → 可投 25.0% (52场)
# ② 有★(赔率<2.0 且 TS平<25%) → 加倍 63.6% (11场); ★整体54.2% vs 无★10.2%
# ③ HKJC升水 → 不碰 7.9% (红线A, 成立)
# ④ 平博掉水 → 不作红线 (17.1% 与基准持平, 白误杀41场)
# ⑤ 高命中联赛 → 仅🟢标记, 不作筛选项
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

def vb_dir(bv):
    """best_value.outcome(home/draw/away) → 主/平/客; 缺失返回 None"""
    return {'home': '主', 'draw': '平', 'away': '客'}.get(((bv or {}).get('outcome') or ''))


def avoid_reasons(m, bv=None):
    """返回 🚫避雷原因列表(可多个); 空=不打 🚫

    2026-09-24 用户拍板方案A —— 4764 场已结算复核(results.json):
      · edge>=15% 且 TS 反向  → 94.2% 败 / ROI -54.3% (n=537)   ← 唯一 🚫 触发条件
      · EV>=2 (= 赔率×edge)   → 94.8% 败, 仅作 edge 附注
      · kelly>=15% 不再单独触发: 剔除 edge 后 522 场败率 79.7% = 客胜池基准 78.6%,
        ROI +10.6% 纯噪音 (数学上 edge>=15% ⇒ kelly>=15%, 附注亦无信息量)
      · edge>=15% 且 TS 同向  → 命中 37.3% vs 隐含 12.4% / ROI +229.5% (n=67)
        2026-09-24 细化复核后升为 💡机会标: 各赔率档全正
        (<6: 50.0% n=8 | 6-10: 42.9% n=35 | 10-13: 25.0% n=16 | >=13: 25.0% n=8), 故不设赔率上限
        支持性大样本: TS同向且 edge<15% 亦为正 (n=895, 47.2% vs 27.9%, ROI +73.9%)
      · 2026-09-24 细化否掉的两条:
        (a) TS反向强度 gap=tsmax-自身TS概率 不加门槛 — 全池单调(<0.03:+22.2% → >=0.15:-49.0%),
            但在 edge>=15% 集内不成立: gap 0.03~0.08 (n=35, ROI -64.3%) 比整体(-51.0%)更雷,
            gap>=0.15 (n=433) 与 0.08~0.15 (n=60, -6.8%) 也比整体浅; 唯一无害的 gap<0.03 仅 9 场,
            剔掉只让 🚫 集 ROI 从 -51.0% 变 -52.1% => 不值得加规则
        (b) 赔率>=13 不单列 🚫 原因 — value池 159 场中 150 场(94%)已被 edge∧反向覆盖
            (那 150 场 2.7% 命中/ROI -58.6%, 比整体更雷), 余 9 场反而是 TS同向 💡机会场(+366.7%, n=9)
      · ⚡高权重 已降级: 前向 1065 场命中 48.1% vs 隐含 50.9% (ROI -7.1%), 只出 ⚠️⚡提示
    """
    bv = bv or (m.get('best_value') or {})
    d = bv.get('outcome')
    if not d or (bv.get('edge') or 0) < 0.15:
        return []
    # 注意: 本文件 argmax3 返回 '主/平/客', best_value.outcome 是 'home/draw/away' —— 必须译名后比
    tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
    if tsd == {'home': '主', 'draw': '平', 'away': '客'}.get(d):
        return []
    reasons = ['edge≥15%·TS反向']
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
        tsp = max(*_num3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0)))
        bv = m.get('best_value') or {}
        # 📐低熵区(2026-09-29 用户指令): 只读标记, 不参与筛场
        e = ent3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        o_dir = hkjc_dir_odds(cur, md)
        rows.append({
            'date': m.get('date', ''), 'mt': mt, 'league': t2s(m.get('event', '')),
            'home': t2s(m.get('home_team', '')), 'away': t2s(m.get('away_team', '')), 'no': no_tag(m),
            'fid': m.get('fid'),
            'odds': cur[2] if cur else None,
            'dir': md, 'model_prob': mv, 'lgbm_prob': lv, 'ev': bv.get('ev', 0),
            'ent': e, 'dir_odds': o_dir,  # 📐低熵区: LGBM三路熵 + 该方向HKJC即时赔率
            # 参考赔率: 平博开/即, HKJC开/即 (均为 主/平/客 三元组)
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'ts_dir': tsd, 'ts_prob': tsp,  # TS最大概率及方向
            'avoid': is_hw_avoid(m),  # ⚡高权重弱提示(2026-09-24 降级)
            'av_reasons': avoid_reasons(m, bv),  # 扩展避雷原因
            # 💡机会(2026-09-24): value方向 edge>=15% 且 TS 同向 → 高赔正期望
            'chance': bool(vb_dir(bv) and (bv.get('edge') or 0) >= 0.15 and vb_dir(bv) == tsd),
        })
    rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))

    # ========== 规则C: 🎯甜点区 — 2026-08-20 用户拍板取消(不参考), 清单不再输出 ==========
    # 历史: 2026-08-14 投注簿挖掘 27-43%/+38单位; 8/19 实际 3/10 30% -1.27; 大样本基准
    # (results.json 1830场): 客+赔率>=2.5 整体69.2%但美洲场仅47.6%, 用户决定整段移除
    sweet_rows = []

    print()
    print(f"①高置信方向投注 ({'全部' if show_all else '今日窗口(' + win_label + ')内及未来未开赛可投'} {len(rows)}场) 规则: model=LGBM方向一致(主/平/客) 且 一方概率>44.9% (2026-08-17新规格)")
    print("=" * 92)
    for r in rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        tag = ''
        if r.get('sweet'):
            tag = ' 🎯甜点'
        elif r.get('av_reasons'):
            tag = ' 🚫避雷(' + ','.join(r['av_reasons']) + ')'
        elif r.get('avoid'):
            tag = ' ⚠️⚡提示'
        if r.get('av_reasons') and r.get('avoid'):
            tag += '·⚡'  # 🚫与⚡同时命中: 保留 ⚡ 信息
        if r.get('chance'):
            tag += ' 💡机会(edge≥15·TS同向)'  # 与 🚫 互斥(反向/同向), 可与 ⚠️⚡提示 并存
        print(f"{t} [{lg_tag(r['league'])}] {r['home']} vs {r['away']} →{r['dir']}{tag}{r.get('no', '')}")
        ent = r.get('ent')
        ltag = ''
        if ent is not None:
            ltag = f" | 熵{ent:.3f}"
            if ent <= 1.075:
                ltag += ' 📐低熵'
                if r.get('dir_odds') and r['dir_odds'] >= 1.8:
                    ltag += '核心(赔≥1.8)'
            else:
                # 对偶标记(2026-09-30 立, 只标不筛): 熵>1.075 三段逐档 ROI 全负
                # (账本 1029 注 37.8% ROI-8.57%; 季度走前 4/4 段为负) → 负期望预警
                ltag += ' ⛔高熵(负期望段)'
        print(f"   {r['dir']}概率: model {r['model_prob']*100:.0f}% | LGBM {r['lgbm_prob']*100:.0f}% | EV {r['ev']:.2f} | TS {r['ts_dir']}{r['ts_prob']*100:.0f}%{ltag}")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not rows:
        if show_all:
            print("(全部场次无符合条件者)")
        else:
            print(f"(今日窗口 {win_label} 内及未来无未开赛可投场次)")

    # ===== 📐低熵核心区: ①段末指针 (2026-09-29 用户拍板「单独出一段」→ 明细在清单末尾独立一段) =====
    # 只标只报, 不改任何筛场规则(① 仍然按 model=LGBM同向 且 >44.9% 出)
    low = [r for r in rows if r.get('ent') is not None and r['ent'] <= 1.075]
    core = [r for r in low if r.get('dir_odds') and r['dir_odds'] >= 1.8]
    high = [r for r in rows if r.get('ent') is not None and r['ent'] > 1.075]
    if low or high:
        print("=" * 92)
        print(f"📐低熵区: {len(low)}场(熵≤1.075) | 其中核心(该方向HKJC即时≥1.8) {len(core)}场"
              f" → 明细见清单末尾「📐低熵核心区」独立一段(只跟踪)")
        if high:
            print(f"⛔高熵负期望段: {len(high)}场(熵>1.075) — 账本1029注命中37.8% ROI-8.57%, "
                  f"①池1263场(占①池53.8%)命中51.7% ROI-12.22% 且贡献①池76%亏损额; "
                  f"月度4/4段为负; 只标不筛, 跟不跟由用户自判")

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
        ts_draw = m.get('ts_draw') or 0
        is_fill = abs(ts_draw - 0.241) < 0.001  # TS填充值污染剔除
        # 2026-09-01 用户拍板: ★门限 TS平<25% 收紧到 <22% (回测: 命中率83% vs 25%的75.8%, 单注EV更高)
        star = (cur[2] < 2.0 and ts_draw < 0.22 and not is_fill)
        comp = m.get('comparison') or {}
        tsp = max(*_num3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0)))
        rows_b.append({
            'date': m.get('date', ''), 'mt': mt, 'league': t2s(m.get('event', '')),
            'home': t2s(m.get('home_team', '')), 'away': t2s(m.get('away_team', '')), 'no': no_tag(m),
            'fid': m.get('fid'),
            'odds': cur[2], 'ts_draw': ts_draw, 'star': star,
            'ts_dir': tsd, 'ts_prob': tsp,  # TS最大概率方向及概率
            'model_prob': m.get('model_loss', 0),  # 客客客: 模型指客概率
            'ev': (m.get('best_value') or {}).get('ev', 0),
            'lgbm_prob': max(*_num3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))),
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'avoid': is_hw_avoid(m),  # ⚡高权重弱提示(2026-09-24 降级)
        })
    # 高置信优先, 再按时间
    rows_b.sort(key=lambda x: (not x['star'], x['mt'] or datetime.datetime.max))

    print()
    print("=" * 92)
    print(f"②三方一致·客客客 ({'全部' if show_all else '今日窗口内及未来未开赛可投'} {len(rows_b)}场) 规则: model=LGBM=TS均指客 | ★=客赔<2.0且TS平<22%")
    print("=" * 92)
    for r in rows_b:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        star = " ★" if r['star'] else ""
        # 2026-09-01 用户拍板: ★场次豁免过滤(★=方向高置信>⚡避雷), 带★不标⚠️⚡
        av = ' ⚠️⚡提示' if (r.get('avoid') and not r['star']) else ''
        print(f"{t} [{lg_tag(r['league'])}] {r['home']} vs {r['away']}{star}{av}{r.get('no', '')}")
        print(f"   HKJC客胜 {r['odds']} | 模型概率 {r['model_prob']*100:.0f}% | LGBM客概率 {r['lgbm_prob']*100:.0f}% | EV {r['ev']:.2f} | TS{r['ts_dir']} {r['ts_prob']*100:.0f}%")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not rows_b:
        if show_all:
            print("(全部场次无符合条件者)")
        else:
            print(f"(今日窗口 {win_label} 内及未来无未开赛三方一致客场次)")

    # ========== 规则D: 三方一致·胜胜胜 (客客客镜像, 主胜方向) ==========
    rows_d = []
    for m in MS:
        if not in_window(m):
            continue
        md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = argmax3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        if not (md == ld == tsd == '主'):
            continue  # 三方一致且指主(胜)
        cur = hkjc_cur(m)
        if not cur:
            continue
        mt = parse_dt(m.get('match_time') or m.get('date'))
        ts_draw = m.get('ts_draw') or 0
        is_fill = abs(ts_draw - 0.241) < 0.001  # TS填充值污染剔除
        # 2026-09-01 用户拍板: ★门限 TS平<25% 收紧到 <22% (回测: 命中率83% vs 25%的75.8%, 单注EV更高)
        star = (cur[0] < 2.0 and ts_draw < 0.22 and not is_fill)  # 主赔<2.0
        comp = m.get('comparison') or {}
        tsp = max(*_num3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0)))
        rows_d.append({
            'date': m.get('date', ''), 'mt': mt, 'league': t2s(m.get('event', '')),
            'home': t2s(m.get('home_team', '')), 'away': t2s(m.get('away_team', '')), 'no': no_tag(m),
            'fid': m.get('fid'),
            'odds': cur[0], 'ts_draw': ts_draw, 'star': star,
            'ts_dir': tsd, 'ts_prob': tsp,  # TS最大概率方向及概率
            'model_prob': m.get('model_win', 0),  # 胜胜胜: 模型指主概率
            'ev': (m.get('best_value') or {}).get('ev', 0),
            'lgbm_prob': max(*_num3(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))),
            'pin_open': fmt3(comp.get('open')), 'pin_cur': fmt3(comp.get('current')),
            'hkjc_open': fmt3((m.get('pin_comparison') or {}).get('open')), 'hkjc_cur': fmt3(cur),
            'avoid': is_hw_avoid(m),  # ⚡高权重弱提示(2026-09-24 降级)
        })
    # 高置信优先, 再按时间
    rows_d.sort(key=lambda x: (not x['star'], x['mt'] or datetime.datetime.max))

    print()
    print("=" * 92)
    print(f"③三方一致·胜胜胜 ({'全部' if show_all else '今日窗口内及未来未开赛可投'} {len(rows_d)}场) 规则: model=LGBM=TS均指主 | ★=主赔<2.0且TS平<22%")
    print("=" * 92)
    for r in rows_d:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        star = " ★" if r['star'] else ""
        # 2026-09-01 用户拍板: ★场次豁免过滤(★=方向高置信>⚡避雷), 带★不标⚠️⚡
        av = ' ⚠️⚡提示' if (r.get('avoid') and not r['star']) else ''
        print(f"{t} [{lg_tag(r['league'])}] {r['home']} vs {r['away']}{star}{av}{r.get('no', '')}")
        print(f"   HKJC主胜 {r['odds']} | 模型概率 {r['model_prob']*100:.0f}% | LGBM主概率 {r['lgbm_prob']*100:.0f}% | EV {r['ev']:.2f} | TS{r['ts_dir']} {r['ts_prob']*100:.0f}%")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")
    if not rows_d:
        if show_all:
            print("(全部场次无符合条件者)")
        else:
            print(f"(今日窗口 {win_label} 内及未来无未开赛三方一致主场次)")

    # 2026-09-23 用户拍板: 撤销「避雷汇总」段
    # 避雷场次照推 —— 不再单独汇总/警示, 只在各档位明细行保留 🚫/⚠️⚡ 标记, 是否跟由用户自判

    # ===== 📐低熵核心区: 独立一段 (2026-09-29 用户拍板「好 单独出一段。」) =====
    # 口径: ① 的子集 —— 熵≤1.075(=该方向模型概率≳0.45, 三路不模糊) 且 该方向 HKJC 即时赔 ≥1.8
    # 只展示/只跟踪: 不改任何筛场规则, 不重复计入投注簿与战绩(①里已有这些场次), 供逐周跟踪
    core_rows = [r for r in rows
                 if r.get('ent') is not None and r['ent'] <= 1.075
                 and r.get('dir_odds') and r['dir_odds'] >= 1.8]
    core_rows.sort(key=lambda x: (x['mt'] or datetime.datetime.max, -x['ev']))
    print()
    print(f"📐低熵核心区 (熵≤1.075 ∧ 该方向HKJC即时≥1.8 — ①的子集, 只跟踪): {len(core_rows)}场")
    print("   规则不动: ①仍按 model=LGBM同向 且 >44.9% 出; 本段不重复计入战绩/投注簿(同场只记①那一条)")
    print("   回测(2026-09-30 重算, results.json ①池2348场 HKJC赔口径): 54场 59.3% 均赔1.96 ROI+16.5%")
    print("   (①池整池 ROI-8.65%; 本子集唯一为正 — 命中≈同场市场热门, 收益来自赔≥1.8 定价; 样本54场, 只跟踪)")
    print("=" * 92)
    if not core_rows:
        print("(今日无核心场: ①内无「熵≤1.075 且该方向赔≥1.8」的场次)")
    for r in core_rows:
        t = r['mt'].strftime('%m-%d %H:%M') if r['mt'] else r['date']
        tag = ''
        if r.get('av_reasons'):
            tag = ' 🚫避雷(' + ','.join(r['av_reasons']) + ')'
        elif r.get('avoid'):
            tag = ' ⚠️⚡提示'
        if r.get('chance'):
            tag += ' 💡机会(edge≥15·TS同向)'
        print(f"{t} [{lg_tag(r['league'])}] {r['home']} vs {r['away']} →{r['dir']}{tag}{r.get('no', '')}")
        print(f"   {r['dir']}概率: model {r['model_prob']*100:.0f}% | LGBM {r['lgbm_prob']*100:.0f}%"
              f" | EV {r['ev']:.2f} | TS {r['ts_dir']}{r['ts_prob']*100:.0f}%"
              f" | 熵{r['ent']:.3f} 📐低熵 核心(赔{r['dir_odds']:.2f}≥1.8)")
        print(f"   平博 初/即: {r['pin_open']} → {r['pin_cur']} | HKJC 初/即: {r['hkjc_open']} → {r['hkjc_cur']}")

    # ===== 📋战术阵容参考: 只读展示 (2026-09-30 用户拍板 A档) =====
    # 数据: match_formations/match_lineups(fetch_lineups.py 回填) + xg_features + match_analysis.h2h
    # 规则不动: 不参与①入选, 不动 LGBM/泊松/EV/避雷; 段头用 📋 打头(非①②③), 三解析器天然不计入
    try:
        import os as _os_, sys as _sys_
        _sys_.path.insert(0, _os_.path.dirname(_os_.path.abspath(__file__)))
        import lineup_ref
        _seen = []
        for _r in rows + rows_b + rows_d:
            _f = _r.get('fid')
            if _f and _f not in _seen:
                _seen.append(_f)
        _ref = lineup_ref.collect(_seen, max_rows=40)
    except Exception as _ex:
        _ref = []
        print(f"(📋战术阵容参考生成跳过: {_ex})")
    print()
    if _ref:
        for _l in _ref:
            print(_l)
    else:
        print("▫️战术阵容参考: 今日窗口内无阵容数据 (titan007 详情页未出阵容/未回填; U21与低级别联赛常见)")

    if md_file:
        sys.stdout.write("```\n")
        sys.stdout.flush()
        sys.stdout = sys.__stdout__  # 先恢复, 避免解释器退出时flush已关闭文件
        md_file.close()

if __name__ == '__main__':
    main()
