import json

with open('data/matches_20260721.json') as f:
    d = json.load(f)
    ms = d.get('matches', [])
    print(f'21号beidan源: {len(ms)} 场')
    for m in ms:
        mt = m.get('match_time','')
        print(f'  fid={m.get("fid","")} 日期={mt[:10]:15s} 时间={mt[-5:]} {m["home_team"]:25s} vs {m["away_team"]:25s} score={m.get("score","-")}')

with open('data/matches_20260720.json') as f:
    d = json.load(f)
    ms20 = d.get('matches', [])
    print(f'\n20号beidan源: {len(ms20)} 场')

fids21 = {m.get('fid') for m in ms}
fids20 = {m.get('fid') for m in ms20}
common = fids20 & fids21
print(f'\n相同fid: {len(common)}')
for fid in sorted(common):
    m20 = next(m for m in ms20 if m.get('fid')==fid)
    m21 = next(m for m in ms if m.get('fid')==fid)
    print(f'  fid={fid}')
    print(f'    20号: mt={m20.get("match_time","")} score={m20.get("score","-")}')
    print(f'    21号: mt={m21.get("match_time","")} score={m21.get("score","-")}')
