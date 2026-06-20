#!/usr/bin/env python3
"""
fetch_zgzcw_results.py — 从足彩网抓取竞彩赛果

数据源: live.zgzcw.com/jz/ (竞彩) + live.zgzcw.com/bd/ (北单)
优势: 队名与赔率数据同源，无需跨源队名归一化

用法:
  python fetch_zgzcw_results.py                          # 昨天赛果
  python fetch_zgzcw_results.py --date 2026-06-15        # 指定日期
  python fetch_zgzcw_results.py --date 2026-06-15 --backfill  # 抓取+回填DB
  python fetch_zgzcw_results.py --backfill-last 7        # 回填最近N天
"""

import os
import re
import sys
import json
import time
import argparse
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from html import unescape

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(REPO_DIR, 'data', 'football.db')
CACHE_DIR = os.path.join(REPO_DIR, 'data', 'cache')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://live.zgzcw.com/',
}

# 足彩网页面类型
PAGE_JZ = 'jz'   # 竞彩
PAGE_BD = 'bd'   # 北京单场


def fetch_page(date_str: str, page_type: str = PAGE_JZ) -> Optional[str]:
    """获取足彩网比分直播页面HTML"""
    import urllib.request
    url = f'https://live.zgzcw.com/{page_type}/?date={date_str}'
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                print(f'  ❌ HTTP {resp.status}')
                return None
            raw = resp.read()
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('gbk', errors='replace')
            return text
    except Exception as e:
        print(f'  ❌ 请求失败: {e}')
        return None


def parse_jz_results(html: str) -> List[Dict]:
    """解析竞彩赛果页面HTML，提取完场比赛的比分
    
    足彩网页面文本格式（fetch_web渲染后）：
    序号赛事轮次时间状态主队（让球）比分客队半场...
    例: 周一013世界杯 小组赛 06-16 00:00 **完** 0西班牙(**-2** ) 0-0佛得角0 0-0**101-10:00下双**
    """
    results = []
    
    # 按行处理
    lines = html.split('\n') if '\n' in html else [html]
    
    for line in lines:
        # 只处理完场比赛
        if '**完**' not in line and '完' not in line:
            continue
        
        # 清理标记
        clean = line.replace('**完**', '').replace('**', '')
        clean = re.sub(r'\*\[\d+\]\*', '', clean)  # 排名标记 *[4]*
        clean = re.sub(r'<[^>]+>', ' ', clean)      # HTML标签
        from html import unescape as _unescape
        clean = _unescape(clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        # 提取比分：让球括号 ) 后面紧跟的 数字-数字
        score_m = re.search(r'\)\s*(\d+)\s*[-–]\s*(\d+)\s*([\u4e00-\u9fa5A-Za-z·\'.]+)', clean)
        if not score_m:
            # 无让球括号的情况：队名 数字-数字 队名
            score_m = re.search(
                r'([\u4e00-\u9fa5A-Za-z·\'.]+)\s+(\d+)\s*[-–]\s*(\d+)\s+([\u4e00-\u9fa5A-Za-z·\'.]+)',
                clean
            )
            if score_m:
                home = score_m.group(1).strip()
                home_score = int(score_m.group(2))
                away_score = int(score_m.group(3))
                away = score_m.group(4).strip()
            else:
                continue
        else:
            home_score = int(score_m.group(1))
            away_score = int(score_m.group(2))
            away = score_m.group(3).strip()
            # 提取主队：让球括号前面的中文名
            home_m = re.search(r'([\u4e00-\u9fa5A-Za-z·\'.]+)\s*(?:\([^)]*\))', clean)
            home = home_m.group(1).strip() if home_m else ''
        
        # 过滤无效数据
        if home_score > 20 or away_score > 20:
            continue
        if len(home) < 2 or len(away) < 2:
            continue
        
        # 提取赛事
        league = '未知'
        league_match = re.search(
            r'(世界杯|欧洲杯|英超|西甲|意甲|德甲|法甲|中超|芬超|瑞典超|瑞典超甲|巴西甲|巴西乙|'
            r'冰岛超|智利甲|日职|韩职|澳超|美职|挪超|挪甲|丹超|波超|荷甲|葡超|比甲|俄超|'
            r'苏超|奥甲|捷甲|瑞士超|罗甲|匈甲|希超|土超|国际友谊|日乙|日联杯|韩联杯|'
            r'英冠|英甲|英乙|西乙|意乙|德乙|法乙|中超杯|足协杯|社区盾|超级杯)',
            clean
        )
        if league_match:
            league = league_match.group(1)
        
        time_match = re.search(r'(\d{2}:\d{2})', clean)
        time_str = time_match.group(1) if time_match else ''
        
        # 判定赛果方向
        if home_score > away_score:
            outcome = '主胜'
        elif home_score == away_score:
            outcome = '平局'
        else:
            outcome = '客胜'
        
        score_str = f'{home_score}-{away_score}'
        
        results.append({
            'home': home,
            'away': away,
            'home_score': home_score,
            'away_score': away_score,
            'score': score_str,
            'outcome': f'{outcome} {score_str}',
            'league': league,
            'time': time_str,
            'source': 'zgzcw_jz',
        })
    
    return results


def fetch_results(date_str: str, page_type: str = PAGE_JZ) -> List[Dict]:
    """抓取指定日期的赛果"""
    type_name = '竞彩' if page_type == PAGE_JZ else '北单'
    print(f'📥 足彩网{type_name}赛果: {date_str}')
    
    html = fetch_page(date_str, page_type)
    if not html:
        # 检查是否被WAF拦截
        if '访问被拦截' in str(html) or '418' in str(html):
            print('  ⚠️ WAF拦截，尝试Playwright方案...')
            return fetch_with_playwright(date_str, page_type)
        return []
    
    # 检查WAF
    if '访问被拦截' in html or '攻击行为' in html:
        print('  ⚠️ WAF拦截，尝试Playwright方案...')
        return fetch_with_playwright(date_str, page_type)
    
    results = parse_jz_results(html)
    print(f'  ✅ {type_name}完场: {len(results)} 场')
    
    # 保存缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f'zgzcw_{page_type}_{date_str.replace("-", "")}.json')
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump({'date': date_str, 'results': results, 'fetch_time': datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2)
    
    return results


def fetch_with_playwright(date_str: str, page_type: str = PAGE_JZ) -> List[Dict]:
    """WAF拦截时用Playwright DOM提取"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('  ❌ 未安装playwright，无法绕过WAF')
        print('     安装: pip install playwright && playwright install chromium')
        return []
    
    type_name = '竞彩' if page_type == PAGE_JZ else '北单'
    url = f'https://live.zgzcw.com/{page_type}/?date={date_str}'
    
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 '
                       'Chrome/90.0.4430.91 Mobile Safari/537.36',
            viewport={'width': 412, 'height': 915},
            locale='zh-CN',
        )
        page = context.new_page()
        
        print(f'  [Playwright] 加载{type_name}页面...')
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        
        # 滚动加载所有比赛
        for _ in range(5):
            page.evaluate('window.scrollBy(0, 500)')
            time.sleep(0.5)
        
        # 提取DOM文本
        body_text = page.evaluate('document.body.innerText')
        
        # 解析文本中的赛果
        # 格式: "队名 数字-数字 队名" 或 "队名 数字 - 数字 队名"
        lines = body_text.split('\n')
        for i, line in enumerate(lines):
            # 匹配完场比赛的比分
            score_match = re.search(
                r'([\u4e00-\u9fa5A-Za-z·\'.]+)\s+(\d+)\s*[-–]\s*(\d+)\s+([\u4e00-\u9fa5A-Za-z·\'.]+)',
                line.strip()
            )
            if not score_match:
                continue
            
            home = score_match.group(1).strip()
            hs = int(score_match.group(2))
            as_ = int(score_match.group(3))
            away = score_match.group(4).strip()
            
            if hs > 20 or as_ > 20:
                continue
            if len(home) < 2 or len(away) < 2:
                continue
            
            # 检查附近行是否有"完"标记
            context_text = ' '.join(lines[max(0, i-3):i+3])
            if '完' not in context_text:
                continue
            
            outcome = '主胜' if hs > as_ else ('平局' if hs == as_ else '客胜')
            score_str = f'{hs}-{as_}'
            
            results.append({
                'home': home,
                'away': away,
                'home_score': hs,
                'away_score': as_,
                'score': score_str,
                'outcome': f'{outcome} {score_str}',
                'league': '未知',
                'time': '',
                'source': f'zgzcw_{page_type}_pw',
            })
        
        browser.close()
    
    print(f'  ✅ Playwright {type_name}完场: {len(results)} 场')
    return results


def backfill_db(results: List[Dict], db_path: str = None) -> int:
    """用赛果回填数据库"""
    if not results:
        print('  无赛果数据可回填')
        return 0
    
    db_path = db_path or DB_PATH
    if not os.path.exists(db_path):
        print(f'  ❌ DB不存在: {db_path}')
        return 0
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 获取DB中所有缺少赛果的记录
    cursor.execute("""
        SELECT id, home_team, away_team, actual_outcome, kickoff_time, date
        FROM poisson_predictions
    """)
    db_records = [dict(r) for r in cursor.fetchall()]
    
    updated = 0
    for rec in db_records:
        # 已有赛果则跳过
        if rec['actual_outcome'] and re.search(r'\d+-\d+', rec['actual_outcome']):
            continue
        
        for res in results:
            if team_match(rec['home_team'], res['home']) and team_match(rec['away_team'], res['away']):
                cursor.execute(
                    'UPDATE poisson_predictions SET actual_outcome = ? WHERE id = ?',
                    (res['outcome'], rec['id'])
                )
                updated += 1
                break
    
    conn.commit()
    conn.close()
    print(f'  ✅ 回填 {updated} 条赛果')
    return updated


def team_match(a: str, b: str) -> bool:
    """队名匹配（同源数据一般精确匹配，加模糊兜底）"""
    if not a or not b:
        return False
    a = a.strip()
    b = b.strip()
    if a == b:
        return True
    # 子串匹配
    if a in b or b in a:
        return True
    # 去除常见后缀
    for suffix in ['FC', 'SC', 'CF', 'CD', 'SV', 'IF', 'FK', 'BK', 'AC', 'AS', 'SS', 'SP', 'RJ', 'MG', 'PA', 'RS', 'GO', 'CE', 'BA', 'SE', 'EC', 'AA', 'FT', 'MT', 'PI', 'PB', 'PR', 'GP', 'AP', 'TP', 'BB', 'CB']:
        a2 = re.sub(rf'\s*{suffix}\s*$', '', a, flags=re.IGNORECASE)
        b2 = re.sub(rf'\s*{suffix}\s*$', '', b, flags=re.IGNORECASE)
        if a2 == b2:
            return True
        if a2 in b2 or b2 in a2:
            return True
    return False


def backfill_last_n_days(n: int = 7, db_path: str = None) -> int:
    """回填最近N天的赛果"""
    total = 0
    for i in range(1, n + 1):
        date_str = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        print(f'\n--- {date_str} ---')
        results = fetch_results(date_str, PAGE_JZ)
        # 也抓北单
        bd_results = fetch_results(date_str, PAGE_BD)
        all_results = results + bd_results
        updated = backfill_db(all_results, db_path)
        total += updated
    print(f'\n总计回填 {total} 条')
    return total


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='足彩网赛果抓取')
    parser.add_argument('--date', help='指定日期 YYYY-MM-DD，默认昨天')
    parser.add_argument('--backfill', action='store_true', help='抓取后回填DB')
    parser.add_argument('--backfill-last', type=int, help='回填最近N天')
    parser.add_argument('--type', choices=['jz', 'bd', 'all'], default='jz',
                        help='页面类型: jz=竞彩, bd=北单, all=两者')
    parser.add_argument('--debug', action='store_true', help='输出调试信息')
    args = parser.parse_args()
    
    if args.backfill_last:
        backfill_last_n_days(args.backfill_last)
    else:
        date_str = args.date or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        all_results = []
        if args.type in ('jz', 'all'):
            all_results += fetch_results(date_str, PAGE_JZ)
        if args.type in ('bd', 'all'):
            all_results += fetch_results(date_str, PAGE_BD)
        
        if args.debug:
            for r in all_results:
                print(f"  {r['league']} | {r['home']} {r['score']} {r['away']}")
        
        if args.backfill:
            backfill_db(all_results)
        else:
            print(f'\n共 {len(all_results)} 场赛果（--backfill 回填DB）')
