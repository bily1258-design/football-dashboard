#!/usr/bin/env python3
"""分析DB队名 vs 500.com队名差异"""
import sqlite3, urllib.request, re

ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# 获取所有500.com页面的队名
print("=== 收集500.com队名 ===")
page_teams = {}  # team -> count

for url, src in [
    ('https://live.500.com/wanchang.php', 'wanchang'),
    ('https://live.500.com/weekfixture.php', 'weekfixture'),
]:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': ua})
        raw = urllib.request.urlopen(req, timeout=20).read()
        pat = rb'gy="([^"]*)"'
        for gy_bytes in re.findall(pat, raw):
            gy = gy_bytes.decode('gbk', errors='replace')
            parts = gy.split(',')
            if len(parts) >= 3:
                for name in [parts[1].strip(), parts[2].strip()]:
                    page_teams[name] = page_teams.get(name, 0) + 1
        print(f'  {src}: {len(raw)}字节')
    except Exception as e:
        print(f'  {src} 失败: {e}')

# 获取DB队名
conn = sqlite3.connect('data/football.db')
cur = conn.cursor()
cur.execute("SELECT DISTINCT home_team FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0")
db_homes = {r[0] for r in cur.fetchall()}
cur.execute("SELECT DISTINCT away_team FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0")
db_aways = {r[0] for r in cur.fetchall()}
db_teams = sorted(db_homes | db_aways)
conn.close()

print(f"\n=== DB中缺fid的队名: {len(db_teams)}个 ===")

# 检查每个DB队名是否在500.com页面上（包含、被包含、别名）
def normalize(n):
    return n.replace(' ', '').replace('&amp;', '&').replace('（', '(').replace('）', ')')

def matches_page(db_name, page_name):
    dn = normalize(db_name)
    pn = normalize(page_name)
    if dn == pn:
        return '精确'
    if dn in pn:
        return f'页面包含DB ({pn})'
    if pn in dn:
        return f'DB包含页面 ({pn})'
    return None

def best_match(db_name, page_teams):
    dn = normalize(db_name)
    best = None
    best_score = 0
    for pt in page_teams:
        pn = normalize(pt)
        if dn == pn:
            return ('精确', pt)
        if dn in pn and len(dn) / max(len(pn), 1) >= 0.4:
            score = len(dn) / max(len(pn), 1)
            if score > best_score:
                best = pt
                best_score = score
        if pn in dn and len(pn) / max(len(dn), 1) >= 0.4:
            score = len(pn) / max(len(dn), 1)
            if score > best_score:
                best = pt
                best_score = score
        # 编辑距离简单版
        common = len(set(dn) & set(pn))
        total = len(set(dn) | set(pn))
        if total > 0 and common / total >= 0.6:
            if best_score < 0.7:
                best = pt
                best_score = 0.7
    if best:
        return ('近似', best)
    return (None, None)

unmatchable = []
for dt in db_teams:
    mt, matched = best_match(dt, page_teams)
    if mt:
        print(f'  ✅ {dt} -> {mt}: {matched}')
    else:
        unmatchable.append(dt)

print(f"\n=== 完全在500.com页面上找不到的队名: {len(unmatchable)}个 ===")
for u in sorted(unmatchable):
    print(f'  {u}')
