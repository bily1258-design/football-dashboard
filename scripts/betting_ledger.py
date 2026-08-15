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
TOP_N_PER_DAY = 3      # 每日限额: 每天只记 EV 最高的 N 场 (2026-08-15 新增; 回测 top1 +4.94 / top3 -0.45, 取3均衡样本量)

LABELS = {'home': '主胜', 'draw': '平局', 'away': '客胜'}


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

        # ── 信号1: 价值投注 (双门槛) ──
        bv = m.get('best_value') or {}
        if bv.get('outcome') and bv.get('ev', 0) > EV_MIN and bv.get('edge', 0) > EDGE_MIN:
            match_signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'value', 'signal_cn': '价值投注',
                'outcome': bv['outcome'], 'odds': bv.get('odds', 0),
                'ev': bv.get('ev', 0), 'edge': bv.get('edge', 0),
                'kelly': bv.get('kelly', 0),
            })

        # ── 信号2: 客胜规则A ──
        if bv.get('outcome') == 'away' and bv.get('ev', 0) > AWAY_EV_MIN:
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
        # weight 信号不受限额: 从所有场次里补上
        for ev, sigs in items[top_n:]:
            for s in sigs:
                if s['signal'] == 'weight':
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
    seen = {(e['fid'], e['signal']) for e in ledger}

    new_count = 0
    settled = 0
    for sig in collect_signals(matches, top_n):
        key = (sig['fid'], sig['signal'])
        if key in seen:
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

    save_ledger(ledger)

    # ── 统计 ──
    completed = [e for e in ledger if e.get('result') in ('win', 'loss')]
    wins = [e for e in completed if e['result'] == 'win']
    losses = [e for e in completed if e['result'] == 'loss']
    pending = [e for e in ledger if not e.get('result')]

    print('═' * 50)
    print('📒 统一投注簿')
    print('═' * 50)
    print(f'总记录: {len(ledger)} (新增 {new_count}, 本次结算 {settled})')
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

    print()
    print(f'账本文件: docs/data/betting_ledger.json')


if __name__ == '__main__':
    main()
