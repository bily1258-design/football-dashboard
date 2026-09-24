#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一投注簿 (Betting Ledger) — 2026-08-12 新增
================================================
把零散的推荐信号整合成一个「推荐 → 结算 → 战绩」闭环账本:

信号源 (从 results.json 提取):
  1. 价值投注: best_value.ev > 0.05 且 edge > 0.02 (双门槛, 2026-08-12 起)
  2. 客胜规则A: best_value.outcome==away 且 ev>0.5 (经HKJC回测)
  3. ⚡高权重: importance_weight >= 1.14 (避雷信号追踪)
  4. 模型方向: M最大概率方向 — 已砍 (2026-08-15 回测: 命中率51.9%但亏-50.83单位, 赔率端无错误定价)
  5. 🍬甜点区: 双模型同向(主主/客客) + 方向赔率≥2.0 + M概率≥45% — 已砍 (2026-08-16 回测130场实际结算+0.24打平, 甜点优先限额-6.46负优化, 不加入投注, 仅保留看板筛选)
  (升水信号已于 2026-08-12 砍掉: 胜率44.9%却亏36.99单位, 低赔率陷阱)

结算: score 非空 → 解析胜平负 → win/loss → profit (1单位本金)
输出: docs/data/betting_ledger.json + 控制台统计

用法:
  python3 scripts/betting_ledger.py            # 更新账本(去重+回填)
  python3 scripts/betting_ledger.py --stats    # 只显示统计
"""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')
LEDGER = os.path.join(ROOT, 'docs', 'data', 'betting_ledger.json')

# 信号参数
EV_MIN = 0.05          # 价值投注 EV 门槛
EDGE_MIN = 0.02        # edge 门槛 (2026-08-12 双门槛)
AWAY_EV_MIN = 0.5      # 规则A: 客胜 EV>0.5
WEIGHT_MIN = 1.14      # ⚡高权重门槛
# 2026-09-24 用户拍板(方案③): value/ruleA 限赔率 ≤5.0
# 依据: 实测"宣称 edge/EV 无预测力"——宣称 edge 0.10-0.20 (n=229) 实际 -1.7pp,
# edge>0.20 (n=63) 实际 -2.6pp (越高越差); 全月皆负 (6月-0.2pp/7月-3.2pp/8月-1.8pp/9月-1.6pp);
# 且赔率 1.0-5.0 以外的档位 ROI 全负。故对 value 与 ruleA(同一深盘冷门宇宙)一并限赔率。
VALUE_ODDS_MAX = 5.0
VALUE_ODDS_MAX_SIGNALS = ('value', 'ruleA')
TOP_N_PER_DAY = 3      # 每日限额: 每天只记 EV 最高的 N 场 (2026-08-15 新增; 回测 top1 +4.94 / top3 -0.45, 取3均衡样本量)
SAME_MATCH_KEEP = 'value'  # 同场双计修正: 同一 fid 同时有 weight(⚡大热)与 value(价值)时只记一条, 取哪侧 ('weight'|'value')

LABELS = {'home': '主胜', 'draw': '平局', 'away': '客胜'}

# ── 真价值口径 (2026-09-24 方案②, 与 ai_analysis._get_true_value 对齐) ──
TRUE_EDGE_MIN = 0.03
# 只从该日起记录 truev — 门槛在 06-26~09-23 上定出, 回填历史=样本内自证 (要全史视图就改这里)
TRUEV_START = '2026-09-25'
# 同一场只算一注的「价值侧」信号 (2026-09-16 起 value/ruleA 互斥; 2026-09-24 并入 truev)
VALUE_SIDE = ('value', 'ruleA', 'truev')


def parse_score(s):
    """'3-3' / '2 - 1' / '2:1' → (3,3); 无法解析返回 None"""
    if not s:
        return None
    s = str(s).strip().replace('：', ':')
    import re
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def score_to_outcome(score_tuple, outcome):
    """比分 → 该投注是否赢 (home/draw/away)"""
    if score_tuple is None:
        return None
    h, a = score_tuple
    if outcome == 'home':
        return h > a
    if outcome == 'away':
        return a > h
    if outcome == 'draw':
        return h == a
    return None


def collect_signals(matches, top_n=TOP_N_PER_DAY):
    """从 results.json 每场提取所有信号; 按天限额: 每天只取 EV 最高的 top_n 场投注信号
    (weight 避雷信号不受限额, 始终全量记录)"""
    raw = []  # (day, fid, ev, signals_of_match)
    for m in matches:
        fid = m.get('fid', '')
        teams = f"{m.get('home_team', '')} vs {m.get('away_team', '')}"
        mt = m.get('match_time') or m.get('date', '')
        score = m.get('score', '')
        day = str(mt)[:10]

        match_signals = []

        # ── 信号1: 真价值投注 (2026-09-24 方案②: 真edge≥0.03 ∧ TS同向, 全天候选号) ──
        # 旧 value 口径(best_value: ev>5% ∧ edge>2%, 未去水) 已停发, 历史行保留为凭证:
        #   旧口径同日大样本 n=3962 命中 22.6% / 均赔 5.31 / ROI −3.7% (虚 edge 均值 9.3pp)
        #   新口径 66 天 n=332 命中 68.1% / fair 隐含 48.2% → 超额 +19.9pp / ROI +33.9% / 日均 5.0
        # TRUEV_START: 只记起点之后的场次 — 门槛是在 06-26~09-23 上定出来的, 回填历史=样本内自证
        tv = m.get('true_value') or {}
        if (tv.get('outcome') and (tv.get('true_edge') or 0) >= TRUE_EDGE_MIN
                and day >= TRUEV_START):
            match_signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'truev', 'signal_cn': '真价值',
                'outcome': tv['outcome'], 'odds': tv.get('odds', 0),
                'ev': tv.get('ev', 0), 'edge': tv.get('true_edge', 0),
                'true_edge': tv.get('true_edge', 0), 'fair': tv.get('fair'),
                'kelly': tv.get('kelly', 0),
            })

        # ── 信号2: 客胜规则A (同批修正: 与 value 同一深盘冷门宇宙, 一并限赔率) ──
        bv = m.get('best_value') or {}
        _bv_odds = bv.get('odds', 0) or 0
        if (bv.get('outcome') == 'away' and bv.get('ev', 0) > AWAY_EV_MIN
                and 1.0 < _bv_odds <= VALUE_ODDS_MAX):
            match_signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'ruleA', 'signal_cn': '客胜规则A',
                'outcome': 'away', 'odds': bv.get('odds', 0),
                'ev': bv.get('ev', 0), 'edge': bv.get('edge', 0),
                'kelly': bv.get('kelly', 0),
            })

        # ── 信号3: ⚡高权重 (避雷, 不受限额) ──
        w = m.get('importance_weight', 0) or 0
        if w >= WEIGHT_MIN:
            # 避雷方向: 模型==TS 同向
            def argmax3(w_, dr_, l_):
                mx = max(w_, dr_, l_)
                return 0 if mx == w_ else (1 if mx == dr_ else 2)
            md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
            tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
            # 避雷方向赔率 (2026-08-15 修复: 之前 odds=0 导致胜也记 -1)
            w_dir = ['home', 'draw', 'away'][md]
            w_odds = {'home': m.get('odds_win', 0), 'draw': m.get('odds_draw', 0), 'away': m.get('odds_loss', 0)}.get(w_dir, 0) or 0
            match_signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'weight', 'signal_cn': '⚡高权重',
                'outcome': w_dir, 'odds': w_odds, 'ev': 0, 'edge': 0, 'kelly': 0,
                'weight': w, 'same_dir': (md == tsd),
            })

        # ── 信号4: 模型方向 (M最大概率) — 2026-08-15 已砍 ──
        # 回测: 1389场 胜率51.9% 利润-50.83; 赔率≥2.0仍-5.77, 庄家定价已吃掉模型判断
        # 口径验证(中间概率→最大概率): 命中率24.9%→51.9%, 平局癌63.8%→3.5%
        # 结论: 方向准≠下注赚钱, 该信号是负资产, 与升水信号同批弃用

        # ── 信号5: 🍬甜点区 — 2026-08-16 回测后砍除(不加入投注) ──
        # 规则: 双模型同向(主主/客客, 排除和和) + 方向赔率≥2.0 + M方向概率≥45%
        # 回测: 130场 命中46.2% 期望EV+7.0%, 但实际结算利润+0.24(打平, 7月-5.22/8月+4.53)
        # 验证: 甜点区优先入选每日限额 = -6.46 (vs 纯EV限额 +29.28), 挤掉ruleA/value真利润
        # 联赛区分: 中小联赛+6.58 vs 欧战-3.74(先验), 样本小(63场)仅作看板筛选参考
        # 结论: 期望正但实际打平, 非负资产但无超额收益; 保留看板组合筛选(主主/客客), 不投注


        # ── 2026-09-16 修复: 同一场次价值侧信号同时命中会重复计入 (2026-09-24 并入 truev) ──
        # (ruleA 是 value 的客胜子集; truev 是新口径, 与旧口径同场时保留旧行不双计)
        # 2026-09-24: value 停发后 ruleA 会「浮出」(ruleA ⊆ 旧 value 门槛: edge=EV/odds>2% 恒成立)
        #   → 保持原屏蔽语义: 凡通过旧 value 门槛的场次, ruleA 照旧不记 (否则账本凭空多一批 ruleA 行)
        _old_gate = (bv.get('outcome') and bv.get('ev', 0) > EV_MIN and bv.get('edge', 0) > EDGE_MIN
                     and 1.0 < _bv_odds <= VALUE_ODDS_MAX)
        if _old_gate and any(s['signal'] == 'ruleA' for s in match_signals):
            match_signals = [s for s in match_signals if s['signal'] != 'ruleA']
        _side = [s for s in match_signals if s['signal'] in VALUE_SIDE]
        if len(_side) > 1:
            # 优先级: value(旧口径历史行) > truev(新口径) > ruleA; 均无则取首条
            keep = (next((s for s in _side if s['signal'] == 'value'), None)
                    or next((s for s in _side if s['signal'] == 'truev'), None)
                    or _side[0])
            extra = [s['signal'] for s in _side if s is not keep]
            if extra:
                keep['signal_extra'] = extra
            match_signals = [s for s in match_signals if s is keep or s['signal'] not in VALUE_SIDE]

        if match_signals:
            ev = max((s.get('ev', 0) or 0) for s in match_signals)
            raw.append((day, fid, ev, match_signals))

    # 按天分组 → 每天按 EV 排序 → 只取 top_n 场 (weight 全量保留)
    from collections import defaultdict
    by_day = defaultdict(list)
    for day, fid, ev, sigs in raw:
        by_day[day].append((ev, sigs))

    signals = []
    for day, items in sorted(by_day.items()):
        items.sort(key=lambda x: -x[0])  # EV 降序
        for ev, sigs in items[:top_n]:
            signals.extend(sigs)
        # weight/truev 信号不受限额: weight=避雷全量; truev=真价值全天候选号(回测口径无每日上限)
        for ev, sigs in items[top_n:]:
            for s in sigs:
                if s['signal'] in ('weight', 'truev'):
                    signals.append(s)
    return signals


def load_ledger():
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_ledger(entries):
    with open(LEDGER, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def main():
    stats_only = '--stats' in sys.argv
    breakdown = '--breakdown' in sys.argv
    top_n = TOP_N_PER_DAY
    if '--top' in sys.argv:
        try:
            top_n = int(sys.argv[sys.argv.index('--top') + 1])
        except (ValueError, IndexError):
            pass

    with open(RESULTS, 'r', encoding='utf-8') as f:
        matches = json.load(f)['matches']

    # 读取现有账本 (按 fid+signal 去重)
    ledger = load_ledger()

    # ── 2026-09-24 用户拍板(方案③): value/ruleA 限赔率 ≤5.0 (含存量修正) ──
    # 账本是增量式(load→append→save), 只加过滤不改历史 → 存量超限记录须显式剔除,
    # 否则本次规则对已有战绩毫无影响。剔除 = 该场不再视为投注(与"不下注"同义);
    # 旧账本可从 git 历史取回。
    if VALUE_ODDS_MAX and VALUE_ODDS_MAX > 0:
        before = len(ledger)
        pruned_detail = {}
        kept_v = []
        for e in ledger:
            if (e.get('signal') in VALUE_ODDS_MAX_SIGNALS
                    and (e.get('odds') or 0) > VALUE_ODDS_MAX):
                k = e.get('signal')
                pruned_detail[k] = pruned_detail.get(k, 0) + 1
                continue
            kept_v.append(e)
        ledger = kept_v
        pruned = before - len(ledger)
    else:
        pruned = 0
        pruned_detail = {}

    # ── 2026-09-16 修复结算断链 ──
    # 旧逻辑: (fid, signal) 已存在即 continue, 采集时 score 为空的记录永不再刷新比分,
    # 末尾补结算又要求 score 非空 → 已完赛场次永远待结算(实测 980 条 / 965 条其实早有比分)。
    # 现在每次运行都用 results.json 最新比分回填账本缺失的 score。
    score_by_fid = {}
    for m in matches:
        sc = m.get('score')
        if sc:
            score_by_fid[str(m.get('fid', ''))] = sc
    refreshed = 0
    for e in ledger:
        if not e.get('score'):
            sc = score_by_fid.get(str(e.get('fid', '')))
            if sc:
                e['score'] = sc
                refreshed += 1

    # ── 2026-09-16 口径修正: 同场重复投注去重 (value/ruleA 曾各记一条; 2026-09-24 并入 truev) ──
    dedup = {}
    kept = []
    dropped = 0
    for e in ledger:
        if e.get('signal') in VALUE_SIDE:
            k = str(e.get('fid', ''))
            if k in dedup:
                prev = dedup[k]
                if e.get('signal') not in (prev.get('signal_extra') or []):
                    prev.setdefault('signal_extra', []).append(e['signal'])
                # 重复条已结算而保留条未结算 → 迁移结算结果(避免丢战绩)
                if not prev.get('result') and e.get('result'):
                    for f in ('score', 'result', 'profit', 'settled_at', 'odds'):
                        if e.get(f) not in (None, ''):
                            prev[f] = e[f]
                dropped += 1
                continue
            dedup[k] = e
        kept.append(e)
    if dropped:
        ledger = kept

    seen = {(e['fid'], e['signal']) for e in ledger}
    # 已存在价值侧行的 fid: truev 不重复记 (同一场只保留一条, 旧口径行优先)
    _fid_side = {str(e.get('fid', '')) for e in ledger if e.get('signal') in VALUE_SIDE}

    new_count = 0
    settled = 0
    for sig in collect_signals(matches, top_n):
        key = (sig['fid'], sig['signal'])
        if key in seen:
            continue
        if sig['signal'] == 'truev' and str(sig['fid']) in _fid_side:
            continue

        # 结算 (若有比分)
        if sig.get('score'):
            st = parse_score(sig['score'])
            won = score_to_outcome(st, sig['outcome'])
            if won is not None:
                odds = sig.get('odds', 0)
                sig['result'] = 'win' if won else 'loss'
                sig['profit'] = round(odds - 1, 3) if won else -1.0
                sig['settled_at'] = datetime.now().isoformat()
                settled += 1

        # 结算已完赛但未标记 result 的历史场次
        if not sig.get('result') and sig.get('score'):
            st = parse_score(sig['score'])
            won = score_to_outcome(st, sig['outcome'])
            if won is not None:
                odds = sig.get('odds', 0)
                sig['result'] = 'win' if won else 'loss'
                sig['profit'] = round(odds - 1, 3) if won else -1.0
                sig['settled_at'] = datetime.now().isoformat()
                settled += 1

        ledger.append(sig)
        seen.add(key)
        new_count += 1

    # 回填: 已有记录但未结算且现在有比分
    for e in ledger:
        if e.get('result') or not e.get('score'):
            continue
        st = parse_score(e['score'])
        won = score_to_outcome(st, e['outcome'])
        if won is not None:
            odds = e.get('odds', 0)
            e['result'] = 'win' if won else 'loss'
            e['profit'] = round(odds - 1, 3) if won else -1.0
            e['settled_at'] = datetime.now().isoformat()
            settled += 1

    # ── 2026-09-24 用户拍板: 同场只记一条 (取 weight/⚡大热侧) ──
    # 同场双计: 同一 fid 的 value(当时为深盘冷门) 与 weight(⚡大热) 各记一条、方向实测 100% 相反
    # (43/43 组), 战绩里既双算又互相抵消。修正 = 每场只留一条计入战绩。
    # 口径: 留行 + 打标记 + 不计战绩 (不删行、不改历史数值 → 可对账、可回退);
    #       标记每次运行重算, 改 SAME_MATCH_KEEP 即可翻转取哪侧 (单纯改常数+重跑)。
    for _e in ledger:
        _e.pop('dedup_same_match', None)
        _e.pop('dedup_kept', None)
    _by_fid = {}
    for _e in ledger:
        _by_fid.setdefault(str(_e.get('fid', '')), []).append(_e)
    same_match_pairs = 0
    same_match_dropped = 0
    for _fid, _rows in _by_fid.items():
        _w = [r for r in _rows if r.get('signal') == 'weight']
        _v = [r for r in _rows if r.get('signal') in VALUE_SIDE]
        if not (_w and _v):
            continue
        if _w[0].get('outcome') == _v[0].get('outcome'):
            continue  # 同向不属双计
        same_match_pairs += 1
        for _r in (_v if SAME_MATCH_KEEP == 'weight' else _w):
            _r['dedup_same_match'] = True
            _r['dedup_kept'] = SAME_MATCH_KEEP
            same_match_dropped += 1

    save_ledger(ledger)

    # ── 统计 ──
    # 2026-09-23 用户拍板(撤销避雷汇总, 避雷场次照推): ⚡高权重(weight) 并入战绩统计
    # (旧口径 2026-09-16: weight 单列"避雷追踪标记, 非投注, 不计入战绩")
    bets = [e for e in ledger if not e.get('dedup_same_match')]
    # ⚡追踪与战绩同population: 同场双计被剔除的 weight 行也不进⚡组, 否则两处口径打架
    wtrack = [e for e in bets if e.get('signal') == 'weight']
    completed = [e for e in bets if e.get('result') in ('win', 'loss')]
    wins = [e for e in completed if e['result'] == 'win']
    losses = [e for e in completed if e['result'] == 'loss']
    pending = [e for e in bets if not e.get('result')]

    print('═' * 50)
    print('📒 统一投注簿')
    print('═' * 50)
    print(f'总记录: {len(ledger)} (其中⚡高权重 {len(wtrack)}; 新增 {new_count}, '
          f'本次补比分 {refreshed}, 去重 {dropped}, 结算 {settled})')
    if pruned:
        print(f'③ value 限赔率 ≤{VALUE_ODDS_MAX}: 剔除存量超限 {pruned} 条 '
              f'({", ".join(f"{k}:{v}" for k, v in pruned_detail.items())})')
    if same_match_dropped:
        print(f'同场双计修正: 剔除 {same_match_dropped} 条 (取 {SAME_MATCH_KEEP} 侧, 反向对 {same_match_pairs} 组; '
              f'留行标 dedup_same_match, 不计战绩)')
    print(f'已结算: {len(completed)}  待结算: {len(pending)}')
    if completed:
        total_profit = sum(e.get('profit', 0) for e in completed)
        win_rate = len(wins) / len(completed) * 100
        print(f'胜: {len(wins)}  负: {len(losses)}')
        print(f'胜率: {win_rate:.1f}%  总利润: {total_profit:+.2f} 单位 (1单位/注)')
        # 按信号类型统计
        print()
        print('按信号类型:')
        by_signal = {}
        for e in completed:
            s = e.get('signal_cn', e.get('signal', '?'))
            by_signal.setdefault(s, {'n': 0, 'w': 0, 'profit': 0.0})
            by_signal[s]['n'] += 1
            by_signal[s]['w'] += 1 if e['result'] == 'win' else 0
            by_signal[s]['profit'] += e.get('profit', 0)
        for s, st in sorted(by_signal.items(), key=lambda x: -x[1]['n']):
            wr = st['w'] / st['n'] * 100
            print(f"  {s:10s} {st['n']:3d}场  胜率{wr:5.1f}%  利润{st['profit']:+7.2f}")
        # 月度
        print()
        print('按月:')
        by_month = {}
        for e in completed:
            d = (e.get('match_time') or '')[:7]
            if not d:
                continue
            by_month.setdefault(d, {'n': 0, 'w': 0, 'profit': 0.0})
            by_month[d]['n'] += 1
            by_month[d]['w'] += 1 if e['result'] == 'win' else 0
            by_month[d]['profit'] += e.get('profit', 0)
        for d, st in sorted(by_month.items()):
            wr = st['w'] / st['n'] * 100 if st['n'] else 0
            print(f"  {d}  {st['n']:3d}场  胜率{wr:5.1f}%  利润{st['profit']:+7.2f}")

    if breakdown:
        print()
        print('信号 × 方向明细:')
        by_sig_dir = {}
        for e in completed:
            s = e.get('signal_cn', e.get('signal', '?'))
            out = LABELS.get(e.get('outcome'), e.get('outcome', '?'))
            key = (s, out)
            by_sig_dir.setdefault(key, {'n': 0, 'w': 0, 'profit': 0.0})
            by_sig_dir[key]['n'] += 1
            by_sig_dir[key]['w'] += 1 if e['result'] == 'win' else 0
            by_sig_dir[key]['profit'] += e.get('profit', 0)
        for (s, d), st in sorted(by_sig_dir.items(), key=lambda x: -x[1]['n']):
            wr = st['w'] / st['n'] * 100 if st['n'] else 0
            print(f"  {s:8s} {d:8s} {st['n']:4d}场  胜率{wr:5.1f}%  利润{st['profit']:+8.2f}")

    wcomp = [e for e in wtrack if e.get('result') in ('win', 'loss')]
    if wcomp:
        ww = sum(1 for e in wcomp if e['result'] == 'win')
        wp = sum(e.get('profit', 0) for e in wcomp)
        print()
        share = f'  占已结算{len(wcomp) / len(completed) * 100:.0f}%' if completed else ''
        print('⚡高权重追踪 (避雷标记, 已并入上方战绩):')
        print(f"  {len(wcomp):4d}场  命中率{ww / len(wcomp) * 100:5.1f}%  " f'盈亏{wp:+.2f}{share}')

    # ── 影子追踪 (2026-09-16 立): 按赔率档只观察, 不改任何选号规则 ──
    if completed:
        def _band(o):
            return ('<2' if o < 2 else '2-4' if o < 4 else '4-8' if o < 8
                    else '8-15' if o < 15 else '15-30' if o < 30 else '30+')

        # 市场基准: 平博三向赔率按同档统计实际命中率 (不掺模型选向)
        mkt = {}
        for m in matches:
            sc = m.get('score')
            ow, odr, ol = m.get('odds_win'), m.get('odds_draw'), m.get('odds_loss')
            if not sc or not ow or not odr or not ol:
                continue
            s2 = parse_score(sc)
            if not s2:
                continue
            for o, oc in ((ow, 'home'), (odr, 'draw'), (ol, 'away')):
                hit = score_to_outcome(s2, oc)
                if hit is None:
                    continue
                b = mkt.setdefault(_band(o), {'n': 0, 'w': 0, 'so': 0.0})
                b['n'] += 1
                b['w'] += 1 if hit else 0
                b['so'] += o

        bands = {}
        for e in completed:
            o = e.get('odds') or 0
            if o <= 0:
                continue
            b = bands.setdefault(_band(o), {'n': 0, 'w': 0, 'so': 0.0, 'p': 0.0})
            b['n'] += 1
            b['w'] += 1 if e['result'] == 'win' else 0
            b['so'] += o
            b['p'] += e.get('profit', 0)

        if bands:
            print()
            print('🔬 影子追踪 (按赔率档观察, 不改选号规则):')
            print('  档      注数  命中率   保本线   市场实际    ROI      盈亏')
            for k in ('<2', '2-4', '4-8', '8-15', '15-30', '30+'):
                b = bands.get(k)
                if not b:
                    continue
                ao = b['so'] / b['n']
                mk = mkt.get(k) or {'n': 0, 'w': 0}
                mk_rate = 100 * mk['w'] / mk['n'] if mk['n'] else 0.0
                roi = 100 * b['p'] / b['n']
                wr = 100 * b['w'] / b['n']
                flag = ''
                if b['n'] >= 30 and mk['n'] >= 100 and (wr - mk_rate) <= -8:
                    flag = '  ⚠️持续落后市场'
                print(f"  {k:6s} {b['n']:4d}  {wr:5.1f}%  {100 / ao:7.1f}%  "
                      f"{mk_rate:7.1f}%  {roi:+7.1f}%  {b['p']:+8.2f}{flag}")

    print()
    print(f'账本文件: docs/data/betting_ledger.json')


if __name__ == '__main__':
    main()
