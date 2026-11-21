#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shin 去水影子落盘（只读影子，不改推荐/星级/账本）

把每场的 proportional(现行) 与 Shin 两套去水后的模型概率写进
docs/data/shin_probs.json，攒 1-2 周后用真实赛果 + CLV 复盘:
    - 哪套去水的 edge 更"值钱"
    - Shin 砍掉的高赔 edge 是否真亏

用法: python3 scripts/shin_shadow_dump.py [--days 3] [--dry]
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.expanduser('~/football-dashboard')
RES = os.path.join(ROOT, 'docs', 'data', 'results.json')
OUT = os.path.join(ROOT, 'docs', 'data', 'shin_probs.json')
KEEP_DAYS = 60

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import ai_analysis as A  # noqa: E402


def shin(ow, od, ol):
    """Shin 去水 -> (pw, pd, pl, margin)"""
    o = [1.0 / ow, 1.0 / od, 1.0 / ol]
    S = sum(o)

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * oi * oi / S) - z) / (2 * (1 - z)) for oi in o]

    lo, hi = 1e-9, 0.35
    for _ in range(80):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > 1:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    t = sum(p)
    return p[0] / t, p[1] / t, p[2] / t, S - 1


def prop(ow, od, ol):
    """proportional 去水 -> (pw, pd, pl, margin)"""
    iw, idr, il = 1.0 / ow, 1.0 / od, 1.0 / ol
    t = iw + idr + il
    return iw / t, idr / t, il / t, t - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3, help='只处理最近 N 天开赛的场次')
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()

    d = json.load(open(RES, encoding='utf-8'))
    ms = d['matches'] if isinstance(d, dict) else d

    cutoff = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    try:
        priors = A.load_league_priors() or {}
    except Exception as e:
        print('[shin] priors 不可用:', e)
        priors = {}

    raw_prop = A.implied_from_odds

    def calc(m, fn):
        A.implied_from_odds = fn
        try:
            return A.calc_model_probs(float(m['odds_win']), float(m['odds_draw']),
                                      float(m['odds_loss']), priors,
                                      A.LEAGUE_PRIOR_LAMBDA, m.get('league') or '')
        finally:
            A.implied_from_odds = raw_prop

    store = {}
    if os.path.exists(OUT):
        try:
            store = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            store = {}

    n_hit = 0
    for m in ms:
        day = str(m.get('match_time') or m.get('date') or '')[:10]
        if day and day < cutoff:
            continue
        fid = str(m.get('fid') or '')
        if not fid:
            continue
        try:
            ow, odr, ol = float(m['odds_win']), float(m['odds_draw']), float(m['odds_loss'])
        except (TypeError, ValueError, KeyError):
            continue
        if min(ow, odr, ol) <= 1.0:
            continue
        try:
            pw, pdr, pl, _ = calc(m, prop)
            sw, sdr, sl, _ = calc(m, shin)
        except Exception:
            continue
        n_hit += 1
        store[fid] = dict(
            day=day, league=m.get('league'), teams=m.get('teams'),
            odds=[round(ow, 3), round(odr, 3), round(ol, 3)],
            prop=[round(pw, 5), round(pdr, 5), round(pl, 5)],
            shin=[round(sw, 5), round(sdr, 5), round(sl, 5)],
            model=[m.get('model_win'), m.get('model_draw'), m.get('model_loss')],
            star=m.get('stars') or m.get('star'),
            record=datetime.now().strftime('%Y-%m-%d %H:%M'),
        )

    # 裁剪: 只保留 KEEP_DAYS 天内
    c2 = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    before = len(store)
    store = {k: v for k, v in store.items() if str(v.get('day') or '') >= c2}
    print(f'[shin] 本轮写入 {n_hit} 场 | 裁剪 {before - len(store)} | 累计 {len(store)} 场')
    if args.dry:
        print('[shin] dry-run, 不写文件')
        return
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f'[shin] 已写 {OUT} ({os.path.getsize(OUT)} bytes)')


if __name__ == '__main__':
    main()
