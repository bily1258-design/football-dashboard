#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
500.com赛果拉取脚本（只抓赛果，不抓赔率）

【重要】数据源说明：
- 本脚本数据来自【500.com】，只抓比赛结果/比分，不抓赔率
- 赔率数据来自【中国足彩网 zgzcw.com】，见 fetch_pinnacle_odds.py
- 中国竞彩网(sporttery.cn)是体彩官方平台，本脚本未使用

数据源：
  1. zx.500.com/jczq/kaijiang.php - 竞彩开奖结果（含让球+赔率）
  2. live.500.com/wanchang.php - 全部完场比分
  3. live.500.com/zqdc.php - 北单当前期赛事（含SP值）

输出：data/cache/500com_results_{date}.json
供 jingcai_review.py 和 beidan_review.py 读取
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构
_REPO_DIR = os.path.dirname(WORK_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    DATA_BASE_DIR = _REPO_DIR
else:
    DATA_BASE_DIR = WORK_DIR
CACHE_DIR = os.path.join(DATA_BASE_DIR, "data", "cache")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def fetch_page(url: str, encoding: str = 'utf-8') -> Optional[str]:
    """获取页面内容"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  ❌ {url} -> HTTP {resp.status_code}")
            return None
        try:
            text = resp.content.decode(encoding)
        except UnicodeDecodeError:
            # 尝试gbk
            text = resp.content.decode('gbk', errors='replace')
        return text
    except Exception as e:
        print(f"  ❌ {url} -> {e}")
        return None


# ========== 1. 竞彩开奖 ==========

def parse_jingcai_results(html: str) -> List[Dict]:
    """
    解析竞彩开奖页面
    返回: [{match_id, league, kickoff, home, away, handicap, score, result, bonus, odds_w, odds_d, odds_l}]
    """
    results = []
    
    # 竞彩页面是gb2312编码
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    
    for tr in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 8:
            continue
        
        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        
        # 检查是否是数据行（match_id格式如"周四005"）
        if not re.match(r'周[一二三四五六日]\d{3}', clean_tds[0]):
            continue
        
        match_id = clean_tds[0]  # 周四005
        league = clean_tds[1]    # 荷甲
        kickoff = clean_tds[2]   # 05-22 03:00
        
        # 主队含让球标记（如"乌德勒支"、"-1"是让球）
        home_raw = clean_tds[3]
        handicap = clean_tds[4]  # -1, +1等
        away_raw = clean_tds[5]
        score = clean_tds[6]     # 3:2
        result = clean_tds[7]    # 胜/平/负（让球后结果）
        
        # TD[8]=spacer, TD[9]=奖金, TD[10]=spacer, TD[11-13]=平均欧赔(胜/平/负), TD[14]=spacer
        bonus = clean_tds[9].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 9 else ''
        odds_w = clean_tds[11].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 11 else ''
        odds_d = clean_tds[12].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 12 else ''
        odds_l = clean_tds[13].replace('\xa0', '').replace('&nbsp;', '') if len(clean_tds) > 13 else ''
        
        # 清理队名（去掉编号标记）
        home = re.sub(r'^\d+', '', home_raw).strip()
        away = re.sub(r'\d+$', '', away_raw).strip()
        
        # 只记录有比分的完赛
        if not score or not re.match(r'\d+:\d+', score):
            continue
        
        # 比分标准化
        score_parts = score.split(':')
        h_score = int(score_parts[0])
        a_score = int(score_parts[1])
        
        # 让球数
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
            'result_hcap': result,  # 让球后结果
            'bonus': bonus,
            'odds_w': odds_w,
            'odds_d': odds_d,
            'odds_l': odds_l,
        })
    
    return results


def fetch_jingcai_results(date_str: str) -> List[Dict]:
    """获取竞彩开奖结果"""
    url = f'http://zx.500.com/jczq/kaijiang.php?playid=1&d={date_str}'
    print(f"  竞彩开奖: {url}")
    html = fetch_page(url, encoding='gb2312')
    if not html:
        return []
    results = parse_jingcai_results(html)
    print(f"    -> {len(results)} 场竞彩赛果")
    return results


# ========== 2. 完场比分 ==========

def parse_wanchang_results(html: str) -> List[Dict]:
    """
    解析完场比分页面
    TD结构: [0]联赛 [1]轮次 [2]时间 [3]状态 [4]主队 [5]让球比分 [6]客队 [7]半场 [8]直播 [9]分析
    
    TD[5]格式: "2受平手/半球1" -> 主2球, 盘口受平手/半球, 客1球
    TD[7]格式: "1 - 1" -> 半场比分
    """
    results = []
    
    # 提取table_match
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
        
        # 表头行跳过
        if clean_tds[0] in ('赛事', '场次'):
            continue
        
        # 状态检查
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
        
        # 清理队名 - 去掉排名[07]和积分数字
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        
        # 从让球比分列提取全场比分: "2受平手/半球1" -> 2:1
        h_score = None
        a_score = None
        
        # 匹配 "N盘口M" 格式，首尾数字就是全场比分
        m = re.match(r'^(\d+).*?(\d+)$', score_raw.replace(' ', ''))
        if m:
            h_score = int(m.group(1))
            a_score = int(m.group(2))
        
        if h_score is not None and a_score is not None:
            score = f"{h_score}-{a_score}"
            if h_score > a_score:
                outcome = '主胜'
            elif h_score == a_score:
                outcome = '平局'
            else:
                outcome = '客胜'
            
            # 提取半场比分
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
    """获取完场比分"""
    url = 'https://live.500.com/wanchang.php'
    print(f"  完场比分: {url}")
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    results = parse_wanchang_results(html)
    print(f"    -> {len(results)} 场完场赛果")
    return results


# ========== 3. 北单赛事 ==========

def parse_beidan_results(html: str) -> List[Dict]:
    """
    解析北单当前期赛事页面
    TD结构: [0]场次 [1]联赛 [2]轮次 [3]时间 [4]状态 [5]主队 [6]让球比分 [7]客队 [8]参考SP [9]SP值 [10]玩法 [11]直播 [12]分析 [13]置顶
    
    TD[9]格式: "2.83|2.85|3.35" -> 胜SP, 平SP, 负SP
    TD[5]含让球: "耶尔文佩(-1)" -> handicap=-1
    """
    results = []
    
    # 提取table_match
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
        
        # 表头行跳过
        if clean_tds[0] in ('场次', '赛事'):
            continue
        
        # 场次编号
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
        
        # SP值在TD[9]（参考SP值=平均欧赔）
        sp_raw = clean_tds[9] if len(clean_tds) > 9 else ''
        
        # 清理队名
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        # 去掉让球标记 (±N)
        home_clean = re.sub(r'\([+-]?\d+\)', '', home).strip()
        
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        
        # 提取让球数
        hcap_match = re.search(r'\(([+-]?\d+)\)', home_raw)
        handicap = int(hcap_match.group(1)) if hcap_match else 0
        
        # 提取比分（完赛场次）
        h_score = None
        a_score = None
        
        if status == '完':
            m = re.match(r'^(\d+).*?(\d+)$', score_raw.replace(' ', ''))
            if m:
                h_score = int(m.group(1))
                a_score = int(m.group(2))
        
        # 解析SP值（3个2位小数紧连，如"2.832.853.35" -> 2.83, 2.85, 3.35）
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
            'sp_values': sp_values,  # [胜SP, 平SP, 负SP]
        }
        
        if h_score is not None and a_score is not None:
            entry['score'] = f"{h_score}-{a_score}"
            entry['home_score'] = h_score
            entry['away_score'] = a_score
            # 让球后结果
            adjusted_h = h_score + handicap
            if adjusted_h > a_score:
                entry['outcome_hcap'] = '胜'
            elif adjusted_h == a_score:
                entry['outcome_hcap'] = '平'
            else:
                entry['outcome_hcap'] = '负'
            # 90分钟结果
            if h_score > a_score:
                entry['outcome'] = '主胜'
            elif h_score == a_score:
                entry['outcome'] = '平局'
            else:
                entry['outcome'] = '客胜'
        
        results.append(entry)
    
    return results


def fetch_beidan_results() -> List[Dict]:
    """获取北单当前期赛事"""
    url = 'https://live.500.com/zqdc.php'
    print(f"  北单赛事: {url}")
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    results = parse_beidan_results(html)
    completed = [r for r in results if r.get('status') == '完']
    print(f"    -> {len(results)} 场赛事（{len(completed)} 场完赛）")
    return results


# ========== 主函数 ==========

def fetch_all_results(date_str: str = None) -> Dict:
    """
    拉取全部500.com赛果数据
    
    Args:
        date_str: 目标日期 YYYY-MM-DD，默认昨天
    
    Returns:
        {
            "date": "2026-05-21",
            "fetch_time": "...",
            "jingcai": [...],
            "wanchang": [...],
            "beidan": [...],
            "summary": {...}
        }
    """
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    print(f"500.com赛果拉取: {date_str}")
    
    data = {
        "date": date_str,
        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "jingcai": [],
        "wanchang": [],
        "beidan": [],
    }
    
    # 1. 竞彩开奖
    try:
        data["jingcai"] = fetch_jingcai_results(date_str)
    except Exception as e:
        print(f"  ❌ 竞彩获取失败: {e}")
    
    # 2. 完场比分
    try:
        data["wanchang"] = fetch_wanchang_results()
    except Exception as e:
        print(f"  ❌ 完场获取失败: {e}")
    
    # 3. 北单赛事
    try:
        data["beidan"] = fetch_beidan_results()
    except Exception as e:
        print(f"  ❌ 北单获取失败: {e}")
    
    # 汇总
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
    
    # 保存
    os.makedirs(CACHE_DIR, exist_ok=True)
    output_path = os.path.join(CACHE_DIR, f"500com_results_{date_str.replace('-', '')}.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 保存: {output_path}")
    print(f"   竞彩: {jc_count}场 | 完场: {wc_count}场 | 北单: {bd_done}/{bd_all}场完赛")
    
    return data


def load_results(date_str: str) -> Optional[Dict]:
    """加载已缓存的赛果数据"""
    filename = f"500com_results_{date_str.replace('-', '')}.json"
    filepath = os.path.join(CACHE_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def match_score_500com(team_a: str, team_b: str, results: List[Dict], 
                        source: str = None) -> Optional[Dict]:
    """
    在500.com赛果中匹配比赛
    支持模糊匹配（队名包含关系）
    
    Args:
        team_a: 主队名
        team_b: 客队名
        results: 500.com赛果列表
        source: 限定来源 jingcai/wanchang/beidan
    
    Returns:
        匹配到的赛果Dict，或None
    """
    if source:
        results = [r for r in results if r.get('source') == source]
    
    for r in results:
        r_home = r.get('home', '')
        r_away = r.get('away', '')
        
        # 精确匹配
        if (team_a == r_home and team_b == r_away):
            return r
        
        # 包含匹配（处理缩写/全称差异）
        if ((team_a in r_home or r_home in team_a) and 
            (team_b in r_away or r_away in team_b)):
            return r
    
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="500.com赛果拉取")
    parser.add_argument('--date', help='目标日期 YYYY-MM-DD，默认昨天')
    parser.add_argument('--load', help='加载已缓存的日期')
    args = parser.parse_args()
    
    if args.load:
        data = load_results(args.load)
        if data:
            print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
        else:
            print("未找到缓存")
    else:
        fetch_all_results(args.date)
