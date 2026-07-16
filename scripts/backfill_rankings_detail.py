#!/usr/bin/env python3
"""
补丁：用 detail.php 为未匹配到的比赛回填排名
每个 fid 查 detail.php 获取"当前排名"，准确度最高
"""
import urllib.request, re, sqlite3, sys, os, time

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')
BATCH_SIZE = 20
DELAY = 0.3

def fetch(url, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36'
            })
            return urllib.request.urlopen(req, timeout=10).read().decode('gbk', errors='replace')
        except Exception:
            time.sleep(2**i)
    return None

def get_ranks_from_detail(fid):
    """从 detail.php 获取主客队排名"""
    html = fetch(f'https://live.500.com/detail.php?fid={fid}')
    if not html:
        return None
    ranks = re.findall(r'当前排名:(\d+)', html)
    if len(ranks) >= 2:
        return int(ranks[0]), int(ranks[1])
    return None

def main():
    conn = sqlite3.connect(DB_PATH)
    
    # 获取所有仍为0 rank的比赛
    cur = conn.execute('''
        SELECT id, fid_500, league, home_team, away_team, home_ranking
        FROM poisson_predictions
        WHERE reference_score != '' 
          AND fid_500 IS NOT NULL AND fid_500 > 0
          AND (home_ranking IS NULL OR home_ranking = 0)
        ORDER BY date DESC
    ''')
    samples = cur.fetchall()
    conn.close()
    
    total = len(samples)
    print(f"待处理: {total} 场比赛")
    if total == 0:
        return
    
    conn = sqlite3.connect(DB_PATH)
    updated = 0
    errors = 0
    
    for i, (sid, fid, league, ht, at, hr) in enumerate(samples):
        result = get_ranks_from_detail(str(int(fid)))
        if result:
            h_rank, a_rank = result
            conn.execute('''
                UPDATE poisson_predictions 
                SET home_ranking=?, away_ranking=? 
                WHERE id=?
            ''', (h_rank, a_rank, sid))
            updated += 1
        else:
            errors += 1
        
        # 批量提交
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            eta = (total - i - 1) * DELAY / 60
            print(f'  [{i+1}/{total}] 成功{updated} 失败{errors} 估计剩余{eta:.1f}分钟')
        
        time.sleep(DELAY)
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ 完成")
    print(f"   成功: {updated}")
    print(f"   失败: {errors}")
    print(f"   剩余未更新: {total - updated}")

if __name__ == '__main__':
    main()
