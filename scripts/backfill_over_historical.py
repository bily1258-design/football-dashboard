#!/usr/bin/env python3
"""backfill_over_historical.py — 从 titan007 Over页面 回填上半年历史比赛

爬取 2026-01-01 ~ 2026-06-30 的完场比赛:
1. Over_YYYYMMDD.htm → 比赛列表 (sid, 联赛, 主队, 客队, 比分, 时间)
2. 1x2d.titan007.com/{sid}.js → 赔率数据
3. 写入 football.db

用法:
  python3 scripts/backfill_over_historical.py                     # 默认 2026-01-01 ~ 2026-06-30
  python3 scripts/backfill_over_historical.py --start 2026-01-01 --end 2026-01-31  # 指定范围
  python3 scripts/backfill_over_historical.py --dry-run           # 只扫描，不写入
  python3 scripts/backfill_over_historical.py --skip-odds        # 不抓赔率(仅比赛信息)
"""

import re, json, os, sqlite3, sys, time, urllib.request, argparse
from datetime import datetime, date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')
DATA_DIR = os.path.join(PROJECT_DIR, 'data')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36',
    'Referer': 'https://live.titan007.com/',
}


def fetch_over_page(date_str):
    """获取单日 Over 页面的比赛列表
    
    返回: [{sid, league, home_team, away_team, score, kickoff_time, date}, ...]
    """
    date_compact = date_str.replace('-', '')
    url = f'https://bf.titan007.com/football/Over_{date_compact}.htm'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
    except Exception as e:
        print(f'  [WARN] Over_{date_compact}.htm 抓取失败: {e}')
        return []
    
    html = raw.decode('gb2312', errors='replace')
    
    # 提取表格
    table_match = re.search(r'<table[^>]*id=.table_live[^>]*>(.*?)</table>', html, re.DOTALL)
    if not table_match:
        # 尝试无id的表格
        tables = re.findall(r'<table[^>]*cellSpacing=1[^>]*>.*?</table>', html, re.DOTALL)
        if not tables:
            return []
        tbl = tables[0]
    else:
        tbl = table_match.group(1)
    
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.DOTALL)
    
    matches = []
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(tds) < 9:
            continue
        
        # 提取 sid
        sid_matches = re.findall(r'EuropeOdds\((\d+)\)', tds[9] if len(tds) > 9 else '')
        if not sid_matches:
            continue
        sid = sid_matches[0]
        
        league = re.sub(r'<[^>]+>', '', tds[0]).strip()
        time_str = re.sub(r'<[^>]+>', '', tds[1]).strip()
        home = re.sub(r'<[^>]+>', '', tds[3]).strip()
        score_raw = re.sub(r'<[^>]+>', '', tds[4]).strip()
        away = re.sub(r'<[^>]+>', '', tds[5]).strip()
        
        # 提取排名数据 [联赛名排名] 或 [排名] 格式
        home_ranking = None
        away_ranking = None
        h_rank_m = re.search(r'\[[^\]]*?(\d+)\]', home)
        a_rank_m = re.search(r'\[[^\]]*?(\d+)\]', away)
        if h_rank_m:
            home_ranking = int(h_rank_m.group(1))
        if a_rank_m:
            away_ranking = int(a_rank_m.group(1))
        
        # 清理排名标记、中立场地标记
        home_clean = re.sub(r'\s*\[[^\]]*\]', '', home).strip()
        away_clean = re.sub(r'\s*\[[^\]]*\]', '', away).strip()
        home_clean = re.sub(r'\([^)]*\)', '', home_clean).strip()
        away_clean = re.sub(r'\([^)]*\)', '', away_clean).strip()
        
        # 解析比分
        score = ''
        if re.match(r'^\d+\s*-\s*\d+$', score_raw):
            score = score_raw.replace(' ', '')
        
        # 解析时间: "15日13:15" → ISO
        try:
            t_match = re.match(r'(\d+)日(\d+):(\d+)', time_str)
            if t_match:
                day = int(t_match.group(1))
                hour = int(t_match.group(2))
                minute = int(t_match.group(3))
                m = int(date_compact[4:6])
                # 处理跨月：如果 day < 当前日期 day，说明是下个月
                # 用 date_compact 的年月
                kickoff = f'{date_compact[:4]}-{date_compact[4:6]}-{day:02d}T{hour:02d}:{minute:02d}:00'
            else:
                kickoff = f'{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}T00:00:00'
        except:
            kickoff = f'{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}T00:00:00'
        
        matches.append({
            'sid': sid,
            'league': league,
            'home_team': home_clean,
            'away_team': away_clean,
            'score': score,
            'kickoff_time': kickoff,
            'date': date_str,
            'home_ranking': home_ranking,
            'away_ranking': away_ranking,
        })
    
    return matches


def fetch_odds(sid):
    """从 1x2d API 获取赔率数据
    
    返回: {odds_win, odds_draw, odds_loss, ...} 或 None
    """
    url = f'https://1x2d.titan007.com/{sid}.js'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        raw = resp.read()
    except Exception as e:
        return None
    
    try:
        # 注意: 返回的是 UTF-8 with BOM, 不是 gb2312
        text = raw.decode('utf-8-sig')
    except:
        try:
            text = raw.decode('gb2312', errors='replace')
        except:
            return None
    
    result = {}
    
    # 关键公司ID映射: CID -> (prefix, column_suffix)
    # CID=432: HK Jockey Club, CID=177: Pinnacle, CID=281: Bet365
    # CID=115: William Hill, CID=80: Macauslot, CID=82: Ladbrokes
    company_map = {
        432: 'hkjc',
        177: 'pinnacle',
        281: 'bet365',
        115: 'william',
        80: 'ms',        # Macauslot
        82: 'liji',
    }
    
    # 解析 var game=Array("entry1","entry2",...) 格式
    game_match = re.search(r'var game=Array\(([\s\S]*?)\);', text)
    if not game_match:
        return result
    
    content = game_match.group(1)
    
    # 解析带引号的数组元素
    entries = re.findall(r'"([^"]*?)"', content)
    
    for entry in entries:
        parts = entry.split('|')
        if len(parts) < 6:
            continue
        try:
            cid = int(parts[0])
        except ValueError:
            continue
        odds_w = float(parts[3]) if parts[3] else 0
        odds_d = float(parts[4]) if parts[4] else 0
        odds_l = float(parts[5]) if parts[5] else 0
        
        if cid in company_map:
            prefix = company_map[cid]
            result[f'odds_{prefix}_win'] = odds_w
            result[f'odds_{prefix}_draw'] = odds_d
            result[f'odds_{prefix}_loss'] = odds_l
        
        if cid == 432:  # HKJC
            result['odds_hkjc_win'] = odds_w
            result['odds_hkjc_draw'] = odds_d
            result['odds_hkjc_loss'] = odds_l
        elif cid == 177:  # Pinnacle
            result['odds_win'] = odds_w
            result['odds_draw'] = odds_d
            result['odds_loss'] = odds_l
    
    return result if result else None


def match_exists(cursor, sid, date_str):
    """检查比赛是否已在 DB 中"""
    cursor.execute(
        "SELECT id FROM poisson_predictions WHERE match_id=? AND date=?",
        (str(sid), date_str)
    )
    return cursor.fetchone() is not None


def insert_match(conn, match, odds_info, skip_odds=False):
    """插入一场比赛到 football.db"""
    
    # 解析比分
    home_score = 0
    away_score = 0
    actual_outcome = ''
    if match['score']:
        parts = match['score'].split('-')
        home_score = int(parts[0].strip())
        away_score = int(parts[1].strip())
        if home_score > away_score:
            actual_outcome = 'home'
        elif home_score < away_score:
            actual_outcome = 'away'
        else:
            actual_outcome = 'draw'
        actual_outcome += f' ({match["score"]})'
    
    # 赔率值
    odds_win = odds_info.get('odds_win', 0) if odds_info else 0
    odds_draw = odds_info.get('odds_draw', 0) if odds_info else 0
    odds_loss = odds_info.get('odds_loss', 0) if odds_info else 0
    
    # HKJC
    hkjc_w = odds_info.get('odds_hkjc_win', 0) if odds_info else 0
    hkjc_d = odds_info.get('odds_hkjc_draw', 0) if odds_info else 0
    hkjc_l = odds_info.get('odds_hkjc_loss', 0) if odds_info else 0
    
    # Pinnacle
    pin_w = odds_info.get('odds_pinnacle_win', 0) if odds_info else 0
    pin_d = odds_info.get('odds_pinnacle_draw', 0) if odds_info else 0
    pin_l = odds_info.get('odds_pinnacle_loss', 0) if odds_info else 0
    
    # bet365
    bet_w = odds_info.get('odds_bet365_win', 0) if odds_info else 0
    bet_d = odds_info.get('odds_bet365_draw', 0) if odds_info else 0
    bet_l = odds_info.get('odds_bet365_loss', 0) if odds_info else 0
    
    # 威廉
    wil_w = odds_info.get('odds_william_win', 0) if odds_info else 0
    wil_d = odds_info.get('odds_william_draw', 0) if odds_info else 0
    wil_l = odds_info.get('odds_william_loss', 0) if odds_info else 0
    
    # 澳门
    ms_w = odds_info.get('odds_ms_win', 0) if odds_info else 0
    ms_d = odds_info.get('odds_ms_draw', 0) if odds_info else 0
    ms_l = odds_info.get('odds_ms_loss', 0) if odds_info else 0
    
    # 立博
    lj_w = odds_info.get('odds_liji_win', 0) if odds_info else 0
    lj_d = odds_info.get('odds_liji_draw', 0) if odds_info else 0
    lj_l = odds_info.get('odds_liji_loss', 0) if odds_info else 0
    
    now = datetime.now().isoformat()
    
    sql = """INSERT OR IGNORE INTO poisson_predictions (
        match_id, date, league, home_team, away_team, kickoff_time,
        source, actual_outcome, created_at,
        odds_win, odds_draw, odds_loss,
        hkjc_close_w, hkjc_close_d, hkjc_close_l,
        pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
        bet365_close_w, bet365_close_d, bet365_close_l,
        william_close_w, william_close_d, william_close_l,
        ms_close_w, ms_close_d, ms_close_l,
        liji_close_w, liji_close_d, liji_close_l,
        home_ranking, away_ranking
    ) VALUES (?, ?, ?, ?, ?, ?, 'over_backfill', ?, ?, 
        ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?,
        ?, ?)"""
    
    conn.execute(sql, (
        match['sid'], match['date'], match['league'],
        match['home_team'], match['away_team'], match['kickoff_time'],
        actual_outcome, now,
        odds_win, odds_draw, odds_loss,
        hkjc_w, hkjc_d, hkjc_l,
        pin_w, pin_d, pin_l,
        bet_w, bet_d, bet_l,
        wil_w, wil_d, wil_l,
        ms_w, ms_d, ms_l,
        lj_w, lj_d, lj_l,
        match.get('home_ranking'), match.get('away_ranking'),
    ))
    return conn.total_changes > 0


def main():
    parser = argparse.ArgumentParser(description='回填上半年历史比赛数据')
    parser.add_argument('--start', default='2026-01-01')
    parser.add_argument('--end', default='2026-06-30')
    parser.add_argument('--dry-run', action='store_true', help='仅扫描不写入')
    parser.add_argument('--skip-odds', action='store_true', help='不抓赔率')
    parser.add_argument('--delay', type=float, default=0.5, help='请求间隔(秒)')
    parser.add_argument('--match-delay', type=float, default=0.15, help='赔率请求间隔(秒)')
    parser.add_argument('--workers', type=int, default=10, help='并行抓赔率线程数(默认10)')
    args = parser.parse_args()
    
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    
    print(f'📅 回填范围: {args.start} ~ {args.end}')
    print(f'   🔗 Over页面: bf.titan007.com/football/Over_YYYYMMDD.htm')
    if args.dry_run:
        print('   🏃 模式: 干跑(不写入)')
    if args.skip_odds:
        print('   🎲 赔率: 跳过')
    
    if not args.dry_run:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=15000')
        cursor = conn.cursor()
    
    total_days = (end_date - start_date).days + 1
    total_matches = 0
    total_inserted = 0
    total_odds_ok = 0
    
    cur_date = start_date
    day_count = 0
    
    while cur_date <= end_date:
        date_str = cur_date.isoformat()
        day_count += 1
        
        print(f'\n[{day_count}/{total_days}] 📆 {date_str}', end='')
        
        # Step 1: 爬 Over 页面
        matches = fetch_over_page(date_str)
        if not matches:
            print(f' → 0 场比赛')
            cur_date += timedelta(days=1)
            continue
        
        print(f' → {len(matches)} 场', end='')
        total_matches += len(matches)
        
        # Step 2: 检查已存在
        need_odds = []
        skipped = 0
        for m in matches:
            if not args.dry_run:
                if match_exists(cursor, m['sid'], date_str):
                    skipped += 1
                    continue
            need_odds.append(m)
        
        if skipped:
            print(f' (已存{skipped})', end='')
        
        # Step 3: 并行抓赔率
        day_new = 0
        day_odds = 0
        odds_map = {}  # sid -> odds_info
        
        if need_odds and not args.skip_odds:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def fetch_with_sid(m):
                """单条赔率抓取包装"""
                info = fetch_odds(m['sid'])
                return m['sid'], info
            
            workers = args.workers
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(fetch_with_sid, m): m for m in need_odds}
                done_count = 0
                for f in as_completed(futures):
                    sid, info = f.result()
                    if info:
                        day_odds += 1
                        odds_map[sid] = info
                    done_count += 1
                    if done_count % 50 == 0 or done_count == len(need_odds):
                        print(f' [{done_count}/{len(need_odds)} odds]', end='')
                    time.sleep(args.match_delay)  # 如果有设置
        
        # Step 4: 入库
        for i, m in enumerate(need_odds):
            if not args.dry_run:
                inserted = insert_match(conn, m, odds_map.get(m['sid'], {}))
                if inserted:
                    day_new += 1
        
        if need_odds and not args.skip_odds and args.dry_run:
            print(f' [{len(need_odds)}场比赛, {day_odds}有赔率]', end='')
        
        if not args.dry_run:
            conn.commit()
        
        print(f' | 新增{day_new} 有赔率{day_odds}', end='')
        total_inserted += day_new
        total_odds_ok += day_odds
        
        # 每天请求间隔
        time.sleep(args.delay)
        cur_date += timedelta(days=1)
    
    print(f'\n\n{"="*50}')
    print(f'📊 统计:')
    print(f'   日期范围: {args.start} ~ {args.end} ({total_days}天)')
    print(f'   总比赛数: {total_matches}')
    print(f'   新增入库: {total_inserted}')
    print(f'   有赔率: {total_odds_ok}')
    
    if not args.dry_run:
        conn.close()
        print(f'\n✅ 完成! 运行 ai_analysis.py 即可更新看板')


if __name__ == '__main__':
    main()
