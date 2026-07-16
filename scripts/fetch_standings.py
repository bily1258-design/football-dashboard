#!/usr/bin/env python3
"""
联赛积分榜抓取器
从 500.com 各联赛页面获取完整排名+积分数据
支持两种模式:
  1. 单联赛: fetch_standings.py --league 英超
  2. 全部联赛: fetch_standings.py --all
  3. 按比赛列表批量获取排名: fetch_standings.py --matches 带联赛、主客队字段的JSON
"""
import urllib.request, urllib.parse, re, json, sys, os, time, sqlite3
from datetime import datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'standings_cache')
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_url(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36'
            })
            return urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='replace')
        except Exception as e:
            if i < retries - 1:
                time.sleep(2 ** i)
            else:
                raise

def parse_standings_from_league_page(html):
    """从联赛主页(lianai.500.com/zuqiu-XXX/)提取积分榜"""
    # 找 lstable1 的 table
    m = re.search(r'<table[^>]*class="[^"]*lstable1[^"]*"[^>]*>.*?</table>', html, re.DOTALL)
    if not m:
        return None
    t = m.group()
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.DOTALL)
    standings = []
    for row in rows[1:]:  # skip header
        cells = re.findall(r'<t[dh][^>]*>\s*(.*?)\s*</t[dh]>', row, re.DOTALL)
        if len(cells) >= 7:
            rank = re.sub(r'<[^>]+>', '', cells[0]).strip()
            team = re.sub(r'<[^>]+>', '', cells[1]).strip()
            played = re.sub(r'<[^>]+>', '', cells[2]).strip()
            wins = re.sub(r'<[^>]+>', '', cells[3]).strip()
            draws = re.sub(r'<[^>]+>', '', cells[4]).strip()
            losses = re.sub(r'<[^>]+>', '', cells[5]).strip()
            points = re.sub(r'<[^>]+>', '', cells[6]).strip()
            # 清理多余空格
            team = re.sub(r'\s+', ' ', team).strip()
            standings.append({
                'rank': int(rank) if rank.isdigit() else 0,
                'team': team,
                'played': int(played) if played.isdigit() else 0,
                'wins': int(wins) if wins.isdigit() else 0,
                'draws': int(draws) if draws.isdigit() else 0,
                'losses': int(losses) if losses.isdigit() else 0,
                'points': int(points) if points.isdigit() else 0,
            })
    return standings

def extract_league_id_from_detail(html):
    """从detail.php页面提取联赛ID(lianai链接)"""
    m = re.search(r'https?://liansai\.500\.com/zuqiu-(\d+)/', html)
    return m.group(1) if m else None

def get_standings_by_league(league_cn_name):
    """
    按中文联赛名获取积分榜
    通过联赛搜索页面找到联赛ID
    """
    # 先尝试搜索联赛
    enc_name = urllib.parse.quote(league_cn_name.encode('gbk'))
    search_url = f'https://liansai.500.com/search.php?q={enc_name}'
    try:
        html = fetch_url(search_url)
        # 找联赛链接
        m = re.search(r'zuqiu-(\d+)/', html)
        if m:
            league_id = m.group(1)
            league_url = f'https://liansai.500.com/zuqiu-{league_id}/'
            html = fetch_url(league_url)
            standings = parse_standings_from_league_page(html)
            if standings:
                return standings
    except Exception:
        pass
    return None

def get_standings_for_matches(matches_list):
    """
    批量获取比赛对应的排名
    matches_list: [{'home_team':..., 'away_team':..., 'league':..., 'fid':...}, ...]
    返回: {match_index: {'home_rank':N, 'away_rank':N, 'home_points':N, 'away_points':N}}
    """
    # 按联赛分组
    by_league = {}
    for i, m in enumerate(matches_list):
        league = m.get('league', '')
        by_league.setdefault(league, []).append((i, m))
    
    results = {}
    for league, items in by_league.items():
        # 先试detail.php (按fid)
        for idx, match in items:
            fid = match.get('fid', match.get('fid_500', ''))
            if fid:
                try:
                    html = fetch_url(f'https://live.500.com/detail.php?fid={fid}')
                    ranks = re.findall(r'当前排名:(\d+)', html)
                    if len(ranks) >= 2:
                        results[idx] = {
                            'home_rank': int(ranks[0]),
                            'away_rank': int(ranks[1]),
                        }
                        continue
                except Exception:
                    pass
        
        # 如果detail.php没找到，尝试联赛页面
        remaining = [(idx, m) for idx, m in items if idx not in results]
        if remaining:
            try:
                standings = get_standings_by_league(league)
                if standings:
                    team_map = {s['team']: s for s in standings}
                    for idx, match in remaining:
                        ht = match.get('home_team', '')
                        at = match.get('away_team', '')
                        # 模糊匹配：尝试全名匹配
                        home_s = team_map.get(ht)
                        away_s = team_map.get(at)
                        if home_s and away_s:
                            results[idx] = {
                                'home_rank': home_s['rank'],
                                'away_rank': away_s['rank'],
                                'home_points': home_s['points'],
                                'away_points': away_s['points'],
                            }
                        else:
                            # 尝试子串匹配
                            for tn, s in team_map.items():
                                if ht and ht in tn:
                                    if idx not in results:
                                        results[idx] = {}
                                    results[idx]['home_rank'] = s['rank']
                                    results[idx]['home_points'] = s['points']
                                if at and at in tn:
                                    if idx not in results:
                                        results[idx] = {}
                                    results[idx]['away_rank'] = s['rank']
                                    results[idx]['away_points'] = s['points']
            except Exception:
                pass
    
    return results

def backfill_training_db(db_path=None):
    """
    从football.db读取训练数据，提取排名特征并更新
    """
    if not db_path:
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
    
    conn = sqlite3.connect(db_path)
    cur = conn.execute('''
        SELECT id, league, home_team, away_team, fid_500, home_ranking, away_ranking
        FROM poisson_predictions
        WHERE reference_score != '' AND fid_500 IS NOT NULL AND fid_500 > 0
        AND (home_ranking IS NULL OR home_ranking = 0)
        ORDER BY date DESC
    ''')
    samples = cur.fetchall()
    print(f'待更新排名: {len(samples)} 场')
    
    updated = 0
    for i, (sid, league, ht, at, fid, hr, ar) in enumerate(samples):
        try:
            html = fetch_url(f'https://live.500.com/detail.php?fid={fid}')
            ranks = re.findall(r'当前排名:(\d+)', html)
            if len(ranks) >= 2:
                conn.execute('UPDATE poisson_predictions SET home_ranking=?, away_ranking=? WHERE id=?',
                           (int(ranks[0]), int(ranks[1]), sid))
                updated += 1
            if (i+1) % 50 == 0:
                conn.commit()
                print(f'  ... {i+1}/{len(samples)} done, {updated} updated')
            time.sleep(0.3)  # rate limit
        except Exception as e:
            print(f'  失败 fid={fid}: {e}')
    
    conn.commit()
    conn.close()
    print(f'完成: {updated}/{len(samples)} 已更新排名')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='联赛积分榜抓取器')
    parser.add_argument('--league', help='中文联赛名')
    parser.add_argument('--all', action='store_true', help='显示所有联赛')
    parser.add_argument('--backfill-db', action='store_true', help='回填football.db的积分数据')
    parser.add_argument('--test', help='测试fid')
    args = parser.parse_args()
    
    if args.test:
        fid = args.test
        html = fetch_url(f'https://live.500.com/detail.php?fid={fid}')
        ranks = re.findall(r'当前排名:(\d+)', html)
        print(f'fid={fid}: 排名={ranks}')
        
        # 也查一下联赛名
        league_url = extract_league_id_from_detail(html)
        if league_url:
            print(f'联赛链接: zuqiu-{league_url}/')
    
    elif args.league:
        standings = get_standings_by_league(args.league)
        if standings:
            print(f'{args.league} 积分榜:')
            print('{:>3s} {:20s} {:>3s} {:>3s} {:>3s} {:>3s} {:>3s}'.format('排名', '球队', '赛', '胜', '平', '负', '积分'))
            for s in standings:
                print('{:3d} {:20s} {:3d} {:3d} {:3d} {:3d} {:3d}'.format(s['rank'], s['team'], s['played'], s['wins'], s['draws'], s['losses'], s['points']))
        else:
            print(f'未找到联赛: {args.league}')
    
    elif args.backfill_db:
        backfill_training_db()
    
    elif args.all:
        # 显示已知联赛
        print("已知联赛列表（需要在代码中维护league_id映射）")
