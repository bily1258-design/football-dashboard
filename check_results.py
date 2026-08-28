import json, glob
from collections import Counter

res = json.load(open('docs/data/results.json'))
res_by_fid = {str(m.get('fid', '')): m for m in res.get('matches', [])}

print('odds_source:', Counter(str(m.get('odds_source')) for m in res_by_fid.values()))

odds = {}
for f in glob.glob('data/matches_2026*.json'):
    try:
        d = json.load(open(f))
        ms = d if isinstance(d, list) else d.get('matches', [])
        if isinstance(ms, dict): ms = list(ms.values())
        for m in ms:
            fid = str(m.get('fid', ''))
            if fid: odds[fid] = m
    except Exception:
        pass

DIR = {'主胜': 'win', '平局': 'draw', '客胜': 'loss'}
rows = []
for fid, m in res_by_fid.items():
    pred = m.get('prediction_cn') or ''
    if pred not in DIR: continue
    hit = m.get('hit', '')
    if not hit or ('✓' not in hit and '✘' not in hit): continue
    key = DIR[pred]
    od = m.get(f'odds_{key}')
    tsd = m.get('ts_draw')
    star = bool(od and tsd is not None and od < 2.0 and tsd < 25)
    row = {'fid': fid, 'hit': '✓' in hit, 'star': star, 'pred': pred, 'odds': od, 'ts_draw': tsd}
    o = odds.get(fid) or {}
    p_open = o.get(f'odds_pinnacle_open_{key}')
    p_close = o.get(f'odds_pinnacle_{key}')
    h_open = o.get(f'odds_hkjc_open_{key}')
    h_close = o.get(f'odds_hkjc_{key}')
    row['p_chg'] = round(p_close - p_open, 3) if p_open is not None and p_close is not None else None
    row['h_chg'] = round(h_close - h_open, 3) if h_open is not None and h_close is not None else None
    rows.append(row)

def stat(name, rs):
    n = len(rs)
    if n == 0: print(f'{name}: 0'); return
    win = sum(1 for r in rs if r['hit'])
    print(f'{name}: {n} 场, 命中 {win} ({win/n*100:.1f}%)')

allr = rows
stat('全部', allr)
stat('★星级(赔<2.0且TS平<25%)', [r for r in allr if r['star']])
stat('无星', [r for r in allr if not r['star']])

pr = [r for r in allr if r['p_chg'] is not None]
stat('平博升水', [r for r in pr if r['p_chg'] > 0])
stat('平博掉水', [r for r in pr if r['p_chg'] < 0])
hr = [r for r in allr if r['h_chg'] is not None]
stat('HKJC升水', [r for r in hr if r['h_chg'] > 0])
stat('HKJC掉水', [r for r in hr if r['h_chg'] < 0])

stat('★+HKJC掉水', [r for r in hr if r['star'] and r['h_chg'] < 0])
stat('★+HKJC升水', [r for r in hr if r['star'] and r['h_chg'] > 0])
stat('★+平博升水', [r for r in pr if r['star'] and r['p_chg'] > 0])
stat('★+平博掉水', [r for r in pr if r['star'] and r['p_chg'] < 0])

# 用户完整规则: ①推荐方向平博升水 ②HKJC掉水 (加分) 不碰: HKJC升水 或 平博掉水
stat('规则1: 平博升水+HKJC掉水 (加倍)', [r for r in hr if r['p_chg'] > 0 and r['h_chg'] < 0])
stat('规则1+★ (加倍+星)', [r for r in hr if r['star'] and r['p_chg'] > 0 and r['h_chg'] < 0])
stat('不碰1: HKJC升水', [r for r in hr if r['h_chg'] > 0])
stat('不碰2: 平博掉水', [r for r in pr if r['p_chg'] < 0])
stat('双重不碰(HKJC升+平博掉)', [r for r in hr if r['h_chg'] > 0 and r['p_chg'] < 0])
stat('排除不碰后', [r for r in hr if not (r['h_chg'] > 0 or r['p_chg'] < 0)])
stat('排除不碰后+★', [r for r in hr if r['star'] and not (r['h_chg'] > 0 or r['p_chg'] < 0)])
