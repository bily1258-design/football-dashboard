import json
from collections import Counter
d = json.load(open('docs/data/results.json'))
MS = d['matches']

def argmax3(w, dr, l):
    m = max(w, dr, l)
    return 0 if m == w else (1 if m == dr else 2)

def parse_score(s):
    try:
        parts = s.replace('-',':').split(':')
        return int(parts[0]), int(parts[1])
    except: return None

recent = [m for m in MS if m.get('date','')[-5:] in ('08-09','08-10','08-11')]
print(f'最近3天总场次: {len(recent)}')

rows = []
for m in recent:
    sc = parse_score(m.get('score') or '')
    if sc is None: continue
    h, a = sc
    actual = 0 if h > a else (1 if h == a else 2)
    md = argmax3(m.get('model_win',0), m.get('model_draw',0), m.get('model_loss',0))
    tsd = argmax3(m.get('ts_win',0), m.get('ts_draw',0), m.get('ts_loss',0))
    w = round(m.get('importance_weight',0), 2)
    rows.append((m, actual, md, tsd, w))

done = [r for r in rows if True]
print(f'有赛果: {len(done)}')

def report(name, sub):
    if not sub: 
        print(f'{name}: 0场'); return
    hit = sum(1 for r in sub if r[1] == r[2])  # 模型方向命中
    ts_hit = sum(1 for r in sub if r[1] == r[3])
    print(f'{name}: {len(sub)}场 | 模型方向命中 {hit} ({hit/len(sub)*100:.1f}%) | TS方向命中 {ts_hit} ({ts_hit/len(sub)*100:.1f}%)')

# 分组
same = [r for r in done if r[2] == r[3]]
diff = [r for r in done if r[2] != r[3]]
report('【模型==TS 同方向】全部', same)
report('【模型!=TS 反向】全部(对照)', diff)
print()
report('同方向 + ⚡=1.14', [r for r in same if r[4] == 1.14])
report('同方向 + ⚡>=1.14', [r for r in same if r[4] >= 1.14])
report('同方向 + ⚡<1.14 (对照)', [r for r in same if r[4] < 1.14])
print()
report('反向 + ⚡=1.14 (对照)', [r for r in diff if r[4] == 1.14])
report('反向 + ⚡>=1.14 (对照)', [r for r in diff if r[4] >= 1.14])

# ⚡=1.14 且同方向的具体场次
print('\n--- ⚡=1.14 且 模型==TS 场次明细 ---')
for r in [r for r in same if r[4] == 1.14]:
    m, actual, md, tsd, w = r
    d3 = ['主','平','客']
    print(f"{m.get('date')} {m.get('event','')[:14]:14s} {m.get('home_team','')[:10]:10s}vs{m.get('away_team','')[:10]:10s} 方向{d3[md]} 赛果:{m.get('score')} {'✓' if actual==md else '✗'}")
