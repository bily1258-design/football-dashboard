#!/usr/bin/env python3
"""筛选「双模型一致+TS反向」场次：客客主（模型客+LGBM客+TS主）/ 主主客（模型主+LGBM主+TS客）。

用法: python3 scripts/list_dual_consensus_ts_reverse.py [YYYY-MM-DD] [HH:MM]
默认: 今天 18:00 之后的场次。
"""
import json, os, sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_PATH = os.path.join(PROJECT_DIR, 'docs', 'data', 'results.json')

def direction(w, d, l):
    m = max(w, d, l)
    return '主' if m == w else ('平' if m == d else '客')

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    cutoff_time = '18:00'
    if len(sys.argv) > 1:
        today = sys.argv[1]
    if len(sys.argv) > 2:
        cutoff_time = sys.argv[2]
    cutoff = f'{today} {cutoff_time}'

    with open(RESULTS_PATH) as f:
        data = json.load(f)
    matches = data.get('matches', [])

    hits = []
    for m in matches:
        mt = m.get('match_time', '')
        if mt < cutoff:
            continue
        md = direction(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        ld = direction(m.get('lgbm_win', 0), m.get('lgbm_draw', 0), m.get('lgbm_loss', 0))
        td = direction(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        comb = md + ld + td
        if comb not in ('客客主', '主主客'):
            continue
        hits.append((m, md, ld, td, comb))

    hits.sort(key=lambda x: x[0]['match_time'])
    print(f'📋 「双模型一致+TS反向」筛选 [{cutoff} 之后] 共 {len(hits)} 场')
    print('=' * 90)
    for m, md, ld, td, comb in hits:
        h, a = m['home_team'], m['away_team']
        mt = m['match_time']
        sc = m.get('score') or '-'
        ow, od, ol = m.get('odds_win'), m.get('odds_draw'), m.get('odds_loss')
        odds = f'{ow}/{od}/{ol}' if ow else '-'
        ah = m.get('ah_handicap_text') or '-'
        mw, mdr, ml = m.get('model_win'), m.get('model_draw'), m.get('model_loss')
        lw, ldr, ll = m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss')
        tw, tdr, tl = m.get('ts_win'), m.get('ts_draw'), m.get('ts_loss')
        print(f'[{comb}] {mt}  {h} vs {a}  比分:{sc}')
        print(f'      模型 {mw:.0%}/{mdr:.0%}/{ml:.0%} | LGBM {lw:.0%}/{ldr:.0%}/{ll:.0%} | TS {tw:.0%}/{tdr:.0%}/{tl:.0%}')
        print(f'      欧赔 {odds} | 亚盘 {ah}')
        print('-' * 90)
    print(f'共 {len(hits)} 场')

if __name__ == '__main__':
    main()
