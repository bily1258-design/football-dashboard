#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_500com_odds.py — 从500.com抓取5家公司赔率数据 v2

数据源: odds.500.com JSON API
公司: Pinnacle(1055) / Bet365(3) / 利记(651) / 明升(140) / 威廉希尔(293)
玩法: 1X2 / AH / OU (开收盘)

用法:
  python scripts/fetch_500com_odds.py --db data/football.db
  python scripts/fetch_500com_odds.py --db data/football.db --company all
  python scripts/fetch_500com_odds.py --db data/football.db --company pinnacle
  python scripts/fetch_500com_odds.py --db data/football.db --fid 1359255
  python scripts/fetch_500com_odds.py --db data/football.db --dry-run
  python scripts/fetch_500com_odds.py --db data/football.db --rebuild  # 重刷所有有fid的记录
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

# ===== 500.com 公司ID → DB字段前缀映射 =====
COMPANY_CONFIG = {
    'pinnacle': {
        'cid': 1055,
        'db_prefix': 'pinnacle',  # 1x2用pinnacle_open_w等
        'ah_prefix': 'pin_ah',     # AH用pin_ah_handicap等
        'ou_prefix': 'pin_ou',     # OU用pin_ou_line等
    },
    'bet365': {
        'cid': 3,
        'db_prefix': 'bet365',
        'ah_prefix': 'bet365_ah',
        'ou_prefix': 'bet365_ou',
    },
    'liji': {
        'cid': 651,
        'db_prefix': 'liji',       # 新增1x2字段: liji_close_w等
        'ah_prefix': 'liji',       # 已有: liji_handicap等
        'ou_prefix': 'liji_ou',    # 已有: liji_ou_line等
    },
    'mingsheng': {
        'cid': 140,
        'db_prefix': 'ms',         # 新增1x2字段: ms_close_w等
        'ah_prefix': 'ms',         # 已有: ms_handicap等
        'ou_prefix': 'ms_ou',      # 已有: ms_ou_line等
    },
    'william': {
        'cid': 293,
        'db_prefix': 'william',    # 1x2用william_1x2_w(收盘)/william_open_w(开盘)
        'ah_prefix': 'william_ah',
        'ou_prefix': 'william_ou',
    },
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
                raw = raw.strip()
                if raw.startswith('(') and raw.endswith(')'):
                    raw = raw[1:-1]
                if not raw:
                    return None
                return json.loads(raw)
        except json.JSONDecodeError:
            if attempt == retries:
                return None
        except Exception:
            if attempt == retries:
                return None
        time.sleep(1)
    return None


def fetch_1x2(fid, cid):
    """抓取1X2欧赔历史
    
    API: /fenxi/json/ouzhi.php?fid={fid}&cid={cid}&type=europe&r=1
    返回: [[w,d,l,return_rate,timestamp,...], ...]
    第1条=最新(收盘), 最后1条=最初(开盘)
    """
    url = f'https://odds.500.com/fenxi/json/ouzhi.php?fid={fid}&cid={cid}&type=europe&r=1'
    data = fetch_json(url)
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    
    try:
        close = data[0]
        open_ = data[-1]
        
        result = {
            'close': {'w': float(close[0]), 'd': float(close[1]), 'l': float(close[2])},
            'open': {'w': float(open_[0]), 'd': float(open_[1]), 'l': float(open_[2])},
        }
        for key in ('close', 'open'):
            odds = result[key]
            if odds['w'] <= 1 or odds['d'] <= 1 or odds['l'] <= 1:
                return None
        return result
    except (IndexError, ValueError, TypeError):
        return None


def fetch_ah(fid, cid):
    """抓取亚盘历史
    
    API: /json/odds.php?fid={fid}&cid={cid}&type=asian&r=1
    返回: [[home_water,handicap,away_water,timestamp,...], ...]
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
    
    API: /json/odds.php?fid={fid}&cid={cid}&type=daxiao&r=1
    返回: [[over,line,under,timestamp,...], ...]
    注意: line可能是字符串如"2.5/3"
    """
    url = f'https://odds.500.com/json/odds.php?fid={fid}&cid={cid}&type=daxiao&r=1'
    data = fetch_json(url)
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    
    def parse_ou_record(rec):
        """解析OU记录，line可能是字符串"""
        over = float(rec[0])
        line_raw = rec[1]
        under = float(rec[2])
        # 处理 "2.5/3" 格式 → 2.75
        if isinstance(line_raw, str) and '/' in line_raw:
            parts = line_raw.split('/')
            try:
                low = float(parts[0])
                high = float(parts[1])
                line = (low + high) / 2
            except ValueError:
                line = low
        else:
            line = float(line_raw)
        return {'over': over, 'line': line, 'under': under}
    
    try:
        result = {'close': parse_ou_record(data[0])}
        if len(data) >= 2:
            result['open'] = parse_ou_record(data[-1])
        return result
    except (IndexError, ValueError, TypeError):
        return None


def fetch_all_odds(fid, cid):
    """抓取一个fid的1X2+AH+OU数据"""
    result = {'fid': fid, 'cid': cid}
    result['1x2'] = fetch_1x2(fid, cid)
    result['ah'] = fetch_ah(fid, cid)
    result['ou'] = fetch_ou(fid, cid)
    return result


def _build_1x2_sets(sets, params, x2, prefix):
    """构建1X2的SET子句
    
    DB列命名规则:
    - Pinnacle/Bet365/HKJC/LiJi/MS: {prefix}_close_w, {prefix}_open_w
    - William: william_1x2_w (收盘), william_open_w (开盘) — 旧字段兼容
    """
    # 威廉希尔close用1x2前缀（兼容旧字段william_1x2_w），open用新字段william_open_w
    if prefix == 'william':
        close_prefix = 'william_1x2'
        open_prefix = 'william_open'
    else:
        # 其他公司统一用 {prefix}_close_x / {prefix}_open_x
        close_prefix = f'{prefix}_close'
        open_prefix = f'{prefix}_open'
    
    if x2:
        if 'close' in x2:
            for k, col in [('w', f'{close_prefix}_w'), ('d', f'{close_prefix}_d'), ('l', f'{close_prefix}_l')]:
                sets.append(f"{col} = ?")
                params.append(x2['close'][k])
        if 'open' in x2:
            for k, col in [('w', f'{open_prefix}_w'), ('d', f'{open_prefix}_d'), ('l', f'{open_prefix}_l')]:
                sets.append(f"{col} = ?")
                params.append(x2['open'][k])


def _build_ah_sets(sets, params, ah, prefix):
    """构建AH的SET子句"""
    if ah:
        if 'close' in ah:
            sets.extend([
                f"{prefix}_handicap = ?",
                f"{prefix}_home_water = ?",
                f"{prefix}_away_water = ?",
            ])
            params.extend([ah['close']['handicap'], ah['close']['home_water'], ah['close']['away_water']])
        if 'open' in ah:
            sets.extend([
                f"{prefix}_open_handicap = ?",
                f"{prefix}_open_home_water = ?",
                f"{prefix}_open_away_water = ?",
            ])
            params.extend([ah['open']['handicap'], ah['open']['home_water'], ah['open']['away_water']])


def _build_ou_sets(sets, params, ou, prefix):
    """构建OU的SET子句"""
    if ou:
        if 'close' in ou:
            sets.extend([
                f"{prefix}_line = ?",
                f"{prefix}_over = ?",
                f"{prefix}_under = ?",
            ])
            params.extend([ou['close']['line'], ou['close']['over'], ou['close']['under']])
        if 'open' in ou:
            sets.extend([
                f"{prefix}_open_line = ?",
                f"{prefix}_open_over = ?",
                f"{prefix}_open_under = ?",
            ])
            params.extend([ou['open']['line'], ou['open']['over'], ou['open']['under']])


def update_db(db_path, records, company, dry_run=False):
    """将抓取的赔率写入DB"""
    if not records:
        return 0
    
    cfg = COMPANY_CONFIG[company]
    db_prefix = cfg['db_prefix']
    ah_prefix = cfg['ah_prefix']
    ou_prefix = cfg['ou_prefix']
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 确保DB有所需的列
    required_cols = [
        (f'{db_prefix}_open_w', 'REAL'), (f'{db_prefix}_open_d', 'REAL'), (f'{db_prefix}_open_l', 'REAL'),
        (f'{db_prefix}_close_w', 'REAL'), (f'{db_prefix}_close_d', 'REAL'), (f'{db_prefix}_close_l', 'REAL'),
        (f'{ah_prefix}_handicap', 'REAL'), (f'{ah_prefix}_home_water', 'REAL'), (f'{ah_prefix}_away_water', 'REAL'),
        (f'{ah_prefix}_open_handicap', 'REAL'), (f'{ah_prefix}_open_home_water', 'REAL'), (f'{ah_prefix}_open_away_water', 'REAL'),
        (f'{ou_prefix}_line', 'REAL'), (f'{ou_prefix}_over', 'REAL'), (f'{ou_prefix}_under', 'REAL'),
        (f'{ou_prefix}_open_line', 'REAL'), (f'{ou_prefix}_open_over', 'REAL'), (f'{ou_prefix}_open_under', 'REAL'),
    ]
    cursor.execute("PRAGMA table_info(poisson_predictions)")
    existing = {row[1] for row in cursor.fetchall()}
    for col, ctype in required_cols:
        if col not in existing:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
            print(f"  [DB] 新增列: {col}")
    conn.commit()
    updated = 0
    
    for rec in records:
        fid = rec['fid']
        if not fid:
            continue
        
        sets = []
        params = []
        
        _build_1x2_sets(sets, params, rec.get('1x2'), db_prefix)
        _build_ah_sets(sets, params, rec.get('ah'), ah_prefix)
        _build_ou_sets(sets, params, rec.get('ou'), ou_prefix)
        
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


def get_pending_fids(db_path, company, limit=50, rebuild=False):
    """获取需要抓取赔率的fid列表
    
    rebuild=True时返回所有有fid的记录
    否则检查1X2+AH+OU三组字段，任一缺失就重新抓取
    """
    cfg = COMPANY_CONFIG[company]
    db_prefix = cfg['db_prefix']
    ah_prefix = cfg['ah_prefix']
    ou_prefix = cfg['ou_prefix']
    
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    if rebuild:
        c.execute(f"""
            SELECT DISTINCT fid_500, home_team, away_team, kickoff_time
            FROM poisson_predictions
            WHERE fid_500 IS NOT NULL
            ORDER BY kickoff_time DESC
            LIMIT ?
        """, (limit,))
    else:
        # 威廉希尔close用1x2前缀
        if company == 'william':
            check_1x2 = 'william_1x2_w'
        else:
            check_1x2 = f'{db_prefix}_close_w'
        check_ah = f'{ah_prefix}_handicap'
        check_ou = f'{ou_prefix}_line'
        
        # 任一数据组缺失就视为pending（1X2/AH/OU可能有先后上线时间差）
        c.execute(f"""
            SELECT DISTINCT fid_500, home_team, away_team, kickoff_time
            FROM poisson_predictions
            WHERE fid_500 IS NOT NULL
              AND (
                {check_1x2} = 0 OR {check_1x2} IS NULL
                OR {check_ah} = 0 OR {check_ah} IS NULL
                OR {check_ou} = 0 OR {check_ou} IS NULL
              )
            ORDER BY kickoff_time DESC
            LIMIT ?
        """, (limit,))
    rows = [{'fid': r[0], 'home': r[1], 'away': r[2], 'kickoff': r[3]} for r in c.fetchall()]
    conn.close()
    return rows





def main():
    parser = argparse.ArgumentParser(description="从500.com抓取5家公司赔率数据 v2")
    parser.add_argument('--db', required=True, help='数据库路径')
    parser.add_argument('--company', default='all',
                       choices=['all'] + list(COMPANY_CONFIG.keys()),
                       help='公司名称 (默认: all)')
    parser.add_argument('--fid', type=int, help='只抓指定fid')
    parser.add_argument('--limit', type=int, default=100, help='每家公司最多抓取场次数 (默认100)')
    parser.add_argument('--dry-run', action='store_true', help='只显示不写入')
    parser.add_argument('--delay', type=float, default=1.5, help='请求间隔秒数 (默认1.5)')
    parser.add_argument('--save-raw', action='store_true', help='保存原始JSON到data/raw/500com/')
    parser.add_argument('--rebuild', action='store_true', help='重刷所有有fid的记录(含已有赔率)')
    args = parser.parse_args()
    
    companies = list(COMPANY_CONFIG.keys()) if args.company == 'all' else [args.company]
    
    if args.fid:
        # 单场模式 — 抓所有公司
        for company in companies:
            cfg = COMPANY_CONFIG[company]
            cid = cfg['cid']
            print(f'\n🔍 [{company}] fid={args.fid} (cid={cid})')
            result = fetch_all_odds(args.fid, cid)
            
            x2 = result.get('1x2')
            ah = result.get('ah')
            ou = result.get('ou')
            
            if x2:
                o = x2.get('open', {})
                c = x2.get('close', {})
                print(f"  1X2: 开 {o.get('w','-'):.2f}/{o.get('d','-'):.2f}/{o.get('l','-'):.2f} → 收 {c.get('w','-'):.2f}/{c.get('d','-'):.2f}/{c.get('l','-'):.2f}")
            else:
                print(f"  1X2: ❌ 无数据")
            
            if ah:
                o = ah.get('open', {})
                c = ah.get('close', {})
                print(f"  AH:  开 {o.get('handicap','-')} {o.get('home_water','-')}/{o.get('away_water','-')} → 收 {c['handicap']} {c['home_water']}/{c['away_water']}")
            else:
                print(f"  AH:  ❌ 无数据")
            
            if ou:
                o = ou.get('open', {})
                c = ou.get('close', {})
                print(f"  OU:  开 {o.get('line','-')} {o.get('over','-')}/{o.get('under','-')} → 收 {c['line']} {c['over']}/{c['under']}")
            else:
                print(f"  OU:  ❌ 无数据")
            
            if not args.dry_run:
                n = update_db(args.db, [result], company)
                print(f"  DB: 更新{n}条")
        
        return
    
    # 批量模式
    total_updated = 0
    for company in companies:
        cfg = COMPANY_CONFIG[company]
        cid = cfg['cid']
        
        pending = get_pending_fids(args.db, company, args.limit, args.rebuild)
        if not pending:
            print(f'✅ [{company}] 所有记录已有赔率数据')
            continue
        
        print(f'\n📋 [{company}] 待抓取: {len(pending)}场 (cid={cid}), 间隔={args.delay}s')
        
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
            
            if args.save_raw and has_data:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(os.path.join(DATA_DIR, f'{fid}_{company}.json'), 'w') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
            
            time.sleep(args.delay)
        
        print(f'\n  📊 [{company}] {stats["total"]}场有数据, {stats["empty"]}场无数据')
        print(f'     1X2={stats["1x2"]} AH={stats["ah"]} OU={stats["ou"]}')
        
        if results and not args.dry_run:
            n = update_db(args.db, results, company)
            print(f'     DB: 更新{n}条记录')
            total_updated += n
        elif args.dry_run:
            print(f'     [dry-run] 跳过DB写入 ({len(results)}条)')
    
    print(f'\n🎉 总计更新 {total_updated} 条记录')


if __name__ == '__main__':
    main()
