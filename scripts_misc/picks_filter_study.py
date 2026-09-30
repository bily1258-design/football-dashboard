#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读研究: 「只加入跑清单(①)里面的赛事」能否过滤掉错方向的比赛?
口径:
  ① = model(argmax) 与 LGBM(argmax) 方向一致 ∧ 该方向 model 或 lgbm 概率 >0.449
       (away_value_picks.py:234-250 原样复刻, 与实际出号规则一致)
  命中 = ①推荐方向 == 实际赛果(主/平/客); 赔率口径两套: HKJC(pin_comparison.current) / Pinnacle(odds_*)
  ROI = 单位注(中 +赔-1, 不中 -1)
结论只读, 不写库、不改任何选号规则。
"""
import json, re, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'docs', 'data', 'results.json')
DIRS = ('主', '平', '客')


def num(*xs):
    out = []
    for x in xs:
        try:
            v = float(x)
        except (TypeError, ValueError):
            v = 0.0
        out.append(v if v == v else 0.0)
    return out


def argmax3(a, b, c):
    v = num(a, b, c)
    i = max(range(3), key=lambda k: v[k])
    return DIRS[i], v[i]


def score_outcome(s):
    if not s:
        return None
    m = re.findall(r'\d+', str(s))
    if len(m) < 2:
        return None
    h, a = int(m[0]), int(m[1])
    return '主' if h > a else ('客' if a > h else '平')


def build():
    R = json.load(open(RES, encoding='utf-8'))
    ms = R['matches'] if isinstance(R, dict) else R
    recs = []
    for m in ms:
        o = score_outcome(m.get('score'))
        if o is None:
            continue
        md, _ = argmax3(m.get('model_win'), m.get('model_draw'), m.get('model_loss'))
        ld, _ = argmax3(m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss'))
        is1 = (md == ld)
        if is1:
            s = '_win' if md == '主' else ('_draw' if md == '平' else '_loss')
            is1 = (num(m.get('model' + s))[0] > 0.449 or num(m.get('lgbm' + s))[0] > 0.449)
        pc = (m.get('pin_comparison') or {}).get('current')
        hk = num(*pc) if (pc and len(pc) == 3) else None
        pin = num(m.get('odds_win'), m.get('odds_draw'), m.get('odds_loss'))
        idx = DIRS.index(md)
        ent = 0.0
        for p in num(m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss')):
            if p > 0:
                ent -= p * math.log(p)
        tsd = argmax3(m.get('ts_win'), m.get('ts_draw'), m.get('ts_loss'))[0]
        cand = [(pin[k], DIRS[k]) for k in range(3) if pin[k] > 1]
        mkt = min(cand)[1] if cand else ''
        recs.append(dict(o=o, md=md, is1=is1, ent=ent, tsd=tsd, date=(m.get('date') or ''),
                         hk=(hk[idx] if hk and hk[idx] > 1 else None),
                         pin=(pin[idx] if pin[idx] > 1 else None),
                         ok=(md == o), mkt=mkt, mktok=(mkt == o), has_hk=(hk is not None)))
    return recs


def rep(name, rs, key):
    rs = [r for r in rs if r[key] is not None]
    if not rs:
        print(f'   {name}: 0 场')
        return
    n = len(rs)
    hits = sum(1 for r in rs if r['ok'])
    prof = sum(((r[key] - 1) if r['ok'] else -1) for r in rs)
    mh = sum(1 for r in rs if r['mktok'])
    print(f'   {name}: n={n:5d} 命中={hits/n*100:5.1f}% 错={100-hits/n*100:5.1f}% '
          f'均赔={sum(r[key] for r in rs)/n:4.2f} ROI={prof/n*100:+6.1f}% | 同场市场热门命中={mh/n*100:5.1f}%')


def main():
    recs = build()
    n_all = len(recs)
    print(f'== 全历史已结算场次 n={n_all} ==')
    mh = sum(1 for r in recs if r['mktok'])
    print(f'   市场热门(最低Pinnacle赔)命中 = {mh/n_all*100:.1f}%  ← 方向基线')
    for key, label in (('pin', 'Pinnacle'), ('hk', 'HKJC')):
        print(f'\n== [{label} 赔口径] ①清单内 vs ①清单外 ==')
        rep('①清单内', [r for r in recs if r['is1']], key)
        rep('①清单外', [r for r in recs if not r['is1']], key)
    one = [r for r in recs if r['is1']]
    print(f'   ①占比 = {len(one)/n_all*100:.1f}% (清单把候选砍掉 {100-len(one)/n_all*100:.1f}%)')

    print('\n== ①清单内: 再加过滤器能否进一步压错方向 (HKJC赔口径) ==')
    ohk = [r for r in one if r['hk'] is not None]
    for lo, hi in [(0, 1.06), (1.06, 1.075), (1.075, 1.085), (1.085, 1.095), (1.095, 9)]:
        rep(f'LGBM三路熵 {lo}~{hi}', [r for r in ohk if lo <= r['ent'] < hi], 'hk')
    for lo, hi in [(1, 1.8), (1.8, 2.5), (2.5, 4.0), (4.0, 99)]:
        rep(f'该方向赔 {lo}~{hi}', [r for r in ohk if lo <= r['hk'] < hi], 'hk')
    for same in (True, False):
        rep('TS' + ('同向' if same else '反向'), [r for r in ohk if (r['tsd'] == r['md']) == same], 'hk')
    for d in DIRS:
        rep(f'方向{d}', [r for r in ohk if r['md'] == d], 'hk')
    rep('熵≤1.075 ∧ 赔≥1.8 (📐低熵核心区)', [r for r in ohk if r['ent'] <= 1.075 and r['hk'] >= 1.8], 'hk')
    print('\n注: 熵越低的场次方向越明确但赔越低 → 命中高、ROI 薄; 反过来赔高的场次命中低。')

    # ── 2026-09-30 续挖: 熵闸门的稳健性 ──────────────────────────────
    print('\n== 续挖1: 熵闸门是否只是"赔率的代理"? 固定赔率带内再看熵 (①池, HKJC口径) ==')
    print('   (若同一赔率带内低熵正/高熵负 → 熵是独立维度, 不是赔率代理)')
    for lo, hi in [(1, 1.8), (1.8, 2.5), (2.5, 99)]:
        band = [r for r in ohk if lo <= r['hk'] < hi]
        if not band:
            continue
        print(f'   赔 {lo}~{hi} (n={len(band)}):')
        for e0, e1 in [(0, 1.075), (1.075, 9)]:
            sub = [r for r in band if e0 <= r['ent'] < e1]
            if not sub:
                continue
            n = len(sub)
            h = sum(1 for r in sub if r['ok'])
            p = sum(((r['hk'] - 1) if r['ok'] else -1) for r in sub)
            print(f'      熵 {e0}~{e1}: n={n:4d} 命中={h/n*100:5.1f}% 均赔={sum(r["hk"] for r in sub)/n:4.2f} ROI={p/n*100:+6.2f}%')

    print('\n== 续挖2: 走前验证 — 按时间切段看闸门是否稳定 (①池, HKJC口径) ==')
    qs = {}
    for r in ohk:
        d = r.get('date') or ''
        qs.setdefault(d[:7], []).append(r)
    print(f'   {"月份":8s}{"":4s}{"低熵组(n/命中/ROI)":34s}高熵组(n/命中/ROI)')
    for mo in sorted(qs):
        g = qs[mo]
        cells = []
        for e0, e1 in [(0, 1.075), (1.075, 9)]:
            sub = [r for r in g if e0 <= r['ent'] < e1]
            if not sub:
                cells.append('n=0')
                continue
            n = len(sub)
            h = sum(1 for r in sub if r['ok'])
            p = sum(((r['hk'] - 1) if r['ok'] else -1) for r in sub)
            cells.append(f'n={n:4d} {h/n*100:5.1f}% {p/n*100:+7.2f}%')
        print(f'   {mo:8s}{"":4s}{cells[0]:34s}{cells[1]}')

    print('\n== 续挖3: 阈值敏感性 (①池 熵≤X 全池, HKJC口径) ==')
    for x in (1.055, 1.06, 1.065, 1.07, 1.075, 1.08, 1.085, 1.09, 1.10):
        sub = [r for r in ohk if r['ent'] <= x]
        if not sub:
            continue
        n = len(sub)
        h = sum(1 for r in sub if r['ok'])
        p = sum(((r['hk'] - 1) if r['ok'] else -1) for r in sub)
        print(f'   熵≤{x:.3f}: n={n:5d} 命中={h/n*100:5.1f}% 均赔={sum(r["hk"] for r in sub)/n:4.2f} ROI={p/n*100:+6.2f}%')
    print('   (①池整体 ROI 为负: 低赔被抽水吃掉 → 池内闸门只能"少亏", 增益要靠"低熵∧≥1.8"的高赔子集)')


if __name__ == '__main__':
    main()
