#!/usr/bin/env python3
"""
回填 football.db 中的球队排名/积分数据
从 liansai.500.com/zuqiu-XXXX/ 各联赛页获取积分榜
匹配方式：联赛名 → league_id → 站台表 → 按球队名匹配
"""
import urllib.request
import re
import sqlite3
import sys
import os
import time

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36'
            })
            return urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='replace')
        except Exception as e:
            if i < retries - 1:
                time.sleep(2**i)
            else:
                raise

def parse_standings(html):
    """解析联赛积分榜 HTML，返回 [{team, rank, played, wins, draws, losses, points}]"""
    m = re.search(r'<table[^>]*lstable1[^>]*>.*?</table>', html, re.DOTALL)
    if not m:
        return None
    t = m.group()
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.DOTALL)
    standings = []
    for row in rows[1:]:  # skip header
        cells = re.findall(r'<t[dh][^>]*>\s*(.*?)\s*</t[dh]>', row, re.DOTALL)
        if len(cells) >= 7:
            clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells[:7]]
            standings.append({
                'rank': int(clean[0]) if clean[0].isdigit() else 0,
                'team': re.sub(r'\s+', ' ', clean[1]).strip(),
                'played': int(clean[2]) if clean[2].isdigit() else 0,
                'wins': int(clean[3]) if clean[3].isdigit() else 0,
                'draws': int(clean[4]) if clean[4].isdigit() else 0,
                'losses': int(clean[5]) if clean[5].isdigit() else 0,
                'points': int(clean[6]) if clean[6].isdigit() else 0,
            })
    return standings

def get_league_ids(conn):
    """从数据库获取所有不重复联赛的 league_id 映射"""
    cur = conn.execute('''
        SELECT DISTINCT p.league, p.fid_500
        FROM poisson_predictions p
        WHERE p.fid_500 IS NOT NULL AND p.fid_500 > 0 AND p.reference_score != ''
    ''')
    rows = cur.fetchall()
    # 按联赛分组，取第一个fid
    league_fid = {}
    for league, fid in rows:
        if league not in league_fid:
            league_fid[league] = str(int(fid))
    return league_fid

def get_league_id_from_detail(fid):
    """从detail.php提取联赛ID"""
    try:
        html = fetch(f'https://live.500.com/detail.php?fid={fid}')
        m = re.search(r'https?://liansai\.500\.com/zuqiu-(\d+)/', html)
        return m.group(1) if m else None
    except:
        return None

def backfill_standings():
    conn = sqlite3.connect(DB_PATH)
    
    # 获取联赛→fid映射
    league_fid = get_league_ids(conn)
    print(f"共 {len(league_fid)} 个联赛需获取 league_id")
    
    # 逐个联赛获取积分榜
    league_ids = {}      # league → league_id
    league_standings = {}  # league → standings
    
    for idx, (league, fid) in enumerate(sorted(league_fid.items())):
        lid = get_league_id_from_detail(fid)
        if lid:
            league_ids[league] = lid
            try:
                html = fetch(f'https://liansai.500.com/zuqiu-{lid}/')
                standings = parse_standings(html)
                if standings:
                    league_standings[league] = standings
                else:
                    print(f'  [{idx+1}/{len(league_fid)}] {league} (fid={fid}, lid={lid}) — 无法解析积分榜')
            except Exception as e:
                print(f'  [{idx+1}/{len(league_fid)}] {league} (fid={fid}, lid={lid}) — 错误: {e}')
        else:
            print(f'  [{idx+1}/{len(league_fid)}] {league} (fid={fid}) — 无法获取联赛ID')
        
        if (idx + 1) % 20 == 0:
            print(f'  ... {idx+1}/{len(league_fid)} 个联赛已处理')
    
    print(f"\n成功获取 {len(league_standings)} 个联赛的积分榜")
    
    # 更新数据库
    updated = 0
    no_match = 0
    skipped = 0
    
    cur = conn.execute('''
        SELECT id, league, home_team, away_team, home_ranking, away_ranking
        FROM poisson_predictions
        WHERE reference_score != '' AND fid_500 IS NOT NULL AND fid_500 > 0
    ''')
    all_matches = cur.fetchall()
    
    for mid, league, ht, at, hr, ar in all_matches:
        if hr not in (None, 0) and ar not in (None, 0):
            skipped += 1
            continue
        
        standings = league_standings.get(league)
        if not standings:
            no_match += 1
            continue
        
        # 查找主客队
        h_info = a_info = None
        for s in standings:
            if ht and ht == s['team']:
                h_info = s
            if at and at == s['team']:
                a_info = s
        
        if h_info and a_info:
            conn.execute('''
                UPDATE poisson_predictions 
                SET home_ranking=?, away_ranking=?, home_points=?, away_points=?
                WHERE id=?
            ''', (h_info['rank'], a_info['rank'], h_info['points'], a_info['points'], mid))
            updated += 1
        else:
            # 尝试子串匹配（500.com队名可能含空格/缩写）
            if not h_info:
                for s in (standings or []):
                    if ht and ht in s['team']:
                        h_info = s
                        break
            if not a_info:
                for s in (standings or []):
                    if at and at in s['team']:
                        a_info = s
                        break
            if h_info and a_info:
                conn.execute('''
                    UPDATE poisson_predictions 
                    SET home_ranking=?, away_ranking=?, home_points=?, away_points=?
                    WHERE id=?
                ''', (h_info['rank'], a_info['rank'], h_info['points'], a_info['points'], mid))
                updated += 1
            else:
                no_match += 1
        
        if updated % 200 == 0 and updated > 0:
            conn.commit()
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ 更新完成")
    print(f"   已更新排名+积分: {updated} 场")
    print(f"   未找到匹配: {no_match} 场")
    print(f"   已是非零值跳过: {skipped} 场")

if __name__ == '__main__':
    backfill_standings()
