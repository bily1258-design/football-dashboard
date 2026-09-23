#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TS(第4列) × LGBM 分歧 / 同向  影子追踪【只读】

2026-09-24 重设计(用户拍板方案①: 先影子, 不动过滤):
  旧版按 "TS 平局" 分支 → 实测 ts_draw argmax = 0 场(最大 0.338, 中位 0.239),
  TS 从不出平, 该分支删除。

  现改为「连续强度分 + 四分类」:
    方向维度: agree  = TS argmax == LGBM argmax      (同向)
              diverge = 两者 argmax 不同                (反向)
    价格维度: hot    = LGBM 选的是市场最低赔(热门)
              cold   = LGBM 选的不是市场最低赔(冷门)
    强度维度: TSmax  = TS 三向最大概率, 分 4 档 (<0.36 / 0.36-0.40 / 0.40-0.45 / >=0.45)

  实测依据(2026-09-24, n=4764):
    反向×热门 n=911  命中 27.4%  edge -23.6pp  ROI -46.0%
    反向×冷门 n=665  命中 26.8%  edge  -5.3pp  ROI -15.6%
    同向×热门 n=2886                 edge  +5.5pp  ROI +11.3%
    同向×冷门 n=302                  edge +19.2pp  ROI +58.6%
    TSmax 单调: 反向组 -16.8% / -34.8% / -45.4%;  同向组 -16.6% / +7.6% / +26.0%

  预警线(只记不触发, 不改任何过滤): diverge_hot 累计 n>=100 且 edge <= -15pp 时提示。

数据源: docs/data/results.json (matches 数组)。本脚本不写任何文件。

用法:
  python3 scripts/shadow_ts_divergence.py            # 汇总表
  python3 scripts/shadow_ts_divergence.py --detail 5 # 附每类样本明细
  python3 scripts/shadow_ts_divergence.py --since 2026-09-01
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')

BANDS = [(0.0, 0.36, '<0.36'), (0.36, 0.40, '0.36-0.40'),
         (0.40, 0.45, '0.40-0.45'), (0.45, 1.01, '>=0.45')]
CLASS_CN = {'agree_hot': '同向·热门', 'agree_cold': '同向·冷门',
            'diverge_hot': '反向·热门', 'diverge_cold': '反向·冷门'}


def band_of(v):
    for lo, hi, name in BANDS:
        if lo <= v < hi:
            return name
    return BANDS[-1][2]


def actual(m):
    s = (m.get('score') or '').strip()
    if '-' not in s:
        return None
    try:
        h, a = [int(x) for x in s.split('-')[:2]]
    except ValueError:
        return None
    return 'home' if h > a else ('away' if h < a else 'draw')


def argmax3(w, d, l):
    pairs = [('home', w), ('draw', d), ('away', l)]
    if any(v is None for _, v in pairs):
        return None
    return max(pairs, key=lambda p: p[1])[0]


def load(since=None):
    with open(RESULTS, encoding='utf-8') as f:
        data = json.load(f)
    rows = []
    for m in data.get('matches', []):
        date = (m.get('date') or '')[:10]
        if since and date < since:
            continue
        res = actual(m)
        if res is None:
            continue
        tw, td, tl = m.get('ts_win'), m.get('ts_draw'), m.get('ts_loss')
        ts_pick = argmax3(tw, td, tl)
        lgbm_pick = argmax3(m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss'))
        if lgbm_pick is None and m.get('lgbm_prediction'):
            lgbm_pick = m['lgbm_prediction']
        odds = {'home': m.get('odds_win'), 'draw': m.get('odds_draw'), 'away': m.get('odds_loss')}
        if not ts_pick or not lgbm_pick or odds.get(lgbm_pick) in (None, 0):
            continue
        if any(odds.get(k) in (None, 0) for k in odds):
            continue
        fav = min(odds, key=lambda k: odds[k])
        tsmax = max(tw, td, tl)
        rows.append({
            'date': date, 'league': m.get('league') or m.get('event') or '',
            'home': m.get('home_team'), 'away': m.get('away_team'), 'score': m.get('score'),
            'cls': ('agree' if ts_pick == lgbm_pick else 'diverge') + '_' + ('hot' if lgbm_pick == fav else 'cold'),
            'band': band_of(tsmax), 'tsmax': tsmax,
            'pick': lgbm_pick, 'odds': odds[lgbm_pick],
            'hit': (lgbm_pick == res), 'fid': m.get('fid'),
        })
    return rows


def summarize(rows, key, label):
    print(f'\n【按{label}】')
    print(f'{"分类":<14}{"n":>6}{"命中率":>9}{"隐含":>9}{"edge":>10}{"ROI":>10}')
    buckets = defaultdict(list)
    for r in rows:
        buckets[r[key]].append(r)
    order = [k for k in CLASS_CN if k in buckets] or sorted(buckets)
    if key == 'band':
        order = [b[2] for b in BANDS if b[2] in buckets]
    for k in order:
        g = buckets[k]
        n = len(g)
        hit = sum(1 for r in g if r['hit'])
        imp = sum(1 / r['odds'] for r in g) / n
        hr = hit / n
        profit = sum((r['odds'] - 1) if r['hit'] else -1 for r in g)
        name = CLASS_CN.get(k, k)
        print(f'{name:<12}{n:>6}{hr:>9.1%}{imp:>9.1%}{hr - imp:>+10.1%}{profit / n:>+10.1%}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detail', type=int, default=0)
    ap.add_argument('--since', default=None)
    a = ap.parse_args()
    rows = load(a.since)
    if not rows:
        print('无可用样本'); return 0
    print(f'样本(完赛且有 TS/LGBM/三向赔率): n={len(rows)}')
    summarize(rows, 'cls', '四分类')
    print('\n【四分类 × TSmax 强度】')
    print(f'{"分类":<14}{"强度档":<12}{"n":>5}{"命中率":>9}{"隐含":>9}{"edge":>10}{"ROI":>10}')
    grid = defaultdict(list)
    for r in rows:
        grid[(r['cls'], r['band'])].append(r)
    for cls in CLASS_CN:
        for _, _, bn in BANDS:
            g = grid.get((cls, bn))
            if not g:
                continue
            n = len(g); hit = sum(1 for r in g if r['hit'])
            imp = sum(1 / r['odds'] for r in g) / n; hr = hit / n
            profit = sum((r['odds'] - 1) if r['hit'] else -1 for r in g)
            print(f'{CLASS_CN[cls]:<12}{bn:<12}{n:>5}{hr:>9.1%}{imp:>9.1%}{hr - imp:>+10.1%}{profit / n:>+10.1%}')
    # 预警线
    dh = [r for r in rows if r['cls'] == 'diverge_hot']
    if dh:
        n = len(dh); hr = sum(1 for r in dh if r['hit']) / n
        imp = sum(1 / r['odds'] for r in dh) / n
        flag = '⚠ 已达预警线' if (n >= 100 and hr - imp <= -0.15) else '未达预警线'
        print(f'\n预警线(反向·热门 n>=100 且 edge<=-15pp): n={n} edge={hr - imp:+.1%} → {flag}')
    if a.detail:
        print(f'\n【明细前 {a.detail} 例/类】')
        for cls in CLASS_CN:
            g = [r for r in rows if r['cls'] == cls]
            if not g:
                continue
            print(f'-- {CLASS_CN[cls]} (n={len(g)})')
            for r in g[-a.detail:]:
                print(f"   {r['date']} {r['league']} {r['home']} vs {r['away']} "
                      f"{r['score']} TSmax={r['tsmax']:.3f} 选{r['pick']}@{r['odds']} "
                      f"{'✓' if r['hit'] else '✘'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
