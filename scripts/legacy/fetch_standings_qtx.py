#!/usr/bin/env python3
"""
球天下(data.qtx.com)积分榜抓取模块
支持五大联赛、次级联赛、北欧联赛等
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import re

# 缓存配置
CACHE_DIR = 'data/cache'
CACHE_TTL = 6 * 3600  # 6小时缓存

# 联赛名称 → URL code 映射表
LEAGUE_CODE_MAP = {
    # 五大联赛
    "英超": "Jnlj4Y2624_GKWbdBLjAv.html",
    "英格兰超级联赛": "Jnlj4Y2624_GKWbdBLjAv.html",
    "西甲": "N2O6Kmy7eg_bD7zlqG68o.html",
    "西班牙甲级联赛": "N2O6Kmy7eg_bD7zlqG68o.html",
    "意甲": "dqrWXvkjel_nlj4Qb2624.html",
    "意大利甲级联赛": "dqrWXvkjel_nlj4Qb2624.html",
    "德甲": "EBgjm3RWbR_Dlpjr4W8w9.html",
    "德国甲级联赛": "EBgjm3RWbR_Dlpjr4W8w9.html",
    "法甲": "lGKWb946Av_547wXMXjK8.html",
    "法国甲级联赛": "lGKWb946Av_547wXMXjK8.html",
    
    # 次级联赛
    "英冠": "XOW1pZr6yb_Dp6xRqA7gw.html",
    "英格兰冠军联赛": "XOW1pZr6yb_Dp6xRqA7gw.html",
    "西乙": "Ew7NkExjJ9_Dp6xRZL7gw.html",
    "西班牙乙级联赛": "Ew7NkExjJ9_Dp6xRZL7gw.html",
    "德乙": "GKWbdBLjAv_lR7g9gB6GP.html",
    "德国乙级联赛": "GKWbdBLjAv_lR7g9gB6GP.html",
    "意乙": "dJjn3dV6mq_Dp6xRqA7gw.html",
    "意大利乙级联赛": "dJjn3dV6mq_Dp6xRqA7gw.html",
    "法乙": "lpjr9az78w_lR7g9gB6GP.html",
    "法国乙级联赛": "lpjr9az78w_lR7g9gB6GP.html",
    
    # 北欧联赛
    "芬超": "5Xj2qwe7dQ_ry7Boy3jzO.html",
    "芬兰超级联赛": "5Xj2qwe7dQ_ry7Boy3jzO.html",
    "芬甲": "5Xj2qwe7dQ_nA6qXnkj5p.html",
    "芬兰甲级联赛": "5Xj2qwe7dQ_nA6qXnkj5p.html",
    "瑞典超": "5zWel3A629_qDZjMMxjVP.html",
    "瑞典超级联赛": "5zWel3A629_qDZjMMxjVP.html",
    "瑞典超甲": "5zWel3A629_Ew7NkYajJ9.html",
    "瑞典甲级联赛": "5zWel3A629_Ew7NkYajJ9.html",
    "挪超": "Qe7348AjBK_5zWel3A629.html",
    "挪威超级联赛": "Qe7348AjBK_5zWel3A629.html",
    "挪甲": "Qe7348AjBK_Ew7Nk8ajPG.html",
    "挪威甲级联赛": "Qe7348AjBK_Ew7Nk8ajPG.html",
    "丹超": "GojExeY7q0_Xn6JKzlW2B.html",
    "丹麦超级联赛": "GojExeY7q0_Xn6JKzlW2B.html",
    "冰岛超": "KljDPnLjeD_Bgjm3dAWbR.html",
    "冰岛超级联赛": "KljDPnLjeD_Bgjm3dAWbR.html",
    
    # 其他联赛
    "葡超": "g8dWaXD6kv_KljDoyL7eD.html",
    "葡萄牙超级联赛": "g8dWaXD6kv_KljDoyL7eD.html",
    "荷甲": "zaWpaL27wl_Mz6Zq38j2L.html",
    "荷兰甲级联赛": "zaWpaL27wl_Mz6Zq38j2L.html",
    "荷乙": "zaWpaL27wl_KX7QGok6PG.html",
    "荷兰乙级联赛": "zaWpaL27wl_KX7QGok6PG.html",
    "比甲": "2O6Kd2yWeg_KX7QGok6PG.html",
    "比利时甲级联赛": "2O6Kd2yWeg_KX7QGok6PG.html",
    "奥甲": "KljDPnLjeD_Xn6J1KGj2B.html",
    "奥地利甲级联赛": "KljDPnLjeD_Xn6J1KGj2B.html",
    "瑞士超": "5zWel9z629_Xn6JKzlW2B.html",
    "瑞士超级联赛": "5zWel9z629_Xn6JKzlW2B.html",
    "捷甲": "GojExeY7q0_mV6o3Q06GR.html",
    "捷克甲级联赛": "GojExeY7q0_mV6o3Q06GR.html",
    "波兰甲": "dJjngna6mq_Dp6xRZL7gw.html",
    "波兰甲级联赛": "dJjngna6mq_Dp6xRZL7gw.html",
    "韩K联": "2O6Kd2yWeg_ry7Boy3jzO.html",
    "韩国职业联赛": "2O6Kd2yWeg_ry7Boy3jzO.html",
    "日职联": "zaWpag27wl_ry7Boy3jzO.html",
    "日本职业联赛": "zaWpag27wl_ry7Boy3jzO.html",
    "日职乙": "zaWpag27wl_29W8XekWvb.html",
    "日本乙级联赛": "zaWpag27wl_29W8XekWvb.html",
    "澳超": "KajOr0k7G0_547w8GMjK8.html",
    "澳大利亚超级联赛": "KajOr0k7G0_547w8GMjK8.html",
    "美职联": "8dWaeR3Wkv_GojEOgVWq0.html",
    "美国职业足球大联盟": "8dWaeR3Wkv_GojEOgVWq0.html",
    "沙特联": "BoXjPAa6vP_547wXNQjK8.html",
    "沙特职业联赛": "BoXjPAa6vP_547wXNQjK8.html",
    "沙职": "BoXjPAa6vP_547wXNQjK8.html",
    
    # 杯赛
    "欧冠": "Jnlj4Y2624_8dWapaQ6kv.html",
    "欧联": "Jnlj4Y2624_KX7QGok6PG.html",
    "欧协联": "Jnlj4Y2624_2O6Kd33Weg.html",
}

# 队名别名映射（统一名称格式）
TEAM_ALIAS_MAP = {
    # 英超
    "曼彻斯特城": "曼城",
    "曼彻斯特联": "曼联",
    "托特纳姆热刺": "热刺",
    "阿斯顿维拉": "维拉",
    "纽卡斯尔联": "纽卡斯尔",
    "西汉姆联": "西汉姆",
    "布伦特福德": "布伦特",
    "诺丁汉森林": "森林",
    "莱斯特城": "莱斯特",
    "狼队": "狼",
    
    # 西甲
    "皇家马德里": "皇马",
    "巴塞罗那": "巴萨",
    "马德里竞技": "马竞",
    "塞维利亚": "塞维",
    "毕尔巴鄂竞技": "毕尔巴鄂",
    "皇家社会": "皇社",
    "比利亚雷亚尔": "黄潜",
    
    # 意甲
    "国际米兰": "国米",
    "尤文图斯": "尤文",
    "AC米兰": "AC米兰",
    "那不勒斯": "那不勒斯",
    "罗马": "罗马",
    "佛罗伦萨": "佛罗伦萨",
    "都灵": "都灵",
    "亚特兰大": "亚特兰大",
    
    # 德甲
    "拜仁慕尼黑": "拜仁",
    "多特蒙德": "多特蒙德",
    "RB莱比锡": "莱比锡",
    "勒沃库森": "勒沃库森",
    "门兴格拉德巴赫": "门兴",
    "沃尔夫斯堡": "沃尔夫斯堡",
    "法兰克福": "法兰克福",
    "霍芬海姆": "霍芬海姆",
    "斯图加特": "斯图加特",
    "弗赖堡": "弗赖堡",
    "云达不莱梅": "不莱梅",
    "柏林联合": "柏林联合",
    "美因茨": "美因茨",
    "奥格斯堡": "奥格斯堡",
    "海登海姆": "海登海姆",
    "圣保利": "圣保利",
    
    # 法甲
    "巴黎圣日耳曼": "巴黎",
    "里昂": "里昂",
    "马赛": "马赛",
    "摩纳哥": "摩纳哥",
    "里尔": "里尔",
    "朗斯": "朗斯",
}


def get_cache_path(league_name: str) -> str:
    """获取联赛缓存文件路径"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe_name = league_name.replace('/', '_').replace(' ', '_')
    return os.path.join(CACHE_DIR, f"qtx_standings_{safe_name}.json")


def is_cache_valid(cache_path: str) -> bool:
    """检查缓存是否有效"""
    if not os.path.exists(cache_path):
        return False
    mtime = os.path.getmtime(cache_path)
    return (time.time() - mtime) < CACHE_TTL


def fetch_standings(league_name: str, force_refresh: bool = False) -> Optional[Dict]:
    """
    获取指定联赛的积分榜数据
    
    Args:
        league_name: 联赛中文名称
        force_refresh: 是否强制刷新缓存
    
    Returns:
        积分榜数据字典，包含teams列表
        每个team包含: name, rank, played, won, drawn, lost, gf, ga, gd, avg_gf, avg_ga, points
        找不到时返回None
    """
    # 获取URL code
    url_code = LEAGUE_CODE_MAP.get(league_name)
    if not url_code:
        # 尝试模糊匹配
        for key in LEAGUE_CODE_MAP.keys():
            if league_name in key or key in league_name:
                url_code = LEAGUE_CODE_MAP[key]
                break
    
    if not url_code:
        return None
    
    # 检查缓存
    cache_path = get_cache_path(league_name)
    if not force_refresh and is_cache_valid(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    # 抓取数据
    url = f"https://data.qtx.com/jifenbang/{url_code}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://data.qtx.com/',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 查找积分榜表格
        teams = []
        
        # 尝试查找表格
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 10:
                    continue
                
                try:
                    # 解析数据
                    rank_text = cells[0].get_text(strip=True)
                    if not rank_text or not rank_text.isdigit():
                        continue
                    rank = int(rank_text)
                    
                    # 队名（可能在链接中）
                    team_link = cells[1].find('a')
                    if team_link:
                        team_name = team_link.get_text(strip=True)
                    else:
                        team_name = cells[1].get_text(strip=True)
                    
                    # 统一队名
                    team_name = normalize_team_name(team_name)
                    
                    # 场次、胜平负
                    played = int(cells[2].get_text(strip=True))
                    won = int(cells[3].get_text(strip=True))
                    drawn = int(cells[4].get_text(strip=True))
                    lost = int(cells[5].get_text(strip=True))
                    
                    # 进球、失球、净胜球
                    gf = int(cells[6].get_text(strip=True))
                    ga = int(cells[7].get_text(strip=True))
                    gd_text = cells[8].get_text(strip=True)
                    gd = int(gd_text) if gd_text.lstrip('-').isdigit() else 0
                    
                    # 场均进球、场均失球
                    avg_gf_text = cells[9].get_text(strip=True) if len(cells) > 9 else "0"
                    avg_ga_text = cells[10].get_text(strip=True) if len(cells) > 10 else "0"
                    
                    try:
                        avg_gf = float(avg_gf_text)
                    except:
                        avg_gf = round(gf / played, 2) if played > 0 else 0
                    
                    try:
                        avg_ga = float(avg_ga_text)
                    except:
                        avg_ga = round(ga / played, 2) if played > 0 else 0
                    
                    # 积分
                    points = int(cells[11].get_text(strip=True)) if len(cells) > 11 else 0
                    
                    teams.append({
                        'name': team_name,
                        'rank': rank,
                        'played': played,
                        'won': won,
                        'drawn': drawn,
                        'lost': lost,
                        'gf': gf,
                        'ga': ga,
                        'gd': gd,
                        'avg_gf': avg_gf,
                        'avg_ga': avg_ga,
                        'points': points,
                    })
                except Exception as e:
                    continue
        
        if not teams:
            return None
        
        result = {
            'league': league_name,
            'updated_at': datetime.now().isoformat(),
            'teams': teams,
        }
        
        # 保存缓存
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        return result
        
    except Exception as e:
        print(f"抓取{league_name}积分榜失败: {e}")
        return None


def normalize_team_name(name: str) -> str:
    """统一队名格式"""
    # 先查别名表
    if name in TEAM_ALIAS_MAP:
        return TEAM_ALIAS_MAP[name]
    
    # 通用处理：去掉"足球俱乐部"等后缀
    for suffix in ['足球俱乐部', 'FC', '足球', '体育']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    
    return name.strip()


def get_team_stats(standings: Dict, team_name: str) -> Optional[Dict]:
    """
    从积分榜中查找特定球队的数据
    
    Args:
        standings: 积分榜数据
        team_name: 球队名称
    
    Returns:
        球队统计数据，找不到返回None
    """
    if not standings or 'teams' not in standings:
        return None
    
    # 统一队名
    normalized_name = normalize_team_name(team_name)
    
    for team in standings['teams']:
        # 直接匹配
        if team['name'] == normalized_name:
            return team
        
        # 模糊匹配（包含关系）
        if normalized_name in team['name'] or team['name'] in normalized_name:
            return team
        
        # 简化名称匹配
        short_name = normalized_name[:4] if len(normalized_name) > 4 else normalized_name
        if short_name in team['name']:
            return team
    
    return None


def get_match_standings_data(home_team: str, away_team: str, league: str) -> Dict:
    """
    获取比赛的积分榜数据
    
    Args:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
    
    Returns:
        包含主客队积分榜数据的字典
        - home_stats: 主队数据（包含points, avg_gf, avg_ga, rank等）
        - away_stats: 客队数据
        - success: 是否成功获取
    """
    result = {
        'home_points': 0,
        'away_points': 0,
        'home_avg_goals': 0,
        'away_avg_goals': 0,
        'home_avg_conceded': 0,
        'away_avg_conceded': 0,
        'success': False,
    }
    
    standings = fetch_standings(league)
    if not standings:
        return result
    
    home_stats = get_team_stats(standings, home_team)
    if home_stats:
        result['home_points'] = home_stats.get('points', 0)
        result['home_avg_goals'] = home_stats.get('avg_gf', 0)
        result['home_avg_conceded'] = home_stats.get('avg_ga', 0)
        result['success'] = True
    
    away_stats = get_team_stats(standings, away_team)
    if away_stats:
        result['away_points'] = away_stats.get('points', 0)
        result['away_avg_goals'] = away_stats.get('avg_gf', 0)
        result['away_avg_conceded'] = away_stats.get('avg_ga', 0)
        result['success'] = True
    
    return result


# 测试函数
if __name__ == '__main__':
    # 测试英超
    print("测试英超积分榜...")
    standings = fetch_standings("英超")
    if standings:
        print(f"✓ 获取成功，共{len(standings['teams'])}支球队")
        print(f"前3名: {[t['name'] for t in standings['teams'][:3]]}")
        
        # 测试球队查找
        stats = get_team_stats(standings, "曼城")
        if stats:
            print(f"曼城: 排名{stats['rank']}, 积分{stats['points']}, 场均进球{stats['avg_gf']}")
    else:
        print("✗ 获取失败")
    
    # 测试德甲
    print("\n测试德甲积分榜...")
    standings = fetch_standings("德甲")
    if standings:
        print(f"✓ 获取成功，共{len(standings['teams'])}支球队")
        stats = get_team_stats(standings, "拜仁")
        if stats:
            print(f"拜仁: 排名{stats['rank']}, 积分{stats['points']}")
    
    # 测试匹配数据
    print("\n测试比赛数据获取...")
    data = get_match_standings_data("曼城", "阿森纳", "英超")
    print(f"主队(曼城): 积分{data['home_points']}, 场均进球{data['home_avg_goals']}")
    print(f"客队(阿森纳): 积分{data['away_points']}, 场均进球{data['away_avg_goals']}")
