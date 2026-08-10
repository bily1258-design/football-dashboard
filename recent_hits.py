import json

d = json.load(open('docs/data/results.json'))
ms = d['matches']

def out_of(m):
    s = m.get('score', '')
    if not s:
        return None
    try:
        h, a = map(int, s.split('-'))
    except Exception:
        return None
    return ('home', h, a) if h > a else ('away', h, a) if a > h else ('draw', h, a)

hits = []
for m in ms:
    if not m.get('date', '').startswith(('2026-08-08', '2026-08-09', '2026-08-10')):
        continue
    bv = m.get('best_value')
    if not bv or bv.get('outcome') != 'away' or bv.get('ev', 0) <= 0.5:
        continue
    pc = m.get('pin_comparison') or {}
    cur = pc.get('current')
    if not cur or len(cur) < 3 or not (3 <= cur[2] <= 6):
        continue
    hits.append({
        'date': m['date'], 'league': m.get('event', ''),
        'home': m.get('home_team', ''), 'away': m.get('away_team', ''),
        'score': m.get('score', ''), 'odds': cur[2],
        'prob': bv.get('prob', 0), 'ev': bv.get('ev', 0),
        'out': out_of(m)
    })

hits.sort(key=lambda x: (x['date'], -x['ev']))
print(f"最近3天满足条件的场次: {len(hits)}场\n")
for h in hits:
    mark = ''
    if h['out']:
        r = h['out'][0]
        mark = '✅中' if r == 'away' else ('❌' + ('平' if r == 'draw' else '主'))
    print(f"{h['date']} [{h['league']}] {h['home']} vs {h['away']}"
          f"  | 比分 {h['score'] or '未开赛'} {mark}"
          f" | HKJC客胜赔率 {h['odds']} | 模型概率 {h['prob']*100:.0f}% | EV {h['ev']:.2f}")
