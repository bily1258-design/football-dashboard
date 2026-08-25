#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日复盘生成器：today_picks.md 清单原格式 + 回填比分（固定文件名每日覆盖）
用法:
  python3 scripts/gen_daily_review.py --date 2026-08-19 --picks docs/picks_20260819.md
  python3 scripts/gen_daily_review.py --date 2026-08-19   # 缺省读 docs/today_picks.md
输出: docs/推荐清单·赛果回填复盘.md（与清单同格式代码块, 每场比赛行尾追加实际比分）
"""
import argparse, json, re
from datetime import datetime, timedelta

try:
    from opencc import OpenCC
    _cc = OpenCC('t2s')  # 繁体清单 → 简体 results
except ImportError:
    _cc = None

# 别名归一（两阶段）: pre=原文替换(英文名), post=t2s 简体后再替换(同音字/俗称)
ALIAS_PRE = {'San Diego FC': '圣迭戈'}
ALIAS_POST = {'圣地亚哥': '圣迭戈', '休斯顿': '休斯敦', '白帽': '白浪', '波特诺山丘': '波特诺'}

def norm(name):
    n = name.strip()
    for k, v in ALIAS_PRE.items():
        n = n.replace(k, v)
    if _cc:
        n = _cc.convert(n)
    for k, v in ALIAS_POST.items():
        n = n.replace(k, v)
    return n

# 比赛行: 时间 [联赛] 主 vs 客 [→方向] [★/🎯] [避雷标记]
LINE_RE = re.compile(
    r'^(\d{2}-\d{2} \d{2}:\d{2}) \[([^\]]*)\] (.+?) vs (.+?)'
    r'(?:\s+→(主|平|客))?(?:\s+[★🎯])?(?:\s+(?:[⚠⚡🚫\ufe0f]+避雷.*))?$')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True, help='清单日 YYYY-MM-DD')
    ap.add_argument('--picks', default='docs/today_picks.md')
    ap.add_argument('--out', default='docs/推荐清单·赛果回填复盘.md')
    args = ap.parse_args()

    d0 = datetime.strptime(args.date, '%Y-%m-%d')
    win_start = d0 + timedelta(hours=12)
    win_end = win_start + timedelta(hours=24)

    picks = open(args.picks, encoding='utf-8').read()
    # 只处理 ```text 代码块
    mblock = re.search(r'```text\n(.*?)\n```', picks, re.S)
    body = mblock.group(1) if mblock else picks

    results = json.load(open('docs/data/results.json', encoding='utf-8'))
    by_key = {}
    for m in results['matches']:
        mt = m.get('match_time', '')
        try:
            t = datetime.strptime(mt, '%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            continue
        if not (win_start <= t < win_end):
            continue
        key = (norm(m.get('home_team', '')), norm(m.get('away_team', '')))
        cur = by_key.get(key)
        if cur is None or (m.get('score') and not cur.get('score')):
            by_key[key] = m  # 优先有比分记录

    out_lines = []
    total = hit = 0
    pnl = 0.0
    has_pnl = 0
    changed = 0
    lines = body.split('\n')
    sec = None  # 当前档位段: ②客客客无箭头隐含客, ③胜胜胜无箭头隐含主
    for i, line in enumerate(lines):
        if re.match(r'^[①②③]', line):
            sec = line[0]
        mm = LINE_RE.match(line)
        if mm:
            tstr, league, home, away = mm.group(1), mm.group(2), mm.group(3), mm.group(4)
            side = mm.group(5) or ('主' if sec == '③' else '客')  # 无箭头行按段取隐含方向
            key = (norm(home), norm(away))
            m = by_key.get(key)
            if m and m.get('score'):
                sc = str(m['score']).strip().replace(' ', '')
                sm = re.match(r'^(\d+)[-:](\d+)$', sc)
                if sm:
                    h, a = int(sm.group(1)), int(sm.group(2))
                    actual = '主' if h > a else ('平' if h == a else '客')
                    ok = (actual == side)
                    total += 1
                    if ok:
                        hit += 1
                        mark = '✓'
                    else:
                        mark = '✘'
                    # 盈亏: 取比赛行后 2 行内赔率（甜点/客客客: HKJC客胜; 高置信: HKJC 初/即 即赔方向）
                    odds = None
                    tail = '\n'.join(lines[i + 1:i + 3])
                    odds_m = re.search(r'HKJC客胜\s+([\d.]+)', tail)
                    if odds_m:
                        odds = float(odds_m.group(1))
                    else:
                        hm = re.search(r'HKJC 初/即:\s*([\d.]+)/([\d.]+)/([\d.]+)\s*→\s*([\d.]+)/([\d.]+)/([\d.]+)', tail)
                        if hm:
                            idx = {'主': 3, '平': 4, '客': 5}[side]
                            v = hm.group(idx + 1)
                            if v != '-':
                                odds = float(v)
                    if odds:
                        has_pnl += 1
                        pnl += (odds - 1.0) if ok else -1.0
                    out_lines.append(f"{line} | 实际: {sc} {mark}")
                    changed += 1
                    continue
            out_lines.append(f"{line} | ⏳ 未收录")
            changed += 1
        else:
            out_lines.append(line)

    pct = f"{hit / total * 100:.0f}%" if total else "0%"
    header = (f"# 📋 推荐清单 · 赛果回填复盘（{args.date}）\n\n"
              f"> 清单: {args.picks} ｜ 完赛 {total}/{total + sum(1 for l in out_lines if '⏳' in l)}"
              f" · 命中 {hit}（{pct} 完赛场次）"
              f" ｜ 净盈亏 {pnl:+.2f}（{has_pnl} 场有赔率）\n\n```text\n")
    content = header + '\n'.join(out_lines) + '\n```\n'
    open(args.out, 'w', encoding='utf-8').write(content)
    print(f"已生成: {args.out}\n完赛 {total} / 命中 {hit} ({pct}) / 净盈亏 {pnl:+.2f} / 回填行 {changed}")

if __name__ == '__main__':
    main()
