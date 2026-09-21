#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""500.com 北单全池表 -> docs/data/bjdc_pool.json

把 trade.500.com 两页的「当日在售池」整体落盘, 供 docs/bjdc.html 展示:
  让 = 让球胜平负 (trade.500.com/bjdc)       —— 与看板亚初行(ahbd_open_*)同源
  过 = 胜负过关   (trade.500.com/bjdcsf)     —— 与看板亚即行(ahbd_cur_*)同源

只读工作: 只写 docs/data/bjdc_pool.json, 不碰 results.json / 选号 / 升水掉水规则。
全池含大量我方清单里没有的场次(英冠/巴甲/西乙/日韩下午场等), 这是源池差异, 不是匹配 bug。
"""
import importlib.util
import json
import os
from datetime import datetime, date as _date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'f5', os.path.join(ROOT, 'scripts', 'fetch_500_bjdc.py'))
f5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f5)

OUT = os.path.join(ROOT, 'docs', 'data', 'bjdc_pool.json')
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')


def water(sp):
    w = f5.water(sp)
    return None if w is None else round(w, 2)


def _d(s):
    try:
        return _date.fromisoformat((s or '')[:10])
    except ValueError:
        return None


def build_rows(rr, rs, matches):
    hits = {}
    for rows, key in ((rr, 'open'), (rs, 'cur')):
        for sc, fid, m, r in f5.join_rows(rows, matches):
            h = hits.setdefault(fid, {'m': m, 'in_open': False, 'in_cur': False, 'sim': 0.0})
            h['period'] = h.get('period') or (r.get('period') or '')
            h['in_open'] = h['in_open'] or (m.get('ahbd_open_home') is not None)
            h['in_cur'] = h['in_cur'] or (m.get('ahbd_cur_home') is not None)
            h['sim'] = max(h['sim'], round(sc, 2))

    out = []
    for fid in set(rr) | set(rs):
        r, s = rr.get(fid), rs.get(fid)
        base = r or s
        d = (base.get('date') or '')[:10]
        row = {
            'fid': fid, 'date': d, 'time': (r or {}).get('time') or (s or {}).get('time') or '',
            'num': (r or {}).get('num') or (s or {}).get('num') or '',
            'period': (r or {}).get('period') or (s or {}).get('period') or '',
            'league': (r or {}).get('league') or (s or {}).get('league') or '',
            'home': base.get('home', ''),
            'away': base.get('away', ''), 'in_open': bool(r), 'in_cur': bool(s), 'hit': None,
        }
        if r:
            row['open'] = {'hcp': r.get('hcp', ''), 'text': f5.hcp_text(r.get('hcp')),
                           'sp': r.get('sp', [])[:3],
                           'water': [water(x) for x in r.get('sp', [])[:3]]}
        if s:
            row['cur'] = {'hcp': s.get('rq', ''), 'text': f5.hcp_text(s.get('rq')),
                          'sp': s.get('sp', [])[:2],
                          'water': [water(x) for x in s.get('sp', [])[:2]]}
        h = hits.get(fid)
        if h:
            m = h['m']
            dd = None
            a, b = _d(d), _d(m.get('date'))
            if a and b:
                dd = (b - a).days
            row['hit'] = {'date': (m.get('date') or '')[:10], 'home': m.get('home_team'),
                          'away': m.get('away_team'), 'league': m.get('league'),
                          'no': m.get('beidan_no'), 'fid': m.get('fid'), 'period': h.get('period'),
                          'day_diff': dd, 'sim': h['sim'],
                          'in_open': h['in_open'], 'in_cur': h['in_cur']}
            row['league'] = row['league'] or m.get('league') or ''
        out.append(row)
    out.sort(key=lambda x: (x['date'], x['time'] or '99:99', x['league'], x['home']))
    return out


def main():
    matches = json.load(open(RESULTS, encoding='utf-8'))['matches']
    rr, rs = f5.merge_pool(f5.fetch_pool())
    rows = build_rows(rr, rs, matches)
    hit = [r for r in rows if r['hit']]
    nz = list(rr.values()) + list(rs.values())
    periods = sorted({str(x.get('period')) for x in nz if x.get('period')})
    data = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'trade.500.com bjdc(让球胜平负) + bjdcsf(胜负过关)',
        'period': periods[-1] if periods else '',   # 当期期号(如 26096)
        'periods': periods,                        # 池内涉及的各期(当期+前一期)
        'counts': {'total': len(rows), 'rangqiu': len(rr), 'shengfu': len(rs),
                   'hit': len(hit),
                   'hit_open': sum(1 for r in rows if r.get('hit', {}) and r['hit']['in_open']),
                   'hit_cur': sum(1 for r in rows if r.get('hit', {}) and r['hit']['in_cur']),
                   'in_list': sum(1 for r in rows if r['hit'])},
        'rows': rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"[bjdc_pool] 全池 {len(rows)} 行 (让 {len(rr)} / 过 {len(rs)}) | 命中我方清单 {len(hit)} 场 "
          f"(写库 让 {data['counts']['hit_open']} / 过 {data['counts']['hit_cur']}) -> {OUT}")


if __name__ == '__main__':
    main()
