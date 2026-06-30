#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_500com_odds.py — 从500.com抓取Bet365赔率数据 v1

数据源: odds.500.com JSON API
公司: Bet365 (cid=3) — 替代HKJC位置
玩法: 1X2 / AH / OU (开收盘)

用法:
  python scripts/fetch_500com_odds.py --db data/football.db
  python scripts/fetch_500com_odds.py --db data/football.db --fid-only  # 只抓指定fid
  python scripts/fetch_500com_odds.py --db data/football.db --dry-run
  python scripts/fetch_500com_odds.py --db data/football.db --company 3  # Bet365(默认)
  python scripts/fetch_500com_odds.py --db data/football.db --company 293  # 威廉希尔
"""

import os
import re
import sys
import json
import sqlite3
import time
import argparse
import urllib.request
from datetime import datetime

# 500.com 公司ID
COMPANY_IDS = {
    'pinnacle': 1055,
    'bet365': 3,
    'liji': 651,
    'mingsheng': 140,
    'william': 293,
    'hkjc': 122,
    'crown': 280,
    'ladbrokes': 2,
    'saba': 127,
}

# DB字段前缀映射
DB_PREFIX = {
    'bet365': 'bet365',
    'william': 'william',  # 复用已有的william字段
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://odds.500.com/',
    'X-Requested-With': 'XMLHttpRequest',
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'raw', '500com')


def fetch_json(url, retries=2):
    """请求500.com JSON接口"""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
                # 清理BOM和JS包裹
                raw = raw.strip()
                if raw.startswith('(') and raw.endswith(')'):
                    raw = raw[1:-1]
                return json.loads(raw)
        except json.JSONDecodeError as e:
            if attempt == retries:
                return None
        except Exception as e:
            if attempt == retries:
                return None
        time.sleep(1)
    return None


def fetch_1x2(fid, cid):
    """抓取1X2欧赔历史
    
    返回: {'open': {'w':x, 'd':x, 'l':x}, 'close': {'w':x, 'd':x, 'l':x}} 或 None
    API: /fenxi/json/ouzhi.php?fid={fid}&cid={cid}&type=europe&r=1
    返回格式: [[w,d,l,return_rate,timestamp,...], ...]
    第1条=最新(收盘), 最后1条=最初(开盘)
    """
    url = f'https://odds.500.com/fenxi/json/ouzhi.php?fid={fid}&cid={cid}&type=europe&r=1'
    data = fetch_json(url)
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    
    try:
        # 收盘: 第1条
        close = data[0]
        # 开盘: 最后1条
        open_ = data[-1]
        
        result = {
            'close': {'w': float(close[0]), 'd': float(close[1]), 'l': float(close[2])},
            'open': {'w': float(open_[0]), 'd': float(open_[1]), 'l': float(open_[2])},
        }
        # 校验赔率合理性
        for key in ('close', 'open'):
            odds = result[key]
            if odds['w'] <= 1 or odds['d'] <= 1 or odds['l'] <= 1:
                return None
        return result
    except (IndexError, ValueError, TypeError):
        return None


def fetch_ah(fid, cid):
    """抓取亚盘历史
    
    返回: {'open': {'handicap':x, 'home_water':x, 'away_water':x}, 'close': {...}} 或 None
    API: /json/odds.php?fid={fid}&cid={cid}&type=asian&r=1
    返回格式: [[home_water,handicap,away_water,timestamp,...], ...]
    """
    url = f'https://odds.500.com/json/odds.php?fid={fid}&cid={cid}&type=asian&r=1'
    data = fetch_json(url)
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    
    try:
        close = data[0]
        result = {
            'close': {
                'home_water': float(close[0]),
                'handicap': float(close[1]),
                'away_water': float(close[2]),
            },
        }
        if len(data) >= 2:
            open_ = data[-1]
            result['open'] = {
                'home_water': float(open_[0]),
                'handicap': float(open_[1]),
                'away_water': float(open_[2]),
            }
        return result
    except (IndexError, ValueError, TypeError):
        return None


def fetch_ou(fid, cid):
    """抓取大小球历史
    
    返回: {'open': {'line':x, 'over':x, 'under':x}, 'close': {...}} 或 None
    API: /json/odds.php?fid={fid}&cid={cid}&type=daxiao&r=1
    返回格式: [[over,line,under,timestamp,...], ...]
    """
    url = f'https://odds.500.com/json/odds.php?fid={fid}&cid={cid}&type=daxiao&r=1'
    data = fetch_json(url)
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    
    try:
        close = data[0]
        result = {
            'close': {
                'over': float(close[0]),
                'line': float(close[1]),
                'under': float(close[2]),
            },
        }
        if len(data) >= 2:
            open_ = data[-1]
            result['open'] = {
                'over': float(open_[0]),
                'line': float(open_[1]),
                'under': float(open_[2]),
            }
        return result
    except (IndexError, ValueError, TypeError):
        return None


def fetch_all_odds(fid, cid):
    """抓取一个fid的1X2+AH+OU数据"""
    result = {'fid': fid, 'cid': cid}
    
    x2 = fetch_1x2(fid, cid)
    result['1x2'] = x2
    
    ah = fetch_ah(fid, cid)
    result['ah'] = ah
    
    ou = fetch_ou(fid, cid)
    result['ou'] = ou
    
    return result


def update_db(db_path, records, company='bet365', dry_run=False):
    """将抓取的赔率写入DB
    
    records: [{fid, 1x2, ah, ou}, ...]
    company: DB字段前缀
    """
    if not records:
        return 0
    
    prefix = DB_PREFIX.get(company, company)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    updated = 0
    
    for rec in records:
        fid = rec['fid']
        if not fid:
            continue
        
        sets = []
        params = []
        
        # 1X2
        x2 = rec.get('1x2')
        if x2:
            if 'open' in x2:
                sets.extend([
                    f"{prefix}_open_w = ?",
                    f"{prefix}_open_d = ?",
                    f"{prefix}_open_l = ?",
                ])
                params.extend([x2['open']['w'], x2['open']['d'], x2['open']['l']])
            if 'close' in x2:
                sets.extend([
                    f"{prefix}_close_w = ?",
                    f"{prefix}_close_d = ?",
                    f"{prefix}_close_l = ?",
                ])
                params.extend([x2['close']['w'], x2['close']['d'], x2['close']['l']])
        
        # AH
        ah = rec.get('ah')
        if ah:
            if 'close' in ah:
                sets.extend([
                    f"{prefix}_ah_handicap = ?",
                    f"{prefix}_ah_home_water = ?",
                    f"{prefix}_ah_away_water = ?",
                ])
                params.extend([ah['close']['handicap'], ah['close']['home_water'], ah['close']['away_water']])
            if 'open' in ah:
                sets.extend([
                    f"{prefix}_ah_open_handicap = ?",
                    f"{prefix}_ah_open_home_water = ?",
                    f"{prefix}_ah_open_away_water = ?",
                ])
                params.extend([ah['open']['handicap'], ah['open']['home_water'], ah['open']['away_water']])
        
        # OU
        ou = rec.get('ou')
        if ou:
            if 'close' in ou:
                sets.extend([
                    f"{prefix}_ou_line = ?",
                    f"{prefix}_ou_over = ?",
                    f"{prefix}_ou_under = ?",
                ])
                params.extend([ou['close']['line'], ou['close']['over'], ou['close']['under']])
            if 'open' in ou:
                sets.extend([
                    f"{prefix}_ou_open_line = ?",
                    f"{prefix}_ou_open_over = ?",
                    f"{prefix}_ou_open_under = ?",
                ])
                params.extend([ou['open']['line'], ou['open']['over'], ou['open']['under']])
        
        if not sets:
            continue
        
        params.append(fid)
        sql = f"UPDATE poisson_predictions SET {', '.join(sets)} WHERE fid_500 = ?"
        
        if not dry_run:
            cursor.execute(sql, params)
            if cursor.rowcount > 0:
                updated += 1
    
    if not dry_run and updated > 0:
        conn.commit()
    conn.close()
    return updated


def get_pending_fids(db_path, company='bet365', limit=50):
    """获取需要抓取Bet365赔率的fid列表"""
    prefix = DB_PREFIX.get(company, company)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 找有fid_500但bet365数据为空的记录
    c.execute(f"""
        SELECT DISTINCT fid_500, home_team, away_team, kickoff_time
        FROM poisson_predictions
        WHERE fid_500 IS NOT NULL
          AND ({prefix}_close_w = 0 OR {prefix}_close_w IS NULL)
        ORDER BY kickoff_time DESC
        LIMIT ?
    """, (limit,))
    
    rows = [{'fid': r[0], 'home': r[1], 'away': r[2], 'kickoff': r[3]} for r in c.fetchall()]
    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description="从500.com抓取Bet365赔率数据")
    parser.add_argument('--db', required=True, help='数据库路径')
    parser.add_argument('--company', default='bet365', 
                       choices=list(COMPANY_IDS.keys()),
                       help='公司名称 (默认: bet365)')
    parser.add_argument('--cid', type=int, help='直接指定公司ID (覆盖--company)')
    parser.add_argument('--fid', type=int, help='只抓指定fid')
    parser.add_argument('--limit', type=int, default=50, help='最多抓取场次数 (默认50)')
    parser.add_argument('--dry-run', action='store_true', help='只显示不写入')
    parser.add_argument('--delay', type=float, default=1.5, help='请求间隔秒数 (默认1.5)')
    parser.add_argument('--save-raw', action='store_true', help='保存原始JSON到data/raw/500com/')
    args = parser.parse_args()
    
    cid = args.cid or COMPANY_IDS[args.company]
    company = args.company
    
    if args.fid:
        # 单场模式
        print(f'🔍 抓取 fid={args.fid} {company}(cid={cid})')
        result = fetch_all_odds(args.fid, cid)
        
        x2 = result.get('1x2')
        ah = result.get('ah')
        ou = result.get('ou')
        
        if x2:
            print(f"  1X2: 开盘 {x2['open']['w']:.2f}/{x2['open']['d']:.2f}/{x2['open']['l']:.2f} → 收盘 {x2['close']['w']:.2f}/{x2['close']['d']:.2f}/{x2['close']['l']:.2f}")
        else:
            print(f"  1X2: ❌ 无数据")
        
        if ah:
            print(f"  AH:  开盘 {ah.get('open',{}).get('handicap','-')} {ah.get('open',{}).get('home_water','-')}/{ah.get('open',{}).get('away_water','-')} → 收盘 {ah['close']['handicap']} {ah['close']['home_water']}/{ah['close']['away_water']}")
        else:
            print(f"  AH:  ❌ 无数据")
        
        if ou:
            print(f"  OU:  开盘 {ou.get('open',{}).get('line','-')} {ou.get('open',{}).get('over','-')}/{ou.get('open',{}).get('under','-')} → 收盘 {ou['close']['line']} {ou['close']['over']}/{ou['close']['under']}")
        else:
            print(f"  OU:  ❌ 无数据")
        
        if not args.dry_run:
            n = update_db(args.db, [result], company)
            print(f"  DB: 更新{n}条")
        return
    
    # 批量模式
    pending = get_pending_fids(args.db, company, args.limit)
    if not pending:
        print(f'✅ 所有记录已有{company}赔率数据')
        return
    
    print(f'📋 待抓取: {len(pending)}场, 公司={company}(cid={cid}), 间隔={args.delay}s')
    
    results = []
    stats = {'1x2': 0, 'ah': 0, 'ou': 0, 'total': 0, 'empty': 0}
    
    for i, p in enumerate(pending):
        fid = p['fid']
        tag = f'[{i+1}/{len(pending)}]'
        sys.stdout.write(f'\r  {tag} {p["home"]} vs {p["away"]} (fid={fid}) ...')
        sys.stdout.flush()
        
        result = fetch_all_odds(fid, cid)
        
        has_data = result.get('1x2') or result.get('ah') or result.get('ou')
        if has_data:
            results.append(result)
            stats['total'] += 1
            if result.get('1x2'): stats['1x2'] += 1
            if result.get('ah'): stats['ah'] += 1
            if result.get('ou'): stats['ou'] += 1
        else:
            stats['empty'] += 1
        
        # 保存原始数据
        if args.save_raw and has_data:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, f'{fid}_{company}.json'), 'w') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        
        time.sleep(args.delay)
    
    print(f'\n\n📊 抓取完成: {stats["total"]}场有数据, {stats["empty"]}场无数据')
    print(f'   1X2={stats["1x2"]} AH={stats["ah"]} OU={stats["ou"]}')
    
    if results and not args.dry_run:
        n = update_db(args.db, results, company)
        print(f'   DB: 更新{n}条记录')
    elif args.dry_run:
        print(f'   [dry-run] 跳过DB写入 ({len(results)}条)')


if __name__ == '__main__':
    main()
