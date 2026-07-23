#!/usr/bin/env python3
"""update_rankings.py — 从 Over 页面重新提取排名数据，更新已有比赛

对于 football.db 中 home_ranking/away_ranking 为 NULL 的比赛，
重新抓对应的 Over 页面，提取 [联赛名排名] 数据并更新数据库。

用法:
  python3 scripts/update_rankings.py                     # 全量补回
  python3 scripts/update_rankings.py --start 2026-01-01 --end 2026-01-31  # 指定范围
  python3 scripts/update_rankings.py --dry-run           # 只扫描不写入
"""
import re, json, sqlite3, sys, os, time, urllib.request
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, 'scripts'))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36',
    'Referer': 'https://live.titan007.com/',
}


def fetch_over_page(date_str):
    """获取单日 Over 页面，返回 {team_name: ranking} 的映射"""
    date_compact = date_str.replace('-', '')
    url = f'https://bf.titan007.com/football/Over_{date_compact}.htm'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
    except Exception:
        return None
    
    html = raw.decode('gb2312', errors='replace')
    
    # 提取表格
    table_match = re.search(r'<table[^>]*id=.table_live[^>]*>(.*?)</table>', html, re.DOTALL)
    if not table_match:
        tables = re.findall(r'<table[^>]*cellSpacing=1[^>]*>.*?</table>', html, re.DOTALL)
        if not tables:
            return None
        tbl = tables[0]
    else:
        tbl = table_match.group(1)
    
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.DOTALL)
    
    # 返回 (team_clean_name, ranking) 映射
    team_rankings = {}
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(tds) < 7:
            continue
        
        home_raw = re.sub(r'<[^>]+>', '', tds[3]).strip()
        away_raw = re.sub(r'<[^>]+>', '', tds[5]).strip()
        
        # 提取排名
        h_rank = None
        a_rank = None
        h_m = re.search(r'\[[^\]]*?(\d+)\]', home_raw)
        a_m = re.search(r'\[[^\]]*?(\d+)\]', away_raw)
        if h_m:
            h_rank = int(h_m.group(1))
        if a_m:
            a_rank = int(a_m.group(1))
        
        if h_rank is None and a_rank is None:
            continue
        
        # 清理队名（与 backfill 一致）
        home_clean = re.sub(r'\s*\[[^\]]*\]', '', home_raw).strip()
        away_clean = re.sub(r'\s*\[[^\]]*\]', '', away_raw).strip()
        home_clean = re.sub(r'\([^)]*\)', '', home_clean).strip()
        away_clean = re.sub(r'\([^)]*\)', '', away_clean).strip()
        
        if h_rank:
            team_rankings[(date_str, home_clean)] = h_rank
        if a_rank:
            team_rankings[(date_str, away_clean)] = a_rank
    
    return team_rankings


def update_date_range(start_date, end_date, dry_run=False):
    """更新指定日期范围内所有比赛的排名"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA busy_timeout=15000')
    
    current = start_date
    total_updated = 0
    total_dates = (end_date - start_date).days + 1
    date_num = 0
    
    while current <= end_date:
        date_str = current.isoformat()
        date_num += 1
        
        # 检查这天是否有需要补排名的比赛
        cursor = conn.execute(
            "SELECT COUNT(*) FROM poisson_predictions WHERE date=? AND (home_ranking IS NULL OR away_ranking IS NULL)",
            (date_str,)
        )
        need_count = cursor.fetchone()[0]
        if need_count == 0:
            current += timedelta(days=1)
            continue
        
        print(f'[{date_num}/{total_dates}] {date_str}: {need_count}场需补排名...', end=' ', flush=True)
        
        team_rankings = fetch_over_page(date_str)
        if team_rankings is None:
            print(f'❌ 页面抓取失败')
            current += timedelta(days=1)
            continue
        
        # 查询该天的所有比赛
        cursor = conn.execute(
            "SELECT rowid, home_team, away_team, home_ranking, away_ranking FROM poisson_predictions WHERE date=?",
            (date_str,)
        )
        matches = cursor.fetchall()
        
        updated = 0
        for rowid, home_team, away_team, h_rank, a_rank in matches:
            new_h = h_rank
            new_a = a_rank
            
            if h_rank is None:
                key = (date_str, home_team)
                if key in team_rankings:
                    new_h = team_rankings[key]
            
            if a_rank is None:
                key = (date_str, away_team)
                if key in team_rankings:
                    new_a = team_rankings[key]
            
            if new_h != h_rank or new_a != a_rank:
                if not dry_run:
                    conn.execute(
                        "UPDATE poisson_predictions SET home_ranking=?, away_ranking=? WHERE rowid=?",
                        (new_h, new_a, rowid)
                    )
                updated += 1
        
        if not dry_run:
            conn.commit()
        
        print(f'✅ 更新{updated}/{need_count}场 (共{len(team_rankings)}个排名)')
        total_updated += updated
        
        time.sleep(0.3)  # 礼貌间隔
        current += timedelta(days=1)
    
    conn.close()
    print(f'\n🎯 总计: 更新 {total_updated} 场比赛的排名 ({total_dates}天)')
    return total_updated


def main():
    import argparse
    parser = argparse.ArgumentParser(description='从Over页面补回排名数据')
    parser.add_argument('--start', default='2026-01-01')
    parser.add_argument('--end', default='2026-07-15')
    parser.add_argument('--dry-run', action='store_true', help='仅扫描不写入')
    parser.add_argument('--workers', type=int, default=3, help='并行抓取线程数')
    args = parser.parse_args()
    
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    
    print(f'📊 排名回填: {args.start} ~ {args.end}')
    print(f'   {"🟡 干跑模式" if args.dry_run else "🟢 写入模式"}')
    
    update_date_range(start_date, end_date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
