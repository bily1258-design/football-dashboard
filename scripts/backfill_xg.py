#!/usr/bin/env python3
"""批量提取techCountAll历史趋势数据并计算xG"""
import re, sys, os, urllib.request, sqlite3, time, json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
           'Accept-Language': 'zh-CN,zh;q=0.9'}

def safe_fetch(url, delay=0.35, timeout=12):
    time.sleep(delay)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode('utf-8', errors='replace')
    except:
        return None

def extract_techCountAll(html):
    m = re.search(r'<table\s+id="techCountAll"[^>]*>(.*?)</table>', html, re.S)
    if not m:
        return None
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S)
    result = {}
    for tr in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\r','').replace('\n','') for c in cells]
        if len(clean) == 5 and clean[2] in ('进球','失球','被射门','角球','黄牌','犯规','控球率'):
            label = clean[2]
            result[label] = {'h3': clean[0], 'h10': clean[1], 'a3': clean[3], 'a10': clean[4]}
    return result if result else None

def parse_num(v):
    """解析数值，'-'或无则返回None"""
    v = v.strip()
    if not v or v == '-':
        return None
    try:
        return float(v.replace('%',''))
    except:
        return None

def compute_xg(tech):
    hg3 = parse_num(tech.get('进球',{}).get('h3',''))
    hc3 = parse_num(tech.get('失球',{}).get('h3',''))
    ag3 = parse_num(tech.get('进球',{}).get('a3',''))
    ac3 = parse_num(tech.get('失球',{}).get('a3',''))
    hg10 = parse_num(tech.get('进球',{}).get('h10',''))
    hc10 = parse_num(tech.get('失球',{}).get('h10',''))
    ag10 = parse_num(tech.get('进球',{}).get('a10',''))
    ac10 = parse_num(tech.get('失球',{}).get('a10',''))
    
    xg_data = {}
    if hg3 is not None and ac3 is not None:
        xg_data['xg_home_3'] = round((hg3 + ac3) / 2, 3)
    if ag3 is not None and hc3 is not None:
        xg_data['xg_away_3'] = round((ag3 + hc3) / 2, 3)
    if hg10 is not None and ac10 is not None:
        xg_data['xg_home_10'] = round((hg10 + ac10) / 2, 3)
    if ag10 is not None and hc10 is not None:
        xg_data['xg_away_10'] = round((ag10 + hc10) / 2, 3)
    return xg_data if xg_data else None

def main():
    BATCH_SIZE = 200  # 每200条提交一次DB
    start_time = time.time()
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    
    rows = conn.execute("""
        SELECT pp.match_id, pp.date, pp.home_team, pp.away_team
        FROM poisson_predictions pp
        LEFT JOIN xg_features xf ON xf.sid = CAST(pp.match_id AS INTEGER)
        WHERE pp.match_id LIKE '29%' OR pp.match_id LIKE '30%'
            AND CAST(pp.match_id AS INTEGER) > 0
            AND xf.sid IS NULL
        ORDER BY pp.date DESC
    """).fetchall()
    
    total = len(rows)
    if total == 0:
        print("没有需要处理的比赛")
        return
    
    print(f"需要处理 {total} 场比赛 (每请求延迟0.35s, 预计约{total*0.35/60:.0f}分钟)")
    
    success, fail, skip = 0, 0, 0
    batch = []
    
    for i, (mid, date, ht, at) in enumerate(rows):
        sid = int(mid)
        url = f'https://live.titan007.com/detail/{sid}cn.htm'
        html = safe_fetch(url)
        
        if not html:
            fail += 1
        else:
            tech = extract_techCountAll(html)
            if not tech:
                skip += 1
            else:
                xg = compute_xg(tech)
                hg3 = parse_num(tech.get('进球',{}).get('h3',''))
                hc3 = parse_num(tech.get('失球',{}).get('h3',''))
                ag3 = parse_num(tech.get('进球',{}).get('a3',''))
                ac3 = parse_num(tech.get('失球',{}).get('a3',''))
                hg10 = parse_num(tech.get('进球',{}).get('h10',''))
                hc10 = parse_num(tech.get('失球',{}).get('h10',''))
                ag10 = parse_num(tech.get('进球',{}).get('a10',''))
                ac10 = parse_num(tech.get('失球',{}).get('a10',''))
                
                hs3 = parse_num(tech.get('被射门',{}).get('h3',''))
                as3 = parse_num(tech.get('被射门',{}).get('a3',''))
                hs10 = parse_num(tech.get('被射门',{}).get('h10',''))
                as10 = parse_num(tech.get('被射门',{}).get('a10',''))
                hc3_crn = parse_num(tech.get('角球',{}).get('h3',''))
                ac3_crn = parse_num(tech.get('角球',{}).get('a3',''))
                hc10_crn = parse_num(tech.get('角球',{}).get('h10',''))
                ac10_crn = parse_num(tech.get('角球',{}).get('a10',''))
                hp3 = parse_num(tech.get('控球率',{}).get('h3',''))
                ap3 = parse_num(tech.get('控球率',{}).get('a3',''))
                hp10 = parse_num(tech.get('控球率',{}).get('h10',''))
                ap10 = parse_num(tech.get('控球率',{}).get('a10',''))
                
                row = (sid, ht, at, date,
                       hg3, ag3, hc3, ac3, hs3, as3, hc3_crn, ac3_crn, hp3, ap3,
                       hg10, ag10, hc10, ac10, hs10, as10, hc10_crn, ac10_crn, hp10, ap10,
                       xg.get('xg_home_3') if xg else None,
                       xg.get('xg_away_3') if xg else None,
                       xg.get('xg_home_10') if xg else None,
                       xg.get('xg_away_10') if xg else None)
                batch.append(row)
                success += 1
        
        if len(batch) >= BATCH_SIZE:
            conn.executemany("""
                INSERT OR REPLACE INTO xg_features
                    (sid, home_team, away_team, date,
                     home_goals_3, away_goals_3, home_conceded_3, away_conceded_3,
                     home_shots_3, away_shots_3, home_corners_3, away_corners_3,
                     home_possession_3, away_possession_3,
                     home_goals_10, away_goals_10, home_conceded_10, away_conceded_10,
                     home_shots_10, away_shots_10, home_corners_10, away_corners_10,
                     home_possession_10, away_possession_10,
                     xg_home_3, xg_away_3, xg_home_10, xg_away_10, updated_at)
                VALUES (?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,datetime('now'))
            """, batch)
            conn.commit()
            batch = []
        
        elapsed = time.time() - start_time
        rate = (i+1) / elapsed if elapsed > 0 else 0
        eta = (total - i - 1) / rate if rate > 0 else 999
        if (i+1) % 200 == 0 or i == total-1:
            print(f"[{i+1}/{total}] ✅{success} ❌{fail} ⏭{skip}  {rate:.1f}场/秒 ETA:{eta:.0f}s")
    
    if batch:
        conn.executemany("""
            INSERT OR REPLACE INTO xg_features
                (sid, home_team, away_team, date,
                 home_goals_3, away_goals_3, home_conceded_3, away_conceded_3,
                 home_shots_3, away_shots_3, home_corners_3, away_corners_3,
                 home_possession_3, away_possession_3,
                 home_goals_10, away_goals_10, home_conceded_10, away_conceded_10,
                 home_shots_10, away_shots_10, home_corners_10, away_corners_10,
                 home_possession_10, away_possession_10,
                 xg_home_3, xg_away_3, xg_home_10, xg_away_10, updated_at)
            VALUES (?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,datetime('now'))
        """, batch)
        conn.commit()
    
    elapsed = time.time() - start_time
    print(f"\n完成! 总计{elapsed:.0f}s 成功{success} 失败{fail} 跳过{skip}")
    conn.close()

if __name__ == '__main__':
    main()
