#!/usr/bin/env python3
"""
预抓所有历史比赛的H2H/近期战绩统计，存到 stats_cache.json
用法: python3 prefetch_stats_cache.py [--force]
"""
import os
import sys
import json
import sqlite3
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_stats import fetch_match_stats

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
CACHE_PATH = os.path.join(CACHE_DIR, 'stats_cache.json')
os.makedirs(CACHE_DIR, exist_ok=True)

def main():
    force = '--force' in sys.argv
    
    # 加载已有缓存
    cache = {}
    if os.path.exists(CACHE_PATH) and not force:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        print(f"已存在缓存: {len(cache)} 场")
    else:
        print("新建缓存" if force else "新建缓存")
    
    # 从DB获取所有fid
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT DISTINCT fid_500, home_team, away_team, date FROM poisson_predictions "
        "WHERE fid_500 IS NOT NULL ORDER BY date"
    )
    rows = cur.fetchall()
    conn.close()
    print(f"DB中共 {len(rows)} 个唯一fid\n")
    
    new_count = 0
    fail_count = 0
    skip_count = 0
    total = len(rows)
    
    for i, (fid, home, away, date) in enumerate(rows, 1):
        fid_str = str(fid)
        if fid_str in cache:
            skip_count += 1
            if i % 200 == 0:
                print(f"  [{i}/{total}] 跳过 {fid} (已有), 进度: {skip_count}/{total}")
            continue
        
        print(f"  [{i}/{total}] 抓取 {fid} {home} vs {away} ({date})...", end=' ')
        try:
            stats = fetch_match_stats(fid)
            if stats is not None and (stats.get('h2h') or stats.get('home_form') or stats.get('away_form')):
                cache[fid_str] = stats
                new_count += 1
                print(f"✓ (h2h={len(stats.get('h2h',[]))}, home={len(stats.get('home_form',[]))}, away={len(stats.get('away_form',[]))})")
            else:
                # 空结果也存，下次跳过
                cache[fid_str] = None
                fail_count += 1
                print(f"✗ 空结果")
        except Exception as e:
            cache[fid_str] = None
            fail_count += 1
            print(f"✗ {e}")
        
        # 每10场存一次防止丢失
        if new_count % 10 == 0 and new_count > 0:
            with open(CACHE_PATH, 'w') as f:
                json.dump(cache, f)
        
        # 控制请求速率
        time.sleep(0.3)
    
    # 最终保存
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f)
    
    print(f"\n{'='*50}")
    print(f"完成! 新增: {new_count}, 跳过: {skip_count}, 失败/空: {fail_count}")
    print(f"缓存总计: {sum(1 for v in cache.values() if v is not None)} 场有数据, "
          f"{sum(1 for v in cache.values() if v is None)} 场无数据")
    print(f"保存至: {CACHE_PATH}")

if __name__ == '__main__':
    main()
