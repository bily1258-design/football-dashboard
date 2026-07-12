#!/usr/bin/env python3
"""移除所有无赔率的比赛（odds=0/0/0），只保留可计算EV/泊松的场次"""
import json
from collections import Counter

with open('docs/data/results.json') as f:
    d = json.load(f)

removed = 0
kept = 0
removed_detail = Counter()

new_matches = {}
for date, records in d['matches'].items():
    kept_records = []
    for r in records:
        ods = r.get('odds', {})
        if ods.get('w', 0) > 0:
            kept_records.append(r)
            kept += 1
        else:
            removed += 1
            removed_detail[r.get('source', '?')] += 1
    if kept_records:
        new_matches[date] = kept_records

d['matches'] = new_matches

# 更新 daily_stats
total = sum(len(v) for v in new_matches.values())
d['total_matches'] = total
d['_total'] = total

# 更新dates（如果某天没比赛了就移除）
d['dates'] = sorted(new_matches.keys())

# 重建 daily_stats
if 'daily_stats' in d:
    d['daily_stats'] = {dt: len(new_matches.get(dt, [])) for dt in d['dates']}

with open('docs/data/results.json', 'w') as f:
    json.dump(d, f, ensure_ascii=False)

print(f"保留: {kept} 场 (有赔率+EV/泊松)")
print(f"移除: {removed} 场 (无赔率)")
for src, cnt in removed_detail.most_common():
    print(f"  来源={src}: {cnt} 场")

# 验证
import os
size_kb = os.path.getsize('docs/data/results.json') / 1024
print(f"\n文件大小: {size_kb:.1f} KB")
