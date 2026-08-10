#!/usr/bin/env python3
"""客胜价值投注清单生成器
规则(经HKJC真实赔率回测验证): best_value.outcome==away 且 EV>0.5 且 HKJC客胜赔率 3-6
只输出未开赛(可投注)的场次, 按日期排序
用法: python3 scripts/away_value_picks.py [--all]  # --all 输出全部, 默认只输出未开赛
"""
import json
import sys
import datetime

D = json.load(open('docs/data/results.json'))
MS = D['matches']

def main():
    show_all = '--all' in sys.argv
    today = datetime.date.today().isoformat()
    rows = []
    for m in MS:
        bv = m.get('best_value')
        if not bv or bv.get('outcome') != 'away' or bv.get('ev', 0) <= 0.5:
            continue
        pc = m.get('pin_comparison') or {}
        cur = pc.get('current')
        if not cur or len(cur) < 3 or not (3 <= cur[2] <= 6):
            continue
        if not show_all and m.get('score'):
            continue  # 已开赛, 跳过
        rows.append({
            'date': m.get('date', ''), 'league': m.get('event', ''),
            'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
            'odds': cur[2], 'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
        })
    rows.sort(key=lambda x: (x['date'], -x['ev']))

    print(f"客胜价值投注清单 ({'全部' if show_all else '未开赛可投'} {len(rows)}场) 规则: 客胜+EV>0.5+赔率3-6")
    print("=" * 78)
    for r in rows:
        print(f"{r['date']} [{r['league']}] {r['home']} vs {r['away']}"
              f" | HKJC客胜 {r['odds']} | 模型概率 {r['prob']*100:.0f}% | EV {r['ev']:.2f}")
    if not rows:
        print("(今日无符合条件场次)")

    # 今日汇总(含已开赛, 供复盘)
    todays = [r for r in rows if r['date'] == today]
    if todays and not show_all:
        print("=" * 78)
        print(f"今日({today})可投注场次: {len(todays)}场")

if __name__ == '__main__':
    main()
