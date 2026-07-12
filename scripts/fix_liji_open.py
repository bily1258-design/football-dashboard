#!/usr/bin/env python3
"""重新抓取缺利记初盘的未来比赛的AH/OU数据"""
import sys
import sqlite3
sys.path.insert(0, 'scripts')
from fetch_500com_odds import fetch_ah, fetch_ou

db_path = 'data/football.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# 找缺利记初盘的fid
c.execute('''
SELECT DISTINCT fid_500 
FROM poisson_predictions 
WHERE kickoff_time >= datetime("now", "-30 days") 
  AND liji_handicap IS NOT NULL 
  AND liji_open_handicap IS NULL
  AND fid_500 IS NOT NULL
ORDER BY kickoff_time
''')
fids_ah = [r[0] for r in c.fetchall()]

c.execute('''
SELECT DISTINCT fid_500 
FROM poisson_predictions 
WHERE kickoff_time >= datetime("now", "-30 days") 
  AND liji_ou_line IS NOT NULL 
  AND liji_ou_open_line IS NULL
  AND fid_500 IS NOT NULL
ORDER BY kickoff_time
''')
fids_ou = [r[0] for r in c.fetchall()]

all_fids = sorted(set(fids_ah + fids_ou))
print(f"需重抓利记AH初盘: {len(fids_ah)} 场")
print(f"需重抓利记OU初盘: {len(fids_ou)} 场")
print(f"合计: {len(all_fids)} 场")
print()

updated_ah = 0
updated_ou = 0

for fid in all_fids:
    print(f"fid={fid}...", end=" ")
    
    ah = fetch_ah(fid, 651)
    ou = fetch_ou(fid, 651)
    
    ah_ok = ou_ok = False
    sets = []
    params = []
    
    if ah:
        has_open = 'open' in ah
        if 'close' in ah:
            sets.extend(['liji_handicap=?','liji_home_water=?','liji_away_water=?'])
            params.extend([ah['close']['handicap'],ah['close']['home_water'],ah['close']['away_water']])
        if has_open:
            sets.extend(['liji_open_handicap=?','liji_open_home_water=?','liji_open_away_water=?'])
            params.extend([ah['open']['handicap'],ah['open']['home_water'],ah['open']['away_water']])
            ah_ok = True
    
    if ou:
        has_open = 'open' in ou
        if 'close' in ou:
            sets.extend(['liji_ou_line=?','liji_ou_over=?','liji_ou_under=?'])
            params.extend([ou['close']['line'],ou['close']['over'],ou['close']['under']])
        if has_open:
            sets.extend(['liji_ou_open_line=?','liji_ou_open_over=?','liji_ou_open_under=?'])
            params.extend([ou['open']['line'],ou['open']['over'],ou['open']['under']])
            ou_ok = True
    
    if sets:
        params.append(fid)
        c.execute(f'UPDATE poisson_predictions SET {",".join(sets)} WHERE fid_500=?', params)
        conn.commit()
        if c.rowcount > 0:
            ah_str = f"AH初盘{'✓' if ah_ok else ''}" if fid in fids_ah else ""
            ou_str = f"OU初盘{'✓' if ou_ok else ''}" if fid in fids_ou else ""
            print(f"OK [{ah_str} {ou_str}]".strip())
            if ah_ok: updated_ah += 1
            if ou_ok: updated_ou += 1
        else:
            print("no match")
    else:
        print("no data")

conn.close()
print()
print(f"修复完成: AH初盘{updated_ah}场, OU初盘{updated_ou}场")
