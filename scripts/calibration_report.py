#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校准报表: Brier / LogLoss / 命中率 + 众数·热门·市场基线对照 (只读)

数据源: docs/data/results.json (已结算场次) + data/betting_ledger.json (实盘注)
输出:   ~/storage/shared/Documents/校准报表.md + 校准报表.xlsx (固定名, 每日覆盖)
不改任何推荐/账本口径。
"""
import json
import math
import os
from collections import defaultdict
from datetime import datetime

ROOT = os.path.expanduser('~/football-dashboard')
OUT_DIR = os.path.expanduser('~/storage/shared/Documents')
RES = os.path.join(ROOT, 'docs', 'data', 'results.json')
LED = os.path.join(ROOT, 'docs', 'data', 'betting_ledger.json')


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def devig(o):
    """proportional 去水 (生产口径)"""
    if not o or any(x is None or x <= 1.0 for x in o):
        return None
    inv = [1.0 / x for x in o]
    s = sum(inv)
    return [x / s for x in inv]


def outcome_of(score):
    try:
        h, a = [int(x) for x in score.replace(' ', '').split('-')]
    except Exception:
        return None
    return 0 if h > a else (1 if h == a else 2)


def brier_logloss(rows):
    """rows: [(probs(3), y)]"""
    if not rows:
        return None
    b = sum(sum((p[i] - (1.0 if y == i else 0.0)) ** 2 for i in range(3)) for p, y in rows) / len(rows)
    ll, eps = 0.0, 1e-15
    for p, y in rows:
        ll -= math.log(max(eps, min(1 - eps, p[y])))
    acc = sum(1 for p, y in rows if max(range(3), key=lambda i: p[i]) == y) / len(rows)
    return dict(n=len(rows), brier=b, logloss=ll / len(rows), acc=acc)


def load_matches():
    d = json.load(open(RES, encoding='utf-8'))
    ms = d['matches'] if isinstance(d, dict) else d
    out = []
    for m in ms:
        y = outcome_of(str(m.get('score') or ''))
        if y is None:
            continue
        ow, od, ol = m.get('odds_win'), m.get('odds_draw'), m.get('odds_loss')
        mkt = devig([ow, od, ol])
        rec = dict(
            fid=str(m.get('fid') or ''), date=str(m.get('date') or '')[:10],
            league=m.get('league') or m.get('event') or '', y=y,
            odds=[ow, od, ol], mkt=mkt,
            model=[m.get('model_win'), m.get('model_draw'), m.get('model_loss')],
            lgbm=[m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss')],
            cal=[m.get('cal_win'), m.get('cal_draw'), m.get('cal_loss')],
            pred=m.get('prediction'), hit=m.get('hit'),
        )
        for k in ('model', 'lgbm', 'cal'):
            v = rec[k]
            try:
                v = [float(x) for x in v]
            except (TypeError, ValueError):
                rec[k] = None
                continue
            s = sum(v)
            rec[k] = [x / s for x in v] if s > 0 else None
        out.append(rec)
    return out


def calib_table(rows, prob_idx, bins=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.01)):
    """按某方向预测概率分箱 -> 实际频率"""
    rows = [(p, y) for p, y in rows if p is not None]
    out = []
    for lo, hi in zip(bins, bins[1:]):
        sel = [(p[prob_idx], y) for p, y in rows if lo <= p[prob_idx] < hi]
        if len(sel) < 20:
            continue
        k = sum(1 for _, y in sel if y == prob_idx)
        n = len(sel)
        avg_p = sum(p for p, _ in sel) / n
        lo_ci, hi_ci = wilson(k, n)
        out.append(dict(lo=lo, hi=min(hi, 1.0), n=n, avg_p=avg_p, actual=k / n,
                        ci=(lo_ci, hi_ci), diff=k / n - avg_p))
    return out


def main():
    ms = load_matches()
    lines = []
    A = lines.append
    A(f'# 校准报表 · {datetime.now():%Y-%m-%d %H:%M}')
    A('')
    A(f'样本: 已结算场次 **{len(ms)}** 场（{min((m["date"] for m in ms if m["date"]), default="-")} → '
      f'{max((m["date"] for m in ms if m["date"]), default="-")}）')
    A('')

    # 基线
    home_rate = sum(1 for m in ms if m['y'] == 0) / len(ms)
    draw_rate = sum(1 for m in ms if m['y'] == 1) / len(ms)
    away_rate = sum(1 for m in ms if m['y'] == 2) / len(ms)
    fav = 0
    fav_n = 0
    for m in ms:
        o = m['odds']
        if o and all(isinstance(x, (int, float)) and x > 0 for x in o):
            fav_n += 1
            if o.index(min(o)) == m['y']:
                fav += 1
    A('## 一、基线（不看模型）')
    A('')
    A(f'- 主胜率 **{home_rate*100:.2f}%** / 平 **{draw_rate*100:.2f}%** / 客胜 **{away_rate*100:.2f}%**')
    lo, hi = wilson(fav, fav_n)
    A(f'- 永远揀热门（最低赔率方向）命中 **{fav/fav_n*100:.2f}%**（n={fav_n}，95%CI {lo*100:.1f}–{hi*100:.1f}%）')
    A(f'- 永远揀主胜 = **{home_rate*100:.2f}%**（众数基线，n={len(ms)}）')
    A('')

    # 模型对比
    A('## 二、模型 vs 市场（三分类）')
    A('')
    A('| 预测器 | n | 命中率 | Brier | LogLoss |')
    A('|---|---|---|---|---|')
    T = {}
    for name, key in (('泊松综合 model_*', 'model'), ('LGBM', 'lgbm'), ('cal_* (校准后)', 'cal'), ('市场去水(proportional)', 'mkt')):
        rows = [(m[key], m['y']) for m in ms if m[key]]
        s = brier_logloss(rows)
        if s:
            T[name] = s
            A(f'| {name} | {s["n"]} | {s["acc"]*100:.2f}% | {s["brier"]:.4f} | {s["logloss"]:.4f} |')
    A('')
    if 'LGBM' in T and '市场去水(proportional)' in T:
        d = T['LGBM']['brier'] - T['市场去水(proportional)']['brier']
        A(f'- LGBM 比市场 Brier {"差" if d>0 else "好"} **{abs(d):.4f}**；'
          f'LogLoss {"差" if T["LGBM"]["logloss"]>T["市场去水(proportional)"]["logloss"] else "好"} '
          f'**{abs(T["LGBM"]["logloss"]-T["市场去水(proportional)"]["logloss"]):.4f}**')
        A('')
    # 模型方向与市场一致率
    ag = [m for m in ms if m['model'] and m['mkt']]
    if ag:
        same = sum(1 for m in ag if max(range(3), key=lambda i: m['model'][i]) == max(range(3), key=lambda i: m['mkt'][i]))
        A(f'- 模型方向与市场去水方向一致 **{same/len(ag)*100:.2f}%**（{same}/{len(ag)}）；'
          f'不一致 {(len(ag)-same)/len(ag)*100:.2f}%')
        A('')

    # 主胜校准
    A('## 三、主胜概率校准（model_*）')
    A('')
    A('| 预测区间 | n | 平均预测 | 实际 | 偏差 | 95%CI(实际) |')
    A('|---|---|---|---|---|---|')
    rows = [(m['model'], m['y']) for m in ms if m['model']]
    ct = calib_table(rows, 0)
    for b in ct:
        A(f'| {b["lo"]*100:.0f}–{b["hi"]*100:.0f}% | {b["n"]} | {b["avg_p"]*100:.1f}% | '
          f'{b["actual"]*100:.1f}% | {b["diff"]*100:+.1f}pp | {b["ci"][0]*100:.1f}–{b["ci"][1]*100:.1f}% |')
    over = [b for b in ct if b['diff'] < -0.03]
    under = [b for b in ct if b['diff'] > 0.03]
    A('')
    A(f'- 高估区（偏差 < −3pp）：{len(over)} 档；低估区（> +3pp）：{len(under)} 档')
    A('')

    # 客胜校准
    A('## 四、客胜概率校准（model_*）')
    A('')
    A('| 预测区间 | n | 平均预测 | 实际 | 偏差 | 95%CI(实际) |')
    A('|---|---|---|---|---|---|')
    for b in calib_table(rows, 2):
        A(f'| {b["lo"]*100:.0f}–{b["hi"]*100:.0f}% | {b["n"]} | {b["avg_p"]*100:.1f}% | '
          f'{b["actual"]*100:.1f}% | {b["diff"]*100:+.1f}pp | {b["ci"][0]*100:.1f}–{b["ci"][1]*100:.1f}% |')
    A('')

    # 赔率分档
    A('## 五、按热门赔率分档（model_* 命中率 vs 市场）')
    A('')
    A('| 最低赔率 | n | model 命中 | 市场 命中 | 实际热门率 |')
    A('|---|---|---|---|---|')
    edges = [(1, 1.6), (1.6, 2.0), (2.0, 2.6), (2.6, 3.5), (3.5, 99)]
    for lo_, hi_ in edges:
        sel = [m for m in ms if m['odds'] and all(isinstance(x, (int, float)) and x > 0 for x in m['odds'])
               and lo_ <= min(m['odds']) < hi_]
        if not sel:
            continue
        mm = sum(1 for m in sel if m['model'] and max(range(3), key=lambda i: m['model'][i]) == m['y'])
        kk = sum(1 for m in sel if m['mkt'] and max(range(3), key=lambda i: m['mkt'][i]) == m['y'])
        fv = sum(1 for m in sel if m['odds'].index(min(m['odds'])) == m['y'])
        A(f'| {lo_}–{hi_ if hi_<99 else "∞"} | {len(sel)} | {mm/len(sel)*100:.1f}% | '
          f'{kk/len(sel)*100:.1f}% | {fv/len(sel)*100:.1f}% |')
    A('')

    # 实盘注
    try:
        led = json.load(open(LED, encoding='utf-8'))
    except Exception:
        led = []
    settled = [b for b in led if b.get('result') in ('win', 'loss')]
    if settled:
        A('## 六、实盘注 edge 分档：账面概率 vs 实际（投注簿）')
        A('')
        A('| 账面 edge | n | 账面预测命中 | 实际命中 | 95%CI | 均赔 | ROI |')
        A('|---|---|---|---|---|---|---|')
        bucket = [(0, .03), (.03, .05), (.05, .10), (.10, .20), (.20, .40), (.40, 9)]
        for lo_, hi_ in bucket:
            sel = [b for b in settled if lo_ <= float(b.get('edge') or 0) < hi_]
            if len(sel) < 10:
                continue
            n = len(sel)
            w = sum(1 for b in sel if b.get('result') == 'win')
            odds = [float(b.get('odds') or 0) for b in sel]
            exp_hit = sum((1.0 + float(b.get('edge') or 0)) / o for b, o in zip(sel, odds)) / n
            roi = sum(float(b.get('profit') or 0) for b in sel) / n
            l2, h2 = wilson(w, n)
            A(f'| {lo_*100:.0f}–{hi_*100:.0f}% | {n} | {exp_hit*100:.1f}% | {w/n*100:.1f}% | '
              f'{l2*100:.1f}–{h2*100:.1f}% | {sum(odds)/n:.2f} | {roi*100:+.1f}% |')
        A('')
        w = sum(1 for b in settled if b.get('result') == 'win')
        l2, h2 = wilson(w, len(settled))
        A(f'- 全部已结算 **{len(settled)}** 注，命中 **{w}**（{w/len(settled)*100:.2f}%，95%CI {l2*100:.1f}–{h2*100:.1f}%）')
        A(f'- 合计盈亏 **{sum(float(b.get("profit") or 0) for b in settled):+.2f}** 单位')
        A('')
    A('*口径: Brier 为三分类和式; 去水=proportional(生产现状); CI=Wilson 95%; 只读报表, 不影响推荐/账本。*')

    os.makedirs(OUT_DIR, exist_ok=True)
    md = os.path.join(OUT_DIR, '校准报表.md')
    open(md, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print(f'[CAL] 写出 {md} ({os.path.getsize(md)} bytes)')
    print('\n'.join(lines[:40]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
