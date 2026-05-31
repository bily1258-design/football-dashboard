#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
500.com + 中国足彩网赛果拉取 - Termux本地版

数据源:
  1. 500.com (默认): 竞彩开奖 + 完场比分 + 北单
     - 依赖: 标准库，无需pip install
  2. 中国足彩网 (--zgzcw): Playwright DOM提取，绕过API加密
     - 依赖: playwright (pip install playwright && playwright install chromium)

用法:
  python fetch_500com_termux.py                    # 昨天，500.com
  python fetch_500com_termux.py --date 2026-05-27  # 指定日期
  python fetch_500com_termux.py --load 20260527    # 读缓存
  python fetch_500com_termux.py --zgzcw            # 改用中国足彩网
  python fetch_500com_termux.py --zgzcw --date 2026-05-27  # 足彩网+指定日期
"""

import os
import re
import json
import time
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ====== 路径配置 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构
_REPO_DIR = os.path.dirname(SCRIPT_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    DATA_BASE_DIR = _REPO_DIR
else:
    DATA_BASE_DIR = SCRIPT_DIR
CACHE_DIR = os.path.join(DATA_BASE_DIR, "data", "cache")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://live.500.com/',
}


def fetch_page(url: str, encoding: str = 'utf-8') -> Optional[str]:
    """获取页面内容"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                print(f"  ❌ {url} -> HTTP {resp.status}")
                return None
            raw = resp.read()
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                text = raw.decode('gbk', errors='replace')
            return text
    except Exception as e:
        print(f"  ❌ {url} -> {e}")
        return None


# ========== 500.com 赛果 (标准库，无依赖) ==========

# --- 竞彩开奖 ---
def parse_jingcai_results(html: str) -> List[Dict]:
    results = []
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    for tr in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 8:
            continue
        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        if not re.match(r'周[一二三四五六日]\d{3}', clean_tds[0]):
            continue
        match_id = clean_tds[0]
        league = clean_tds[1]
        kickoff = clean_tds[2]
        home_raw = clean_tds[3]
        handicap = clean_tds[4]
        away_raw = clean_tds[5]
        score = clean_tds[6]
        result = clean_tds[7]
        bonus = clean_tds[9].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 9 else ''
        odds_w = clean_tds[11].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 11 else ''
        odds_d = clean_tds[12].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 12 else ''
        odds_l = clean_tds[13].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 13 else ''
        home = re.sub(r'^\d+', '', home_raw).strip()
        away = re.sub(r'\d+$', '', away_raw).strip()
        if not score or not re.match(r'\d+:\d+', score):
            continue
        score_parts = score.split(':')
        h_score = int(score_parts[0])
        a_score = int(score_parts[1])
        try:
            handicap_val = int(handicap)
        except (ValueError, TypeError):
            handicap_val = 0
        results.append({
            'source': 'jingcai_kaijiang',
            'match_id': match_id,
            'league': league,
            'kickoff': kickoff,
            'home': home,
            'away': away,
            'handicap': handicap_val,
            'score': score.replace(':', '-'),
            'home_score': h_score,
            'away_score': a_score,
            'result_hcap': result,
            'bonus': bonus,
            'odds_w': odds_w,
            'odds_d': odds_d,
            'odds_l': odds_l,
        })
    return results


def fetch_jingcai_results(date_str: str) -> List[Dict]:
    mmdd = date_str[5:7] + '-' + date_str[8:10]
    url = f'http://zx.500.com/jczq/kaijiang.php?playid=1&d={mmdd}'
    print(f"  竞彩开奖: {url}")
    html = fetch_page(url, encoding='gb2312')
    if not html:
        return []
    results = parse_jingcai_results(html)
    print(f"    -> {len(results)} 场竞彩赛果")
    return results


# --- 完场比分 ---
def parse_wanchang_results(html: str) -> List[Dict]:
    results = []
    table_match = re.search(r'<table[^>]*id="table_match"[^>]*>(.*?)</table>', html, re.S)
    if not table_match:
        print("    ⚠️ 未找到table_match")
        return results
    content = table_match.group(1)
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.S)
    for tr in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 8:
            continue
        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        if clean_tds[0] in ('赛事', '场次'):
            continue
        status = clean_tds[3] if len(clean_tds) > 3 else ''
        if status != '完':
            continue
        league = clean_tds[0]
        round_info = clean_tds[1] if len(clean_tds) > 1 else ''
        kickoff = clean_tds[2] if len(clean_tds) > 2 else ''
        home_raw = clean_tds[4] if len(clean_tds) > 4 else ''
        score_raw = clean_tds[5] if len(clean_tds) > 5 else ''
        away_raw = clean_tds[6] if len(clean_tds) > 6 else ''
        half_raw = clean_tds[7] if len(clean_tds) > 7 else ''
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        m = re.match(r'^(\d+).*?(\d+)$', score_raw.replace(' ', ''))
        if m:
            h_score = int(m.group(1))
            a_score = int(m.group(2))
            score = f"{h_score}-{a_score}"
            outcome = '主胜' if h_score > a_score else ('平局' if h_score == a_score else '客胜')
            half_score = ''
            hm = re.match(r'(\d+)\s*-\s*(\d+)', half_raw)
            if hm:
                half_score = f"{hm.group(1)}-{hm.group(2)}"
            results.append({
                'source': 'wanchang',
                'league': league,
                'round': round_info,
                'kickoff': kickoff,
                'home': home,
                'away': away,
                'score': score,
                'home_score': h_score,
                'away_score': a_score,
                'outcome': outcome,
                'half_score': half_score,
            })
    return results


def fetch_wanchang_results() -> List[Dict]:
    url = 'https://live.500.com/wanchang.php'
    print(f"  完场比分: {url}")
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    results = parse_wanchang_results(html)
    print(f"    -> {len(results)} 场完场赛果")
    return results


# --- 北单赛事 ---
def parse_beidan_results(html: str) -> List[Dict]:
    results = []
    table_match = re.search(r'<table[^>]*id="table_match"[^>]*>(.*?)</table>', html, re.S)
    if not table_match:
        print("    ⚠️ 未找到table_match")
        return results
    content = table_match.group(1)
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.S)
    for tr in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 8:
            continue
        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        if clean_tds[0] in ('场次', '赛事'):
            continue
        try:
            match_num = int(clean_tds[0])
        except (ValueError, TypeError):
            continue
        league = clean_tds[1] if len(clean_tds) > 1 else ''
        round_info = clean_tds[2] if len(clean_tds) > 2 else ''
        kickoff = clean_tds[3] if len(clean_tds) > 3 else ''
        status = clean_tds[4] if len(clean_tds) > 4 else ''
        home_raw = clean_tds[5] if len(clean_tds) > 5 else ''
        score_raw = clean_tds[6] if len(clean_tds) > 6 else ''
        away_raw = clean_tds[7] if len(clean_tds) > 7 else ''
        sp_raw = clean_tds[9] if len(clean_tds) > 9 else ''
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        home_clean = re.sub(r'\([+-]?\d+\)', '', home).strip()
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        hcap_match = re.search(r'\(([+-]?\d+)\)', home_raw)
        handicap = int(hcap_match.group(1)) if hcap_match else 0
        h_score = None
        a_score = None
        if status == '完':
            m = re.match(r'^(\d+).*?(\d+)$', score_raw.replace(' ', ''))
            if m:
                h_score = int(m.group(1))
                a_score = int(m.group(2))
        sp_values = []
        if sp_raw:
            sp_nums = re.findall(r'\d+\.\d{2}', sp_raw)
            sp_values = [float(x) for x in sp_nums[:3]]
        entry = {
            'source': 'beidan',
            'match_num': match_num,
            'league': league,
            'round': round_info,
            'kickoff': kickoff,
            'status': status,
            'home': home_clean,
            'away': away,
            'handicap': handicap,
            'sp_values': sp_values,
        }
        if h_score is not None and a_score is not None:
            entry['score'] = f"{h_score}-{a_score}"
            entry['home_score'] = h_score
            entry['away_score'] = a_score
            adjusted_h = h_score + handicap
            entry['outcome_hcap'] = '胜' if adjusted_h > a_score else ('平' if adjusted_h == a_score else '负')
            entry['outcome'] = '主胜' if h_score > a_score else ('平局' if h_score == a_score else '客胜')
        results.append(entry)
    return results


def fetch_beidan_results() -> List[Dict]:
    url = 'https://live.500.com/zqdc.php'
    print(f"  北单赛事: {url}")
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    results = parse_beidan_results(html)
    completed = [r for r in results if r.get('status') == '完']
    print(f"    -> {len(results)} 场赛事（{len(completed)} 场完赛）")
    return results


# ========== 中国足彩网赛果 (Playwright DOM提取) ==========

def _check_playwright() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        print("[ERROR] 未安装playwright，请先：pip install playwright && playwright install chromium")
        return False


def _extract_zgzcw_dom(page) -> List[Dict]:
    """从渲染后的DOM提取赛果（绕过API加密）"""
    js_code = """
    () => {
        const results = [];
        const lines = document.body.innerText.split('\\n');
        
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            // 匹配比分行: "队名 数字-数字 队名"
            const scoreMatch = line.match(/([\\S\\u4e00-\\u9fa5·.]+?)\\s{1,4}(\\d+)\\s*[-–]\\s*(\\d+)\\s{1,4}([\\S\\u4e00-\\u9fa5·.]+)/);
            if (!scoreMatch || !scoreMatch[1] || !scoreMatch[4]) continue;
            
            const [, home, hs, as_, away] = scoreMatch;
            const home_score = parseInt(hs);
            const away_score = parseInt(as_);
            if (home_score > 20 || away_score > 20) continue;
            
            let league = '未知', time_str = '00:00', half_h = 0, half_a = 0;
            
            // 往前找联赛名和时间
            for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
                const prev = lines[j].trim();
                if (/\\d{2}:\\d{2}/.test(prev)) {
                    time_str = prev.match(/\\d{2}:\\d{2}/)[0];
                } else if (prev.length > 0 && prev.length < 15 && !/\\d+-\\d+/.test(prev)) {
                    league = prev;
                }
                const halfMatch = prev.match(/(\\d+)@(\\d+)/) || (prev.match(/(\\d+)-(\\d+)/) && j < i - 1 ? prev.match(/(\\d+)-(\\d+)/) : null);
                if (halfMatch && j < i - 1) {
                    half_h = parseInt(halfMatch[1]);
                    half_a = parseInt(halfMatch[2]);
                }
            }
            
            // 判断状态
            let status = '完';
            for (let k = i; k < Math.min(lines.length, i + 3); k++) {
                if (lines[k].includes('中')) { status = '中'; break; }
                if (lines[k].includes('未')) { status = '未'; break; }
                if (lines[k].includes('完')) { status = '完'; break; }
            }
            
            results.push({
                home_team: home.replace(/\\s+/g, '').trim(),
                away_team: away.replace(/\\s+/g, '').trim(),
                home_score,
                away_score,
                league_name: league,
                match_time: time_str,
                half_score_home: half_h,
                half_score_away: half_a,
                status,
            });
        }
        
        // 去重
        const seen = new Set();
        return results.filter(m => {
            const key = `${m.home_team}|${m.away_team}|${m.home_score}-${m.away_score}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }
    """
    try:
        result = page.evaluate(js_code)
        return result if result else []
    except Exception as e:
        print(f"  [提取] 失败: {e}")
        return []


def fetch_zgzcw_results(date_str: str = None, debug: bool = False) -> Dict:
    """用Playwright从中国足彩网抓赛果（绕过API加密）"""
    if not _check_playwright():
        return {}
    
    from playwright.sync_api import sync_playwright
    
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    date_short = date_str[5:].replace('-', '/')
    
    print(f"\n{'='*50}")
    print(f"中国足彩网赛果: {date_str}")
    print(f"{'='*50}")
    
    results = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'zgzcw_dom',
        'total': 0,
        'matches': []
    }
    
    with sync_playwright() as p:
        iphone = p.devices['iPhone 14']
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=iphone['user_agent'],
            viewport=iphone['viewport'],
            locale='zh-CN',
        )
        page = context.new_page()
        
        print("  [加载] 打开中国足彩网...")
        page.goto(
            "https://t.zgzcw.com/module/hdH5/h5/index.html#/pages/match/index",
            wait_until='domcontentloaded',
            timeout=30000
        )
        time.sleep(3)
        
        # 点击"完场"标签
        try:
            click_result = page.evaluate("""
            () => {
                const labels = [...document.querySelectorAll('*')].filter(el => 
                    el.childNodes.length === 1 && el.textContent.trim() === '完场'
                );
                if (labels.length > 0) { labels[0].click(); return 'clicked 完场'; }
                const els = [...document.querySelectorAll('span,div,li,a,button')].filter(el => 
                    el.textContent.includes('完') && el.textContent.trim().length < 5
                );
                if (els.length > 0) { els[0].click(); return 'clicked fallback'; }
                return 'not found';
            }
            """)
            print(f"  [完场标签] {click_result}")
            time.sleep(2)
        except Exception as e:
            print(f"  [完场标签] {e}")
        
        # 点击目标日期
        try:
            date_result = page.evaluate(f"""
            () => {{
                const targets = [...document.querySelectorAll('*')].filter(el => 
                    el.textContent.includes('{date_short}') && el.textContent.trim().length < 8
                );
                if (targets.length > 0) {{ targets[0].click(); return 'clicked {date_short}'; }}
                return 'date not found';
            }}
            """)
            print(f"  [日期] {date_result}")
            time.sleep(2)
        except Exception as e:
            print(f"  [日期] {e}")
        
        # 滚动加载
        for _ in range(5):
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(0.5)
        
        # 调试HTML
        if debug:
            os.makedirs(CACHE_DIR, exist_ok=True)
            html_path = os.path.join(CACHE_DIR, f"zgzcw_debug_{date_str.replace('-','')}.html")
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(page.content())
            print(f"  [调试] HTML -> {html_path}")
        
        matches = _extract_zgzcw_dom(page)
        results['matches'] = matches
        results['total'] = len(matches)
        
        browser.close()
    
    # 保存
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"zgzcw_results_{date_str.replace('-','')}.json")
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n✅ 完成: {results['total']}场 | {cache_file.split('/')[-1]}")
    for m in results['matches'][:5]:
        print(f"  {m['league_name']} | {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}")
    
    return results


# ========== 主函数 ==========

def fetch_all_results(date_str: str = None, use_zgzcw: bool = False, debug: bool = False) -> Dict:
    if use_zgzcw:
        return fetch_zgzcw_results(date_str, debug=debug)
    
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"500.com赛果拉取: {date_str}")
    data = {
        "date": date_str,
        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "500com",
        "jingcai": [],
        "wanchang": [],
        "beidan": [],
    }
    try:
        data["jingcai"] = fetch_jingcai_results(date_str)
    except Exception as e:
        print(f"  ❌ 竞彩获取失败: {e}")
    try:
        data["wanchang"] = fetch_wanchang_results()
    except Exception as e:
        print(f"  ❌ 完场获取失败: {e}")
    try:
        data["beidan"] = fetch_beidan_results()
    except Exception as e:
        print(f"  ❌ 北单获取失败: {e}")
    
    jc_count = len(data["jingcai"])
    wc_count = len(data["wanchang"])
    bd_all = len(data["beidan"])
    bd_done = len([r for r in data["beidan"] if r.get("status") == "完"])
    data["summary"] = {
        "jingcai_completed": jc_count,
        "wanchang_total": wc_count,
        "beidan_total": bd_all,
        "beidan_completed": bd_done,
    }
    
    os.makedirs(CACHE_DIR, exist_ok=True)
    date_code = date_str.replace('-', '')
    output_path = os.path.join(CACHE_DIR, f"500com_results_{date_code}.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 保存: {output_path}")
    print(f"   竞彩: {jc_count}场 | 完场: {wc_count}场 | 北单: {bd_done}/{bd_all}场完赛")
    return data


def load_results(source: str, date_str: str) -> Optional[Dict]:
    """加载缓存
    source: '500com' 或 'zgzcw'
    date_str: YYYYMMDD 或 YYYY-MM-DD
    """
    date_code = date_str.replace('-', '')
    if source == 'zgzcw':
        filepath = os.path.join(CACHE_DIR, f"zgzcw_results_{date_code}.json")
    else:
        filepath = os.path.join(CACHE_DIR, f"500com_results_{date_code}.json")
    
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="赛果拉取 (500.com / 中国足彩网)")
    parser.add_argument('--date', help='目标日期 YYYY-MM-DD，默认昨天')
    parser.add_argument('--load', help='加载缓存(YYYYMMDD或YYYY-MM-DD)')
    parser.add_argument('--zgzcw', action='store_true', help='使用中国足彩网(Playwright方案)')
    parser.add_argument('--debug', action='store_true', help='保存HTML调试文件')
    args = parser.parse_args()
    
    if args.load:
        # 自动判断数据源
        data = load_results('zgzcw', args.load) or load_results('500com', args.load)
        if data:
            src = data.get('source', '?')
            print(f"📅 {data['date']} [{src}] 抓取: {data['fetch_time']}")
            if src == 'zgzcw_dom':
                print(f"   共 {data['total']} 场完场赛果")
                for m in data['matches'][:10]:
                    print(f"   {m['league_name']} | {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}")
            else:
                s = data.get('summary', {})
                print(f"   竞彩: {s.get('jingcai_completed',0)}场 | 完场: {s.get('wanchang_total',0)}场 | 北单: {s.get('beidan_completed',0)}/{s.get('beidan_total',0)}场完赛")
        else:
            print("未找到缓存")
    else:
        fetch_all_results(date_str=args.date, use_zgzcw=args.zgzcw, debug=args.debug)
