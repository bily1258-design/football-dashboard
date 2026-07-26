#!/usr/bin/env python3
"""从 live.titan007.com 提取历史趋势表(近3/10场均数)并计算xG"""
import re, sys, os, urllib.request, sqlite3, time, json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
           'Accept-Language': 'zh-CN,zh;q=0.9'}

def safe_fetch(url, delay=0.3, timeout=15):
    time.sleep(delay)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None

def extract_techCountAll(html):
    """提取techCountAll表数据"""
    m = re.search(r'<table\s+id="techCountAll"[^>]*>(.*?)</table>', html, re.S)
    if not m:
        return None
    table = m.group(1)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)
    result = {}
    for tr in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\r','').replace('\n','') for c in cells]
        if len(clean) == 5 and clean[2] in ['进球','失球','被射门','角球','黄牌','犯规','控球率','射门','射正']:
            label = clean[2]
            result[label] = {
                'h3': clean[0], 'h10': clean[1],
                'a3': clean[3], 'a10': clean[4]
            }
    return result if result else None

def extract_goal_timing(html):
    """提取进失球概率/进球时间段表"""
    # 定位"进失球概率"后的table
    m = re.search(r'进失球概率.{0,50}?<table[^>]*>', html, re.S)
    if not m:
        return None
    start = m.end()
    depth, pos = 1, start
    while depth > 0 and pos < len(html):
        if html[pos:pos+6] == '<table':
            depth += 1
            pos += 6
        elif html[pos:pos+7] == '</table':
            depth -= 1
            if depth == 0:
                end = html.index('>', pos) + 1
                break
            pos += 7
        else:
            pos += 1
    else:
        return None
    
    table2 = html[start:end]
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table2, re.S)
    result = {'periods': [], 'home_goals_score': [], 'home_conceded_score': [], 
              'away_goals_score': [], 'away_conceded_score': []}
    for tr in rows:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        clean = [re.sub(r'<[^>]+>', '', c).strip().replace('\r','').replace('\n','') for c in cells]
        # row可能包含时间段百分比
        # 格式: [主队进球%, 主队失球%, 时间段, 客队进球%, 客队失球%]
        if len(clean) >= 3:
            if any(c.replace('%','').replace('-','').isdigit() for c in clean[:2]):
                pass  # 暂时跳过
    return result  # TODO: improve extraction

def compute_xg(tech_data):
    """从历史趋势表计算简单xG"""
    if not tech_data:
        return None
    try:
        # 进球均数和失球均数
        hg_3 = float(tech_data.get('进球', {}).get('h3', 0).replace('%',''))
        hg_10 = float(tech_data.get('进球', {}).get('h10', 0).replace('%',''))
        ag_3 = float(tech_data.get('进球', {}).get('a3', 0).replace('%',''))
        ag_10 = float(tech_data.get('进球', {}).get('a10', 0).replace('%',''))
        
        hc_3 = float(tech_data.get('失球', {}).get('h3', 0).replace('%',''))
        hc_10 = float(tech_data.get('失球', {}).get('h10', 0).replace('%',''))
        ac_3 = float(tech_data.get('失球', {}).get('a3', 0).replace('%',''))
        ac_10 = float(tech_data.get('失球', {}).get('a10', 0).replace('%',''))
        
        # xG模型：用近3场均数（更反映近期状态）
        # 主队xG = (主队进球均数 + 客队失球均数) / 2
        xg_home_3 = (hg_3 + ac_3) / 2
        xg_home_10 = (hg_10 + ac_10) / 2
        xg_away_3 = (ag_3 + hc_3) / 2
        xg_away_10 = (ag_10 + hc_10) / 2
        
        # 控球率修正
        poss_h = float(tech_data.get('控球率', {}).get('h3', '50%').replace('%',''))
        poss_a = float(tech_data.get('控球率', {}).get('a3', '50%').replace('%',''))
        poss_factor_h = poss_h / 50.0  # >1 if home dominates possession
        poss_factor_a = poss_a / 50.0
        
        xg_home_adj = xg_home_3 * poss_factor_h
        xg_away_adj = xg_away_3 * poss_factor_a
        
        return {
            'xg_home_3': round(xg_home_3, 3),
            'xg_away_3': round(xg_away_3, 3),
            'xg_home_10': round(xg_home_10, 3),
            'xg_away_10': round(xg_away_10, 3),
            'xg_home_adj': round(xg_home_adj, 3),
            'xg_away_adj': round(xg_away_adj, 3),
        }
    except (ValueError, TypeError):
        return None

def main():
    conn = sqlite3.connect(DB_PATH)
    
    # 获取 poisson_predictions 中所有有有效SID的比赛
    rows = conn.execute("""
        SELECT pp.match_id, pp.date, pp.home_team, pp.away_team
        FROM poisson_predictions pp
        WHERE pp.match_id IS NOT NULL AND pp.match_id != ''
            AND (pp.match_id LIKE '29%' OR pp.match_id LIKE '30%')
            AND CAST(pp.match_id AS INTEGER) > 0
            AND CAST(pp.match_id AS INTEGER) < 9999999
    """).fetchall()
    
    print(f'共 {len(rows)} 场比赛有有效SID')
    
    success = 0
    for i, (sid, date, ht, at) in enumerate(rows):
        sid_int = int(sid)
        
        # 先从match_tech_stats查是否已有技统（有的话不需要再算xG）
        has_tech = conn.execute(
            "SELECT home_shots FROM match_tech_stats WHERE sid=? AND home_shots IS NOT NULL",
            (sid_int,)).fetchone()
        
        if i % 100 == 0:
            print(f'[{i}/{len(rows)}] 已成功 {success} 场...')
        
        # 获取历史趋势数据
        url = f'https://live.titan007.com/detail/{sid_int}cn.htm'
        html = safe_fetch(url)
        if not html:
            continue
        
        tech = extract_techCountAll(html)
        if not tech:
            continue
        
        xg_data = compute_xg(tech)
        if xg_data:
            # 存入 match_tech_stats 或新建 xg_features 表
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO match_tech_stats
                        (home_team, away_team, date, sid, updated_at,
                         home_shots_3, away_shots_3, home_shots_10, away_shots_10,
                         home_possession_3, away_possession_3,
                         home_possession_10, away_possession_10,
                         home_corners_3, away_corners_3,
                         home_corners_10, away_corners_10,
                         home_shots_on_target_3, away_shots_on_target_3,
                         home_shots_on_target_10, away_shots_on_target_10,
                         xg_home_3, xg_away_3, xg_home_10, xg_away_10)
                    VALUES (?,?,?,?,datetime('now'),
                            ?,?,?,?,
                            ?,?,?,?,
                            ?,?,?,?,
                            ?,?,?,?,
                            ?,?,?,?)
                """, (ht, at, date, sid_int,
                      # 射门
                      float(tech.get('被射门',{}).get('h3','0') or 0),
                      float(tech.get('被射门',{}).get('a3','0') or 0),
                      float(tech.get('被射门',{}).get('h10','0') or 0),
                      float(tech.get('被射门',{}).get('a10','0') or 0),
                      # 控球
                      float(tech.get('控球率',{}).get('h3','50%').replace('%','')),
                      float(tech.get('控球率',{}).get('a3','50%').replace('%','')),
                      float(tech.get('控球率',{}).get('h10','50%').replace('%','')),
                      float(tech.get('控球率',{}).get('a10','50%').replace('%','')),
                      # 角球
                      float(tech.get('角球',{}).get('h3','0') or 0),
                      float(tech.get('角球',{}).get('a3','0') or 0),
                      float(tech.get('角球',{}).get('h10','0') or 0),
                      float(tech.get('角球',{}).get('a10','0') or 0),
                      # 射正 (有些页面有，有些没有)
                      float(tech.get('射门',{}).get('h3','0') or 0),
                      float(tech.get('射门',{}).get('a3','0') or 0),
                      float(tech.get('射门',{}).get('h10','0') or 0),
                      float(tech.get('射门',{}).get('a10','0') or 0),
                      # xG
                      xg_data['xg_home_3'], xg_data['xg_away_3'],
                      xg_data['xg_home_10'], xg_data['xg_away_10']))
                conn.commit()
                success += 1
            except sqlite3.Error as e:
                print(f'  DB error for {sid}: {e}')
    
    conn.close()
    print(f'\n完成: {success}/{len(rows)} 场成功写入')

if __name__ == '__main__':
    main()
