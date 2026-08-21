# -*- coding: utf-8 -*-
"""生成 8/19 清单回填复盘 md：清单原文 + 实际比分 + 命中/盈亏"""
import json, re

SRC = 'tp2_0819.md'
OUT = 'docs/review_20260819.md'
RESULTS = 'docs/data/results.json'

d = json.load(open(RESULTS, encoding='utf-8'))
ALIAS = {'San Diego FC': '聖地亞哥', '白帽': '白浪', '休斯頓': '休斯敦'}
def norm(s):
    for k, v in ALIAS.items(): s = s.replace(k, v)
    return s.replace('(中)', '').strip()
rmap = {}
for r in d['matches']:
    rmap[(norm(str(r.get('home_team', ''))), norm(str(r.get('away_team', ''))))] = r
for k, r in list(rmap.items()):
    if k in rmap and rmap[k].get('score') and not rmap.get(k):
        pass
# 重复记录优先有比分者
keys = {}
for r in d['matches']:
    k = (norm(str(r.get('home_team', ''))), norm(str(r.get('away_team', ''))))
    if k not in keys or (r.get('score') and not keys[k].get('score')):
        keys[k] = r
rmap = keys

def score_of(r):
    if not r: return None
    sc = str(r.get('score') or '').strip().replace(' ', '')
    mm = re.match(r'^(\d+)[-:](\d+)$', sc)
    if not mm: return None
    h, a = int(mm.group(1)), int(mm.group(2))
    return ('主' if h > a else ('客' if a > h else '平')), h, a

lines = open(SRC, encoding='utf-8').read().splitlines()
secs = {'甜点区客胜': [], '高置信': [], '客客客': []}
sec = None
for i, l in enumerate(lines):
    if '①🎯' in l: sec = '甜点区客胜'; continue
    if '②高置信' in l: sec = '高置信'; continue
    if '③三方一致' in l: sec = '客客客'; continue
    if not sec: continue
    m = re.match(r'^(\d{2}-\d{2} \d{2}:\d{2}) \[([^\]]+)\] (.+?) vs (.+?)(?:\s+→[主平客])?(?:\s+[★🎯])?(?:\s+[⚠⚡🚫\ufe0f]+避雷.*)?$', l)
    if not m: continue
    home, away = m.group(3).strip(), m.group(4).strip()
    dm = re.search(r'→(主|平|客)', l)
    direction = {'主': 'home', '平': 'draw', '客': 'away'}[dm.group(1)] if dm else 'away'
    nxt = lines[i+1] if i+1 < len(lines) else ''
    nxt2 = lines[i+2] if i+2 < len(lines) else ''
    nxtall = nxt + '\n' + nxt2
    odds = None
    if sec == '甜点区客胜':
        om = re.search(r'HKJC客胜 ([\d.]+)', nxt)
        if om: odds = float(om.group(1))
    elif sec == '高置信':
        om = re.search(r'HKJC 初/即: [\d./-]+ → ([\d.]+)/([\d.]+)/([\d.]+)', nxtall)
        if om:
            v = {'主': 0, '平': 1, '客': 2}[dm.group(1)] if dm else 2
            odds = float(om.group(v + 1))
    else:
        om = re.search(r'HKJC客胜 ([\d.]+)', nxt)
        if om: odds = float(om.group(1))
    r = rmap.get((norm(home), norm(away)))
    res = score_of(r)
    secs[sec].append({
        'time': m.group(1), 'event': m.group(2), 'home': home, 'away': away,
        'direction': direction, 'odds': odds, 'raw': l.strip(), 'r': r, 'res': res,
        'flag': '🚫避雷' if '避雷' in l else ('🎯' if '🎯' in l else '')
    })

CNSIDE = {'home': '主', 'draw': '平', 'away': '客'}
total_hit = total_done = total_pnl = 0

def fmt_row(p):
    global total_hit, total_done, total_pnl
    res, odds = p['res'], p['odds']
    if res:
        side, h, a = res
        hit = (side == CNSIDE[p['direction']])
        pnl = (odds - 1.0) if (hit and odds) else (-1.0 if not hit else 0.0)
        total_done += 1
        if hit:
            total_hit += 1
            mark = '✅'
        else:
            mark = '❌'
        total_pnl += pnl
        return f"| {p['time']} | {p['home']} vs {p['away']} | {CNSIDE[p['direction']]} | {odds if odds else '—'} | **{h}-{a}** | {mark} | {pnl:+.2f} |"
    else:
        if p['r'] is not None and not str(p['r'].get('score') or '').strip():
            note = '⚠️ 数据源未收录赛果'
        else:
            note = '⚠️ 赛果待出'
        return f"| {p['time']} | {p['home']} vs {p['away']} | {CNSIDE[p['direction']]} | {odds if odds else '—'} | — | ⏳ | — |"

def sec_block(name, picks):
    done = [p for p in picks if p['res']]
    n_hit = sum(1 for p in done if (p['res'][0] == CNSIDE[p['direction']]))
    pnl = 0.0
    for p in done:
        side, h, a = p['res']
        hit = (side == CNSIDE[p['direction']])
        pnl += (p['odds'] - 1.0) if (hit and p['odds']) else (-1.0 if not hit else 0.0)
    rate = f"{100*n_hit/len(done):.0f}%" if done else '—'
    out = [f"### {name}（清单 {len(picks)} 场 · 完赛 {len(done)} · 命中 {n_hit} · **{rate}** · 盈亏 **{pnl:+.2f}**）", ""]
    out.append("| 时间 | 比赛 | 方向 | 赔率 | 比分 | 结果 | 盈亏 |")
    out.append("|---|---|---|---|---|---|---|")
    for p in picks:
        out.append(fmt_row(p))
    out.append("")
    return '\n'.join(out)

md = []
md.append("# 8/19 推荐清单 · 赛果回填复盘")
md.append("")
md.append(f"> 清单版本：8/19 最终版（16:23）｜赛果源：results.json（{len(d['matches'])} 场）｜生成：2026-08-20")
md.append("")
md.append("> 赔率口径：①③段 HKJC客胜赔率；②段 HKJC 即赔（按推荐方向取值）")
md.append("")

for name in ['甜点区客胜', '高置信', '客客客']:
    md.append(sec_block(name, secs[name]))

md.append("## 汇总")
md.append("")
md.append(f"- 完赛 {total_done} 场 · 命中 {total_hit} 场 · 胜率 **{100*total_hit/total_done:.1f}%** · 均注净盈亏 **{total_pnl:+.2f}**")
md.append(f"- 未回填 {sum(1 for s in secs.values() for p in s if not p['res'])} 场：数据源未收录赛果（鳥取飛翔、蘇維埃之翼、喀山紅寶石、達馬克、吉達裏比亞）")
md.append("")
md.append("## 历史基准对照（1830 场）")
md.append("")
md.append("| 信号 | 昨日实际 | 历史基准 | 偏离 |")
md.append("|---|---|---|---|")
md.append("| 甜点区客胜 | 30% | **69.2%**（156 场） | **−39pp ⚠️ 单日极端偏离** |")
md.append("| └ 非美洲场 | 33%（1/3） | **72.6%**（135 场） | −40pp |")
md.append("| └ 美洲场 | 29%（2/7） | 47.6%（21 场） | −19pp |")
md.append("| 高置信方向 | 57.9% | 59.5% 总基准 | −2pp 正常 |")
md.append("| 客客客（低赔客胜） | 75% | **65.4%**（254 场） | +10pp 吻合 |")

open(OUT, 'w', encoding='utf-8').write('\n'.join(md))
print('已生成:', OUT)
print(f'完赛 {total_done} / 命中 {total_hit} / 盈亏 {total_pnl:+.2f}')
