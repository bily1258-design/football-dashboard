# -*- coding: utf-8 -*-
"""历史基准统计：results.json 全量场次，按实际可见信号分档统计命中率"""
import json, re
from collections import defaultdict

d = json.load(open('docs/data/results.json', encoding='utf-8'))
ms = d['matches']
print('总记录:', len(ms))

# 有赛果的场次
done = [m for m in ms if m.get('score') and re.match(r'^\d+\s*[-:]\s*\d+$', str(m['score']).strip())]
print('有赛果:', len(done))

def is_hit(m):
    h = str(m.get('hit') or '')
    if h.endswith('✓'): return True
    if h == '✘': return False
    return None

def side_of(m):
    p = str(m.get('prediction_cn') or '')
    return {'主胜': '主', '平局': '平', '客胜': '客'}.get(p)

def rate(rows):
    rows = [r for r in rows if is_hit(r) is not None]
    if not rows: return None
    n = sum(1 for r in rows if is_hit(r))
    return n, len(rows), 100.0 * n / len(rows)

def show(title, rows):
    st = rate(rows)
    if st is None:
        print(f'{title}: 无样本')
    else:
        n, t, p = st
        print(f'{title}: {p:.1f}% ({n}/{t})')

allst = rate(done)
print(f'\n=== 总基准: {allst[2]:.1f}% ({allst[0]}/{allst[1]}) ===\n')

# 1. 方向
for side in ['主', '平', '客']:
    show(f'方向={side}', [m for m in done if side_of(m) == side])

# 2. 赔率区间（用推荐方向的赔率）
def odds_of(m):
    key = {'主': 'odds_win', '平': 'odds_draw', '客': 'odds_loss'}.get(side_of(m))
    if not key: return None
    v = m.get(key)
    try: return float(v)
    except: return None

buckets = [(0, 1.3), (1.3, 1.5), (1.5, 1.8), (1.8, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, 10)]
print('--- 赔率区间(推荐方向赔率) ---')
for lo, hi in buckets:
    rows = [m for m in done if odds_of(m) is not None and lo <= odds_of(m) < hi]
    show(f'赔率 {lo}-{hi}', rows)

# 3. 甜点区客胜近似: 方向=客 且 赔率>=2.5
rows = [m for m in done if side_of(m) == '客' and odds_of(m) is not None and odds_of(m) >= 2.5]
print('\n--- 甜点区客胜近似(客+赔率>=2.5) ---')
show('全部', rows)
for lo, hi in [(2.5, 3.0), (3.0, 10)]:
    show(f'  赔率 {lo}-{hi}', [m for m in rows if lo <= odds_of(m) < hi])

# 4. 客客客近似: 方向=客 且 赔率<2.0
rows = [m for m in done if side_of(m) == '客' and odds_of(m) is not None and odds_of(m) < 2.0]
print('\n--- 低赔客胜(客+赔率<2.0) ---')
show('全部', rows)

# 5. 时段
print('\n--- 时段 ---')
def hour_of(m):
    t = str(m.get('match_time', ''))
    mm = re.search(r'(\d{2}):\d{2}$', t)
    return int(mm.group(1)) if mm else None
for lo, hi, label in [(18, 24, '18-24'), (0, 6, '00-06'), (6, 12, '06-12'), (12, 18, '12-18')]:
    rows = [m for m in done if hour_of(m) is not None and lo <= hour_of(m) < hi]
    show(f'时段 {label}', rows)

# 6. ★标记近似: 客胜 且 客赔<2.0 且 ts_draw<25%
print('\n--- ★标记近似(客+赔率<2.0+TS平<25%) ---')
def ts_draw_of(m):
    try: return float(m.get('ts_draw'))
    except: return None
rows = [m for m in done if side_of(m) == '客' and odds_of(m) is not None and odds_of(m) < 2.0
        and ts_draw_of(m) is not None and ts_draw_of(m) < 25]
show('全部', rows)
rows2 = [m for m in rows if odds_of(m) is not None and odds_of(m) >= 1.7]
show('  且赔率>=1.7', rows2)

# 7. 联赛组
print('\n--- 联赛组 ---')
def league_group(m):
    lg = str(m.get('league') or m.get('event') or '')
    if any(k in lg for k in ['美職', '巴西', '智利', '阿根廷', '墨西哥', '哥倫比亞', '厄瓜多', '祕魯', '烏拉圭', '巴拉圭', '委內瑞拉']):
        return '美洲'
    if any(k in lg for k in ['歐', '欧', '英', '西', '意', '德', '法', '蘇', '葡', '荷', '比', '土', '希', '俄']):
        return '欧洲'
    if any(k in lg for k in ['日', '韓', '中', '澳', '越', '泰', '馬', '印', '新加坡', '港', '台']):
        return '亚洲'
    return '其他'
for g in ['美洲', '欧洲', '亚洲', '其他']:
    show(f'组={g}', [m for m in done if league_group(m) == g])

# 8. 近期按天
print('\n--- 按日期(近期10天) ---')
dates = sorted({m.get('date') for m in done})[-10:]
for dt in dates:
    show(dt, [m for m in done if m.get('date') == dt])
