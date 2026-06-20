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
    
    足彩网页面文本格式（dump-dom / fetch_web渲染后）：
    周四025世界杯 小组赛 06-19 00:00 **完** 0捷克(**-1** ) 1-1南非0 1-0**103-11:12下双**
    
    关键格式：
    - **完**标记完场
    - **完**后紧跟排名数字(0)然后主队名
    - 主队名后让球括号如(**-1** )
    - )后紧跟 比分(如1-1)
    - 比分后紧跟客队名
    - 客队名后可能紧跟排名数字
    - 排名后半场比分如0 1-0
    - **...**标记各种附加信息
    """
    results = []
    
    # 先提取每场比赛的块：以周X+数字开头
    # 每场比赛从 "周X数字" 或 "周X数字...完" 开始
    # 用正则按"完"标记切分
    
    # 按行或整段处理
    lines = html.split('\n') if '\n' in html else [html]
    
    for line in lines:
        # 只处理完场比赛
        if '完' not in line:
            continue
        
        # 找所有完场块：以"周"开头到下一个"周"或行尾
        # 先按"周"拆分出各场比赛
        matches = re.finditer(
            r'(周[一二三四五六日]\d+.*?完.*?)(?=周[一二三四五六日]\d+|$)',
            line
        )
        
        for m in matches:
            block = m.group(1)
            result = _parse_single_match(block)
            if result:
                results.append(result)
    
    return results


def _parse_single_match(block: str) -> Optional[Dict]:
    """解析单场比赛块，提取比分信息"""
    # 清理标记
    clean = block.replace('**完**', '完').replace('**', '')
    clean = re.sub(r'\*\[\d+\]\*', '', clean)   # 排名 *[3]*
    clean = re.sub(r'<[^>]+>', ' ', clean)        # HTML标签
    clean = unescape(clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    
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
    
    # 提取比分和队名
    # 格式1: 让球括号 )后紧跟比分: 捷克(**-1** ) 1-1南非0
    #   ) 1-1  → 比分
    #   主队在让球括号前，客队在比分后
    
    # 方案A: 有让球括号
    score_m = re.search(r'\)\s*(\d+)\s*[-–]\s*(\d+)', clean)
    if score_m:
        home_score = int(score_m.group(1))
        away_score = int(score_m.group(2))
        
        # 主队：完/排名数字后到让球括号前
        # "完 0捷克(**-1** )" → 取完和(之间的中文
        pre_paren = clean[:score_m.start()].rstrip()
        home_m = re.search(r'([\u4e00-\u9fa5A-Za-z·\'.\-\s]+?)\s*\(', pre_paren)
        if home_m:
            # 取最后一个匹配（括号前最近的队名）
            home = home_m.group(1).strip()
            # 去掉开头的数字（排名）
            home = re.sub(r'^\d+', '', home).strip()
        else:
            home = ''
        
        # 客队：比分后紧跟的中文名
        after_score = clean[score_m.end():]
        away_m = re.match(r'\s*([\u4e00-\u9fa5A-Za-z·\'.]+)', after_score)
        away = away_m.group(1).strip() if away_m else ''
    else:
        # 方案B: 无让球括号，直接队名 比分 队名
        score_m = re.search(
            r'([\u4e00-\u9fa5A-Za-z·\'.]+)\s+(\d+)\s*[-–]\s*(\d+)\s+([\u4e00-\u9fa5A-Za-z·\'.]+)',
            clean
        )
        if score_m:
            home = re.sub(r'^\d+', '', score_m.group(1)).strip()
            home_score = int(score_m.group(2))
            away_score = int(score_m.group(3))
            away = score_m.group(4).strip()
        else:
            return None
    
    # 过滤无效数据
    if home_score > 20 or away_score > 20:
        return None
    if len(home) < 2 or len(away) < 2:
        return None
    
    # 判定赛果方向
    if home_score > away_score:
        outcome = '主胜'
    elif home_score == away_score:
        outcome = '平局'
    else:
        outcome = '客胜'
    
    score_str = f'{home_score}-{away_score}'
    
    return {
        'home': home,
        'away': away,
        'home_score': home_score,
        'away_score': away_score,
        'score': score_str,
        'outcome': f'{outcome} {score_str}',
        'league': league,
        'time': time_str,
        'source': 'zgzcw_jz',
    }


def fetch_results(date_str: str, page_type: str = PAGE_JZ) -> List[Dict]:
    """抓取指定日期的赛果"""
    type_name = '竞彩' if page_type == PAGE_JZ else '北单'
    print(f'📥 足彩网{type_name}赛果: {date_str}')
    
    html = fetch_page(date_str, page_type)
    if not html:
        # 检查是否被WAF拦截
        if '访问被拦截' in str(html) or '418' in str(html):
            print('  ⚠️ WAF拦截，尝试浏览器方案...')
            return fetch_with_browser(date_str, page_type)
        return []
    
    # 检查WAF
    if '访问被拦截' in html or '攻击行为' in html:
        print('  ⚠️ WAF拦截，尝试浏览器方案...')
        return fetch_with_browser(date_str, page_type)
    
    results = parse_jz_results(html)
    
    # urllib拿到JS骨架(0场)→Playwright兜底渲染
    if not results:
        print(f'  ⚠️ urllib解析0场（可能JS未渲染），尝试浏览器...')
        return fetch_with_browser(date_str, page_type)
    
    print(f'  ✅ {type_name}完场: {len(results)} 场')
    
    # 保存缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f'zgzcw_{page_type}_{date_str.replace("-", "")}.json')
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump({'date': date_str, 'results': results, 'fetch_time': datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2)
    
    return results


def fetch_with_browser(date_str: str, page_type: str = PAGE_JZ) -> List[Dict]:
    """JS未渲染时用浏览器DOM提取（优先chromium dump-dom，回退selenium/playwright）"""
    type_name = '竞彩' if page_type == PAGE_JZ else '北单'
    url = f'https://live.zgzcw.com/{page_type}/?date={date_str}'
    
    # --- 方案1: chromium --dump-dom (Termux最简单，不需要chromedriver) ---
    import shutil
    import subprocess
    # Termux / 常见Linux路径
    _CHROMIUM_CANDIDATES = [
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
        shutil.which('google-chrome'),
        '/data/data/com.termux/files/usr/bin/chromium',
        '/data/data/com.termux/files/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
    ]
    chromium_path = next((p for p in _CHROMIUM_CANDIDATES if p and os.path.isfile(p)), None)
    if not chromium_path:
        print('  ⚠️ 未找到chromium，跳过dump-dom方案')
    if chromium_path:
        print(f'  [chromium dump-dom] 加载{type_name}页面... (chromium={chromium_path})')
        try:
            result = subprocess.run(
                [
                    chromium_path,
                    '--headless',
                    '--no-sandbox',
                    '--disable-gpu',
                    '--disable-dev-shm-usage',
                    '--virtual-time-budget=8000',
                    f'--user-agent=Mozilla/5.0 (Linux; Android 11; Pixel 5) '
                    'AppleWebKit/537.36 Chrome/90.0.4430.91 Mobile Safari/537.36',
                    '--lang=zh-CN',
                    '--dump-dom',
                    url,
                ],
                capture_output=True, text=True, timeout=30,
            )
            html = result.stdout
            if html and len(html) > 500:
                results = parse_jz_results(html)
                if results:
                    print(f'  ✅ chromium dump-dom {type_name}完场: {len(results)} 场')
                    return results
                # 正则兜底
                results = _fallback_parse(html, page_type)
                if results:
                    print(f'  ✅ chromium dump-dom(兜底) {type_name}完场: {len(results)} 场')
                    return results
                if '--debug' in sys.argv or not results:
                    print(f'  ⚠️ dump-dom拿到 {len(html)} 字符但解析0场')
                    # 保存调试（含更多内容方便排查）
                    debug_file = os.path.join(CACHE_DIR or '/tmp', f'debug_dump_{page_type}_{date_str}.html')
                    os.makedirs(os.path.dirname(debug_file), exist_ok=True)
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(html[:20000])
                    print(f'  📄 调试输出: {debug_file}')
                    # 输出前1000字符供终端查看
                    if '--debug' in sys.argv:
                        print(f'  📄 前1000字符: {repr(html[:1000])}')
            else:
                print(f'  ⚠️ chromium dump-dom返回空或过短({len(html) if html else 0}字符)')
        except subprocess.TimeoutExpired:
            print('  ⚠️ chromium dump-dom超时(30s)')
        except Exception as e:
            print(f'  ⚠️ chromium dump-dom失败: {e}')
    
    # --- 方案2: Selenium (需要chromedriver) ---
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        
        print(f'  [Selenium] 加载{type_name}页面...')
        opts = Options()
        opts.add_argument('--headless')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--window-size=412,915')
        opts.add_argument('--user-agent=Mozilla/5.0 (Linux; Android 11; Pixel 5) '
                          'AppleWebKit/537.36 Chrome/90.0.4430.91 Mobile Safari/537.36')
        opts.add_argument('--lang=zh-CN')
        
        if chromium_path:
            opts.binary_location = chromium_path
        
        try:
            driver = webdriver.Chrome(options=opts)
        except Exception:
            try:
                service = Service(shutil.which('chromedriver') or '/usr/bin/chromedriver')
                driver = webdriver.Chrome(service=service, options=opts)
            except Exception as e2:
                print(f'  ❌ Selenium启动失败: {e2}')
                driver = None
        
        if driver:
            try:
                driver.get(url)
                time.sleep(3)
                for _ in range(5):
                    driver.execute_script('window.scrollBy(0, 500)')
                    time.sleep(0.5)
                body_text = driver.find_element('tag name', 'body').text
                driver.quit()
                results = parse_jz_results(body_text)
                if results:
                    print(f'  ✅ Selenium {type_name}完场: {len(results)} 场')
                    return results
                results = _fallback_parse(body_text, page_type)
                print(f'  ✅ Selenium {type_name}完场: {len(results)} 场')
                return results
            except Exception as e:
                print(f'  ❌ Selenium执行失败: {e}')
                try:
                    driver.quit()
                except:
                    pass
    except ImportError:
        print('  ⚠️ 未安装selenium，跳过...')
    
    # --- 方案3: Playwright ---
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('  ❌ chromium/selenium/playwright均不可用')
        print('     Termux: pkg install chromium (已安装即可)')
        print('     Selenium: pip install selenium + chromedriver')
        print('     Playwright: pip install playwright && playwright install chromium')
        return []
    
    print(f'  [Playwright] 加载{type_name}页面...')
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
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        for _ in range(5):
            page.evaluate('window.scrollBy(0, 500)')
            time.sleep(0.5)
        body_text = page.evaluate('document.body.innerText')
        results = parse_jz_results(body_text)
        if not results:
            results = _fallback_parse(body_text, page_type)
        browser.close()
    
    print(f'  ✅ Playwright {type_name}完场: {len(results)} 场')
    return results


def _fallback_parse(body_text: str, page_type: str = PAGE_JZ) -> List[Dict]:
    """innerText格式差异时的简单正则兜底解析"""
    results = []
    lines = body_text.split('\n')
    for i, line in enumerate(lines):
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
            'source': f'zgzcw_{page_type}_browser',
        })
    
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
