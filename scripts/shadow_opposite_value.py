#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⚡反向 value 影子追踪（只读，不改任何规则/清单/账本）

用途：value（真下注）与 ⚡weight（记号）同场且方向相反的场次，历史上几乎必输
（2026-09-23 复核：53 场 3.8% / -35.46，占 value 总亏损 -62.62 的 57%）。
本脚本把 value 注单拆成「同场有反向 ⚡」与「其余」两组，累计跟踪命中率/ROI，
样本攒够再决定要不要立事前过滤规则——**不改选号规则，只做影子对照**。

用法: python3 scripts/shadow_opposite_value.py [--recent 14] [--detail 5]
"""
import json, os, sys, collections, argparse

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      '..', 'docs', 'data', 'betting_ledger.json')


def load():
    with open(LEDGER, encoding='utf-8') as f:
        d = json.load(f)
    return d if isinstance(d, list) else d.get('bets') or d.get('ledger') or []


def stats(rows):
    n = len(rows)
    if not n:
        return dict(n=0, wins=0, rate=0.0, profit=0.0, roi=0.0)
    wins = sum(1 for b in rows if b.get('result') == 'win')
    profit = sum(b.get('profit') or 0 for b in rows)
    return dict(n=n, wins=wins, rate=wins / n * 100, profit=profit, roi=profit / n * 100)


def fmt(tag, s):
    return (f"  {tag}: {s['n']:4d} 注  胜 {s['wins']:3d} ({s['rate']:5.1f}%)  "
            f"利润 {s['profit']:+7.2f}  ROI {s['roi']:+6.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recent', type=int, default=14, help='近 N 天另算一组')
    ap.add_argument('--detail', type=int, default=5, help='列出最近 N 场明细')
    a = ap.parse_args()

    bets = load()
    done = [b for b in bets if b.get('result') in ('win', 'loss')]
    # 同 fid 同时存在反向 weight 记录
    flags = {}
    for b in done:
        if b.get('signal') == 'weight':
            flags.setdefault(b['fid'], set()).add(b.get('outcome'))
    val = [b for b in done if b.get('signal') == 'value']
    grpA = [b for b in val if b['fid'] in flags and b.get('outcome') not in flags[b['fid']]]
    grpB = [b for b in val if b not in grpA]

    print('⚡反向 value 影子追踪（只读；不改规则）')
    print(f'  数据源 {os.path.relpath(LEDGER)}  已结算 {len(done)} 条')
    print(fmt('A 同场有反向⚡', stats(grpA)))
    print(fmt('B 其余 value  ', stats(grpB)))
    d = stats(grpA)['roi'] - stats(grpB)['roi']
    print(f"  组A ROI 落后组B {abs(d):.1f}pp" if d < 0 else f"  组A ROI 领先组B {d:.1f}pp")

    if a.recent:
        import datetime
        cut = (datetime.date.today() - datetime.timedelta(days=a.recent)).isoformat()
        rA = [b for b in grpA if (b.get('match_time') or '')[:10] >= cut]
        rB = [b for b in grpB if (b.get('match_time') or '')[:10] >= cut]
        print(f'近 {a.recent} 天:')
        print(fmt('A 同场有反向⚡', stats(rA)))
        print(fmt('B 其余 value  ', stats(rB)))

    if a.detail:
        print(f'最近 {a.detail} 场组A:')
        for b in sorted(grpA, key=lambda z: z.get('match_time') or '')[-a.detail:]:
            print(f"    {b.get('match_time','')[:10]} {b.get('teams','')} "
                  f"{b.get('outcome','')}@{b.get('odds','')} {b.get('result','')} "
                  f"{b.get('profit',0):+.2f}")

    n = len(grpA)
    print(f"  判定: 组A 样本 {n} 注 " + ('< 50 → 只追踪，不下结论/不改规则'
          if n < 50 else '≥ 50 → 可评估是否立事前过滤'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
