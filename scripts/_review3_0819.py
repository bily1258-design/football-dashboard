# -*- coding: utf-8 -*-
# 8/19 今日清单复盘: 回填比分 -> 判定命中 -> 特征分析(找相同点命中点)
import json, re, statistics

lines = open('/data/data/com.termux/files/home/football-dashboard/tp2_0819.md', encoding='utf-8').read().splitlines()

# ---------- 解析三段 ----------
picks = []
sec = None
for i, l in enumerate(lines):
    if '①🎯' in l: sec = '甜点区客胜'
    elif '②高置信' in l: sec = '高置信'
    elif '③三方一致' in l: sec = '客客客'
    m = re.match(r'^(\d{2}-\d{2} \d{2}:\d{2}) \[([^\]]+)\] (.+?) vs (.+?)(?:\s+→[主平客])?(?:\s+[★🎯])?(?:\s+[⚠⚡🚫\ufe0f]+避雷.*)?$', l)
    if not m or not sec: continue
    home, away = m.group(3).strip(), m.group(4).strip()
    direction = 'away'
    ev = prob = ts_side = ts_val = odds = None
    avoid = ('避雷' in l)
    # 下一行特征
    nxt = lines[i+1] if i+1 < len(lines) else ''
    nxt2 = lines[i+2] if i+2 < len(lines) else ''
    nxtall = nxt + '\n' + nxt2
    if sec == '甜点区客胜':
        mm = re.search(r'HKJC客胜 ([\d.]+)', nxt)
        if mm: odds = float(mm.group(1))
        mm = re.search(r'模型概率 (\d+)%', nxt)
        if mm: prob = int(mm.group(1))
        mm = re.search(r'EV ([\d.-]+)', nxt)
        if mm: ev = float(mm.group(1))
    elif sec == '高置信':
        dm = re.search(r'→(主|平|客)', m.group(0))
        if dm: direction = {'主':'home','平':'draw','客':'away'}[dm.group(1)]
        mm = re.search(r'model (\d+)%', nxt)
        if mm: prob = int(mm.group(1))
        mm = re.search(r'EV ([\d.-]+)', nxt)
        if mm: ev = float(mm.group(1))
        mm = re.search(r'TS (主|平|客)(\d+)%', nxt)
        if mm: ts_side, ts_val = mm.group(1), int(mm.group(2))
        om = re.search(r'HKJC 初/即: [\d./-]+ → ([\d.]+)/([\d.]+)/([\d.]+)', nxtall)
        if om:
            v = {'主':0,'平':1,'客':2}[dm.group(1)] if dm else 2
            odds = float(om.group(v+1))
    elif sec == '客客客':
        mm = re.search(r'HKJC客胜 ([\d.]+)', nxt)
        if mm: odds = float(mm.group(1))
        mm = re.search(r'LGBM客概率 (\d+)%', nxt)
        if mm: prob = int(mm.group(1))
        mm = re.search(r'TS平 (\d+)%', nxt)
        if mm: ts_side, ts_val = '平', int(mm.group(1))
    picks.append(dict(sec=sec, time=m.group(1), league=m.group(2), home=home, away=away,
                      direction=direction, odds=odds, prob=prob, ev=ev,
                      ts_side=ts_side, ts_val=ts_val, avoid=avoid))

# ---------- results.json 匹配 ----------
d = json.load(open('/data/data/com.termux/files/home/football-dashboard/docs/data/results.json'))
ALIAS = {'San Diego FC':'聖地亞哥','白帽':'白浪','休斯頓':'休斯敦'}
def norm(s):
    for k, v in ALIAS.items(): s = s.replace(k, v)
    return s.replace('(中)','').strip()
rmap = {}
for r in d['matches']:
    key = (norm(str(r.get('home_team',''))), norm(str(r.get('away_team',''))))
    old = rmap.get(key)
    if old is None or (not (old.get('score') or '').strip() and (r.get('score') or '').strip()):
        rmap[key] = r

def score_of(r):
    sc = (r.get('score') or '').strip().replace(' ', '')
    mm = re.match(r'^(\d+)[-:](\d+)$', sc)
    if not mm: return None
    h, a = int(mm.group(1)), int(mm.group(2))
    return ('home' if h > a else 'away' if a > h else 'draw', h, a)

for p in picks:
    r = rmap.get((norm(p['home']), norm(p['away'])))
    p['result'] = score_of(r) if r else None
    p['score_raw'] = (r.get('score') or '').strip() if r else None
    if p['result']:
        side, h, a = p['result']
        p['hit'] = (side == p['direction'])
        p['pnl'] = (p['odds'] - 1.0) if (p['hit'] and p['odds']) else (-1.0 if not p['hit'] else 0.0)
    else:
        p['hit'] = None
        p['pnl'] = None

# ---------- 输出: 逐场 ----------
for sec in ['甜点区客胜', '高置信', '客客客']:
    ps = [p for p in picks if p['sec'] == sec]
    done = [p for p in ps if p['hit'] is not None]
    n_hit = sum(1 for p in done if p['hit'])
    pnl = sum(p['pnl'] for p in done)
    print(f"\n## {sec}: 清单{len(ps)}场, 完赛{len(done)}, 命中{n_hit}, 胜率{100*n_hit/len(done) if done else 0:.1f}%, 净盈亏{pnl:+.2f}")
    for p in ps:
        if p['hit'] is None:
            tag = '无比分' if p['score_raw'] is None else '⚠️未匹配'
            print(f"  {tag} {p['time']} {p['home']} vs {p['away']} ->{p['direction']} @{p['odds']} 比分{p['score_raw'] or '-'}")
        else:
            mark = '✓' if p['hit'] else '✘'
            print(f"  {mark} {p['time']} {p['home']} vs {p['away']} ->{p['direction']} @{p['odds']} 比分{p['score_raw']} 盈亏{p['pnl']:+.2f}")

# ---------- 特征分析 ----------
done = [p for p in picks if p['hit'] is not None]
n, nh = len(done), sum(1 for p in done if p['hit'])
print(f"\n=== 特征分析(完赛{n}场, 命中{nh}, 总体{100*nh/n:.1f}%) ===")

def rate(ps):
    ps = [p for p in ps if p['hit'] is not None]
    if not ps: return None
    h = sum(1 for p in ps if p['hit'])
    pnl = sum(p['pnl'] for p in ps)
    return f"{100*h/len(ps):.0f}%({h}/{len(ps)}) 盈亏{pnl:+.1f}"

print(f"\n[避雷] 避雷场: {rate([p for p in done if p['avoid']])} | 非避雷: {rate([p for p in done if not p['avoid']])}")
buckets = [(0, 1.7, '<1.7'), (1.7, 2.5, '1.7-2.5'), (2.5, 3.5, '2.5-3.5'), (3.5, 99, '>3.5')]
print("[赔率区间]")
for lo, hi, lab in buckets:
    ps = [p for p in done if p['odds'] and lo <= p['odds'] < hi]
    if ps: print(f"  {lab}: {rate(ps)}")
buckets = [(0, 35, '<35%'), (35, 40, '35-40%'), (40, 45, '40-45%'), (45, 100, '>45%')]
print("[模型概率]")
for lo, hi, lab in buckets:
    ps = [p for p in done if p['prob'] is not None and lo <= p['prob'] < hi]
    if ps: print(f"  {lab}: {rate(ps)}")
print("[EV区间]")
for lo, hi, lab in [(0, 0.2, '<0.2'), (0.2, 0.5, '0.2-0.5'), (0.5, 99, '>0.5')]:
    ps = [p for p in done if p['ev'] is not None and lo <= p['ev'] < hi]
    if ps: print(f"  {lab}: {rate(ps)}")
print("[联赛组]")
groups = {'欧战': ('歐冠','歐羅巴','歐協聯','解放者','南美杯','RUS'), '主流': ('西甲','美職','巴西乙','阿根廷杯','韓國','JE'), '其他': ()}
for g, kws in groups.items():
    ps = [p for p in done if any(k in p['league'] for k in kws)]
    print(f"  {g}: {rate(ps)}")
rest = [p for p in done if not any(k in p['league'] for g, kws in groups.items() for k in kws)]
print(f"  其他: {rate(rest)}")
print("[时段]")
for lo, hi, lab in [(0, 6, '00-06'), (6, 12, '06-12'), (12, 18, '12-18'), (18, 24, '18-24')]:
    ps = [p for p in done if lo <= int(p['time'][6:8]) < hi]
    if ps: print(f"  {lab}: {rate(ps)}")
print("[★标记(客赔<2.0且TS平<25%)] ★: {} | 无★: {}".format(
    rate([p for p in done if p['sec']=='客客客' and '★' in str(p.get('_star',''))]) if False else rate([p for p in done if p['sec']=='客客客' and p['odds'] is not None and p['odds']<2.0]),
    rate([p for p in done if not (p['sec']=='客客客' and p['odds'] is not None and p['odds']<2.0)])))
# 命中场 vs 未命中场 均值
for feat, lab in [('odds','赔率'), ('prob','概率'), ('ev','EV')]:
    hv = [p[feat] for p in done if p['hit'] and p[feat] is not None]
    mv = [p[feat] for p in done if not p['hit'] and p[feat] is not None]
    if hv and mv:
        print(f"[{lab}均值] 命中场 {statistics.mean(hv):.2f} vs 未命中场 {statistics.mean(mv):.2f}")
