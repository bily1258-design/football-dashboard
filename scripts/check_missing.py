#!/usr/bin/env python3
"""检查哪些缺失技统的比赛可能有有效SID能抓取"""
import sqlite3
from collections import Counter

conn = sqlite3.connect('data/football.db')

rows = conn.execute("""
    SELECT pp.match_id, pp.date, pp.home_team, pp.away_team
    FROM poisson_predictions pp
    LEFT JOIN match_tech_stats mts 
        ON mts.home_team = pp.home_team AND mts.away_team = pp.away_team AND mts.date = pp.date
    WHERE pp.match_id IS NOT NULL AND pp.match_id != ''
        AND (LENGTH(pp.match_id) = 7 OR CAST(pp.match_id AS INTEGER) > 1000000)
        AND mts.home_team IS NULL
    ORDER BY pp.date DESC
""").fetchall()

total = len(rows)
print(f'缺失总计: {total}')

# 29/30前缀 + 2026-06以后
recent_valid = []
for mid, d, ht, at in rows:
    if (mid.startswith('29') or mid.startswith('30')) and d and d >= '2026-06':
        recent_valid.append((mid, d, ht, at))

print(f'\n29xxxxx/30xxxxx + >=2026-06 (可能有数据): {len(recent_valid)}')

if recent_valid:
    print(f'\n具体列表:')
    for mid, d, ht, at in recent_valid[:30]:
        print(f'  {mid} {d} {ht} vs {at}')
    if len(recent_valid) > 30:
        print(f'  ... 还有 {len(recent_valid)-30} 场')

# 按前缀+月份分组
groups = Counter()
for mid, d, ht, at in rows:
    prefix = mid[:4]
    mn = d[:7] if d else 'unknown'
    groups[f'{prefix}|{mn}'] += 1

print(f'\n=== 前缀分布(前20) ===')
for k, c in sorted(groups.items(), key=lambda x: -x[1])[:20]:
    print(f'  {k}: {c}场')

conn.close()
