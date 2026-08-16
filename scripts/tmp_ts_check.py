#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 TS 数据: 覆盖率/方向映射/一致性"""
import json

with open('docs/data/results.json', encoding='utf-8') as f:
    data = json.load(f)
matches = data.get('matches', data) if isinstance(data, dict) else data

print(f'总场次: {len(matches)}')

# 1. TS 覆盖率
none_ts = [m for m in matches if m.get('ts_win') is None]
print(f'ts_win=None: {len(none_ts)} 场 ({len(none_ts)/len(matches):.1%})')

with_ts = [m for m in matches if m.get('ts_win') is not None]
print(f'有TS: {len(with_ts)} 场')

# 2. TS 概率合法性
bad = [m for m in with_ts if not (0 <= m['ts_win'] <= 1) or not (0 <= m['ts_draw'] <= 1) or not (0 <= m['ts_loss'] <= 1)]
print(f'概率越界: {len(bad)} 场')
for m in bad[:5]:
    print(f'  {m.get("match_time")} {m.get("home_team")} vs {m.get("away_team")} ts={m.get("ts_win")},{m.get("ts_draw")},{m.get("ts_loss")}')

# 3. 概率和≈1?
import math
sum_off = [m for m in with_ts if abs(m['ts_win'] + m['ts_draw'] + m['ts_loss'] - 1) > 0.01]
print(f'三概率和≠1(>0.01): {len(sum_off)} 场')
for m in sum_off[:8]:
    print(f'  {m.get("match_time")} ts={m.get("ts_win")}+{m.get("ts_draw")}+{m.get("ts_loss")}={m.get("ts_win")+m.get("ts_draw")+m.get("ts_loss"):.4f}')

# 4. 方向映射一致性: ts_win 是否确实对应"主胜概率"
# 检查: ts_win 大时主队是否真的赢更多
def parse_score(sc):
    try:
        h, a = sc.split('-')
        return int(h), int(a)
    except Exception:
        return None


for cutoff in [0.3, 0.4, 0.5, 0.6]:
    sub = [m for m in with_ts if m.get('ts_win', 0) >= cutoff and parse_score(m.get('score'))]
    if not sub:
        continue
    wins = sum(1 for m in sub if parse_score(m['score'])[0] > parse_score(m['score'])[1])
    print(f'TS主胜>={cutoff:.0%}: {len(sub)}场 主队实胜率 {wins/len(sub):.1%}')

print()
print('=== TS 平局概率 vs 实际 ===')
for cutoff in [0.3, 0.4, 0.5]:
    sub = [m for m in with_ts if m.get('ts_draw', 0) >= cutoff and parse_score(m.get('score'))]
    if not sub:
        continue
    draws = sum(1 for m in sub if parse_score(m['score'])[0] == parse_score(m['score'])[1])
    print(f'TS平>={cutoff:.0%}: {len(sub)}场 实际平局率 {draws/len(sub):.1%}')

print()
print('=== TS 客胜概率 vs 实际 ===')
for cutoff in [0.3, 0.4, 0.5]:
    sub = [m for m in with_ts if m.get('ts_loss', 0) >= cutoff and parse_score(m.get('score'))]
    if not sub:
        continue
    awins = sum(1 for m in sub if parse_score(m['score'])[0] < parse_score(m['score'])[1])
    print(f'TS客胜>={cutoff:.0%}: {len(sub)}场 客队实胜率 {awins/len(sub):.1%}')

# 5. 检查 TS 是否被四舍五入成极端值
import collections
rounded = collections.Counter()
for m in with_ts:
    for k in ['ts_win', 'ts_draw', 'ts_loss']:
        v = m.get(k, 0)
        if v in (0.0, 0.25, 0.5, 0.75, 1.0):
            rounded[k] += 1
print()
print('整数值分布(可疑四舍五入):', dict(rounded))
