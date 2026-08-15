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


def collect_signals(matches):
    """从 results.json 每场提取所有信号"""
    signals = []
    for m in matches:
        fid = m.get('fid', '')
        teams = f"{m.get('home_team', '')} vs {m.get('away_team', '')}"
        mt = m.get('match_time') or m.get('date', '')
        score = m.get('score', '')
        score_t = parse_score(score)

        # ── 信号1: 价值投注 (双门槛) ──
        bv = m.get('best_value') or {}
        if bv.get('outcome') and bv.get('ev', 0) > EV_MIN and bv.get('edge', 0) > EDGE_MIN:
            signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'value', 'signal_cn': '价值投注',
                'outcome': bv['outcome'], 'odds': bv.get('odds', 0),
                'ev': bv.get('ev', 0), 'edge': bv.get('edge', 0),
                'kelly': bv.get('kelly', 0),
            })

        # ── 信号2: 客胜规则A ──
        if bv.get('outcome') == 'away' and bv.get('ev', 0) > AWAY_EV_MIN:
            signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'ruleA', 'signal_cn': '客胜规则A',
                'outcome': 'away', 'odds': bv.get('odds', 0),
                'ev': bv.get('ev', 0), 'edge': bv.get('edge', 0),
                'kelly': bv.get('kelly', 0),
            })

        # ── 信号3: ⚡高权重 (避雷) ──
        w = m.get('importance_weight', 0) or 0
        if w >= WEIGHT_MIN:
            # 避雷方向: 模型==TS 同向
            def argmax3(w_, dr_, l_):
                mx = max(w_, dr_, l_)
                return 0 if mx == w_ else (1 if mx == dr_ else 2)
            md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
            tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
            signals.append({
                'fid': fid, 'teams': teams, 'match_time': mt, 'score': score,
                'signal': 'weight', 'signal_cn': '⚡高权重',
                'outcome': ['home', 'draw', 'away'][md],
                'odds': 0, 'ev': 0, 'edge': 0, 'kelly': 0,
                'weight': w, 'same_dir': (md == tsd),
            })

        # ── 信号4: 模型方向 (M最大概率) — 2026-08-15 已砍 ──
        # 回测: 1389场 胜率51.9% 利润-50.83; 赔率≥2.0仍-5.77, 庄家定价已吃掉模型判断
        # 口径验证(中间概率→最大概率): 命中率24.9%→51.9%, 平局癌63.8%→3.5%
        # 结论: 方向准≠下注赚钱, 该信号是负资产, 与升水信号同批弃用

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

    with open(RESULTS, 'r', encoding='utf-8') as f:
        matches = json.load(f)['matches']

    # 读取现有账本 (按 fid+signal 去重)
    ledger = load_ledger()
    seen = {(e['fid'], e['signal']) for e in ledger}

    new_count = 0
    settled = 0
    for sig in collect_signals(matches):
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
