#!/usr/bin/env python3
"""批量从500.com ouzhi页面抓比分回填DB"""
import urllib.request, re, sqlite3, time, sys

def fetch_score_from_ouzhi(fid, delay=0.3):
    url = f'https://odds.500.com/fenxi/ouzhi-{fid}.shtml'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://odds.500.com/',
    })
    try:
        time.sleep(delay)
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('gbk', errors='replace')
        bf = re.search(r'class="odds_hd_bf"[^>]*>\s*<strong[^>]*>(\d+:\d+)', html)
        if bf:
            score = bf.group(1).replace(':', '-')
            hs, aw = score.split('-')
            outcome = '主胜' if int(hs) > int(aw) else '平局' if int(hs) == int(aw) else '客胜'
            return {'score': score, 'home_score': int(hs), 'away_score': int(aw), 'outcome': outcome}
        return None
    except Exception as e:
        return None

db_path = sys.argv[1] if len(sys.argv) > 1 else 'data/football.db'
date_filter = sys.argv[2] if len(sys.argv) > 2 else None

conn = sqlite3.connect(db_path)
cur = conn.cursor()

where = "WHERE (actual_outcome IS NULL OR actual_outcome = '') AND fid_500 IS NOT NULL"
if date_filter:
    where += f" AND date = '{date_filter}'"

cur.execute(f"""SELECT id, fid_500, home_team, away_team, date, kickoff_time
    FROM poisson_predictions {where} ORDER BY date, kickoff_time""")
matches = cur.fetchall()
print(f'待回填: {len(matches)}场')

updated = 0
failed = 0
for idx, (mid, fid, home, away, date, kt) in enumerate(matches):
    result = fetch_score_from_ouzhi(fid, delay=0.3)
    if result:
        cur.execute("""UPDATE poisson_predictions 
            SET actual_outcome = ?, home_score = ?, away_score = ?
            WHERE id = ?""",
            (result['outcome'], result['home_score'], result['away_score'], mid))
        updated += 1
        print(f'  [{idx+1}/{len(matches)}] ✅ {date} {kt or "无时间"} {home} vs {away} → {result["score"]} {result["outcome"]}')
    else:
        failed += 1
        if failed <= 10:
            print(f'  [{idx+1}/{len(matches)}] ❌ {date} {kt or "无时间"} {home} vs {away} (fid={fid}) 无比分')
    if (idx + 1) % 20 == 0:
        conn.commit()

conn.commit()
conn.close()
print(f'\n完成: 更新{updated}场, 无比分{failed}场')
