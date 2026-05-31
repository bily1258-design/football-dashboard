#!/usr/bin/env python3
"""
竞彩足球泊松分布分析系统 - 每日日报生成（基本面增强版）
基于 football-data.org API 获取积分榜数据，使用真实赔率进行贝叶斯修正
整合基本面分析（伤停/战意/诱盘/冷门预警）和战术分析

【数据源说明】：
- 赛果：500.com (fetch_500com_results.py)
- 赔率：中国足彩网 zgzcw.com (fetch_pinnacle_odds.py)，非500.com，非中国竞彩网(sporttery.cn)
- 基础数据：football-data.org API

执行方式:
    python daily_report.py                      # 默认今天
    python daily_report.py --date 2026-05-12     # 指定日期
    python daily_report.py --dry-run             # 仅测试不生成文件
    python daily_report.py --force-refresh       # 强制刷新API缓存
    python daily_report.py --no-fundamental      # 跳过基本面分析（节省时间）
    python daily_report.py --tactical-only       # 只对4星+场次做战术分析
"""

import math
import json
import os
import sys
import re
import time
import csv
import argparse
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple


def _to_float(val):
    """将综合概率值转为0-1的浮点数，兼容str/int/float"""
    if val is None:
        return 0.0
    try:
        v = float(val)
    except (ValueError, TypeError):
        return 0.0
    # 如果>1说明是百分比形式(如55.3)，转为小数
    if v > 1:
        return v / 100
    return v


def _safe_float(val, default=0.0):
    """安全转为float，不做百分比转换（用于赔率等原始数值）"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

# ========== 导入球天下积分榜模块 ==========
try:
    from fetch_standings_qtx import fetch_standings as fetch_qtx_standings, get_match_standings_data
    HAS_QTX_STANDINGS = True
except ImportError:
    HAS_QTX_STANDINGS = False
    print("⚠️ 球天下积分榜模块未找到，将仅使用 football-data.org 数据")

# ========== 导入基本面分析模块 ==========
try:
    from fundamental_analysis import (
        analyze_fundamental, 
        get_tactical_preview,
        format_fundamental_summary,
        clear_cache,
        calculate_6dim_score,
        set_dim6_enabled
    )
    HAS_FUNDAMENTAL = True
except ImportError:
    HAS_FUNDAMENTAL = False
    print("⚠️ 基本面分析模块未找到，将使用基础模式")

# ========== 导入价值投注模块 ==========
try:
    from value_bet import calculate_value_bet
    HAS_VALUE_BET = True
except ImportError:
    HAS_VALUE_BET = False
    print("⚠️ 价值投注模块未找到，跳过EV计算")

# ========== 配置 ==========

# football-data.org API 配置
API_KEY = "178d3df92c484beea1aa652f1b8654b4"
API_BASE = "https://api.football-data.org/v4"

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构：如果scripts/同级的data/存在则为仓库模式，否则为独立模式
_REPO_DIR = os.path.dirname(SCRIPT_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    # 仓库模式：data/在scripts上一级
    DATA_BASE_DIR = _REPO_DIR
else:
    # 独立模式：data/在scripts同级
    DATA_BASE_DIR = SCRIPT_DIR

CACHE_DIR = os.path.join(DATA_BASE_DIR, "data/cache")
OUTPUT_DIR = os.path.join(DATA_BASE_DIR, "outputs/每日报表")
ODDS_FILE = os.path.join(CACHE_DIR, "real_odds.json")

# 体彩网赔率API
SPORTTERY_API_URL = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?sellStatus=3&pageSize=50&pageNo=1"

# SQLite数据库配置（统一football.db）
DB_PATH = os.path.join(DATA_BASE_DIR, "data/shared_state/football.db")

# 五大联赛代码映射（使用积分榜xG）
LEAGUE_CODES = {
    "PL": {"name": "英超", "name_en": "Premier League"},
    "PD": {"name": "西甲", "name_en": "La Liga"},
    "BL1": {"name": "德甲", "name_en": "Bundesliga"},
    "SA": {"name": "意甲", "name_en": "Serie A"},
    "FL1": {"name": "法甲", "name_en": "Ligue 1"},
}

# 其他支持联赛（使用赔率反推xG）
OTHER_LEAGUE_CODES = {
    "J1": {"name": "日职", "name_en": "J1 League"},
    "K1": {"name": "韩职", "name_en": "K League 1"},
    "MLS": {"name": "美职", "name_en": "MLS"},
    "SAU": {"name": "沙职", "name_en": "Saudi Pro League"},
    "ELC": {"name": "英冠", "name_en": "Championship"},
    "L2": {"name": "法乙", "name_en": "Ligue 2"},
    "CUP_IT": {"name": "意杯", "name_en": "Coppa Italia"},
    "AL": {"name": "澳超", "name_en": "A-League"},
}

# 所有支持的联赛
ALL_LEAGUE_CODES = {**LEAGUE_CODES, **OTHER_LEAGUE_CODES}

# openfootball 联赛编码映射（体彩网联赛名 → openfootball league_code）
# 用于自动获取积分榜和交锋记录
OPENFOOTBALL_LEAGUE_MAP = {
    "英超": "en.1",
    "西甲": "es.1",
    "德甲": "de.1",
    "意甲": "it.1",
    "法甲": "fr.1",
    "英冠": "en.2",
    "葡超": "pt.1",
    "荷甲": "nl.1",
    "比甲": "be.1",
    # 以下联赛openfootball不覆盖，需要fallback到搜索
}

# openfootball支持的联赛（可以自动获取数据）
OPENFOOTBALL_SUPPORTED = {"en.1", "es.1", "de.1", "it.1", "fr.1", "en.2", "pt.1", "nl.1", "be.1"}

# 体彩网联赛简称映射
LEAGUE_SHORT_NAMES = {
    "日职": "日职联",
    "韩职": "韩K联",
    "美职": "美职联",
    "沙职": "沙特联",
    "英冠": "英冠",
    "法乙": "法乙",
    "意杯": "意大利杯",
}

# ========== 双盘口Lambda计算（V4冷门预警用） ==========

# 尝试导入scipy（用于泊松分布优化）
try:
    from scipy.stats import poisson
    from scipy.optimize import minimize
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    import math
    class _PoissonFallback:
        @staticmethod
        def pmf(k, mu):
            if mu <= 0: return 1.0 if k == 0 else 0.0
            return math.exp(-mu) * (mu ** k) / math.factorial(k)
    poisson = _PoissonFallback()


def odds_to_prob(h: float, d: float, a: float) -> tuple:
    """从赔率反推隐含概率"""
    inv_h = 1/h if h > 0 else 0
    inv_d = 1/d if d > 0 else 0
    inv_a = 1/a if a > 0 else 0
    total = inv_h + inv_d + inv_a
    if total <= 0:
        return (0.0, 0.0, 0.0)
    return (inv_h/total, inv_d/total, inv_a/total)


def odds_to_lambda_fast(h: float, d: float, a: float, handicap: float = None) -> tuple:
    """
    从赔率快速反推lambda值
    
    Args:
        h: 主胜赔率
        d: 平局赔率
        a: 客胜赔率
        handicap: 让球数（如-1表示主队让1球）
    
    Returns:
        (lambda_home, lambda_away)
    """
    if h <= 0 or d <= 0 or a <= 0:
        return (None, None)
    
    p_win, p_draw, p_loss = odds_to_prob(h, d, a)
    
    if not HAS_SCIPY:
        # Fallback: 使用简单的网格搜索
        return _odds_to_lambda_grid(h, d, a, handicap)
    
    def objective(lambdas):
        lam_h, lam_a = lambdas[0], lambdas[1]
        if lam_h <= 0 or lam_a <= 0:
            return 1e10
        
        pw, pd_val, pl = 0.0, 0.0, 0.0
        for i in range(10):
            for j in range(10):
                p = poisson.pmf(i, lam_h) * poisson.pmf(j, lam_a)
                if handicap is not None:
                    adj_score = i + handicap
                    if adj_score > j:
                        pw += p
                    elif adj_score == j:
                        pd_val += p
                    else:
                        pl += p
                else:
                    if i > j:
                        pw += p
                    elif i == j:
                        pd_val += p
                    else:
                        pl += p
        
        loss = (pw - p_win)**2 + (pd_val - p_draw)**2 + (pl - p_loss)**2
        return loss
    
    best_result = None
    best_loss = float('inf')
    
    for lam_h_init in [0.5, 1.0, 1.5, 2.0]:
        for lam_a_init in [0.5, 0.8, 1.0, 1.2]:
            try:
                result = minimize(
                    objective,
                    [lam_h_init, lam_a_init],
                    method='L-BFGS-B',
                    bounds=[(0.1, 5.0), (0.1, 5.0)]
                )
                if result.fun < best_loss:
                    best_loss = result.fun
                    best_result = result.x
            except:
                continue
    
    if best_result is None:
        return (None, None)
    
    return (round(best_result[0], 2), round(best_result[1], 2))


def _odds_to_lambda_grid(h: float, d: float, a: float, handicap: float = None) -> tuple:
    """Fallback: 网格搜索版本"""
    if h <= 0 or d <= 0 or a <= 0:
        return (None, None)
    
    p_win, p_draw, p_loss = odds_to_prob(h, d, a)
    
    best_loss = float('inf')
    best_lam = (1.0, 1.0)
    
    for lam_h in [x * 0.1 for x in range(5, 50)]:
        for lam_a in [x * 0.1 for x in range(5, 30)]:
            pw, pd_val, pl = 0.0, 0.0, 0.0
            
            for i in range(8):
                for j in range(8):
                    p = poisson.pmf(i, lam_h) * poisson.pmf(j, lam_a)
                    if handicap is not None:
                        adj = i + handicap
                        if adj > j:
                            pw += p
                        elif adj == j:
                            pd_val += p
                        else:
                            pl += p
                    else:
                        if i > j:
                            pw += p
                        elif i == j:
                            pd_val += p
                        else:
                            pl += p
            
            loss = (pw - p_win)**2 + (pd_val - p_draw)**2 + (pl - p_loss)**2
            if loss < best_loss:
                best_loss = loss
                best_lam = (lam_h, lam_a)
    
    return best_lam


# ========== 队名映射（API英文 -> 中文） ==========

TEAM_NAME_MAP = {
    # 英超
    "Arsenal FC": "阿森纳", "Arsenal": "阿森纳",
    "Aston Villa FC": "阿斯顿维拉", "Aston Villa": "阿斯顿维拉",
    "AFC Bournemouth": "伯恩茅斯", "Bournemouth": "伯恩茅斯",
    "Brentford FC": "布伦特福德", "Brentford": "布伦特福德",
    "Brighton & Hove Albion FC": "布莱顿", "Brighton": "布莱顿",
    "Burnley FC": "伯恩利", "Burnley": "伯恩利",
    "Chelsea FC": "切尔西", "Chelsea": "切尔西",
    "Crystal Palace FC": "水晶宫", "Crystal Palace": "水晶宫",
    "Everton FC": "埃弗顿", "Everton": "埃弗顿",
    "Fulham FC": "富勒姆", "Fulham": "富勒姆",
    "Leeds United FC": "利兹联", "Leeds United": "利兹联", "Leeds": "利兹联",
    "Leicester City FC": "莱斯特城", "Leicester City": "莱斯特城", "Leicester": "莱斯特城",
    "Liverpool FC": "利物浦", "Liverpool": "利物浦",
    "Manchester City FC": "曼城", "Manchester City": "曼城", "Man City": "曼城",
    "Manchester United FC": "曼联", "Manchester United": "曼联", "Manchester Utd": "曼联", "Man Utd": "曼联",
    "Newcastle United FC": "纽卡斯尔", "Newcastle United": "纽卡斯尔", "Newcastle": "纽卡斯尔",
    "Nottingham Forest FC": "诺丁汉森林", "Nottingham Forest": "诺丁汉森林", "Nottingham": "诺丁汉森林",
    "Southampton FC": "南安普顿", "Southampton": "南安普顿",
    "Tottenham Hotspur FC": "热刺", "Tottenham Hotspur": "热刺", "Tottenham": "热刺",
    "West Ham United FC": "西汉姆", "West Ham United": "西汉姆", "West Ham": "西汉姆",
    "Wolverhampton Wanderers FC": "狼队", "Wolverhampton Wanderers": "狼队", "Wolverhampton": "狼队", "Wolves": "狼队",
    "Sunderland AFC": "桑德兰", "Sunderland": "桑德兰",
    "Ipswich Town FC": "伊普斯维奇", "Ipswich Town": "伊普斯维奇", "Ipswich": "伊普斯维奇",
    "Luton Town FC": "卢顿", "Luton Town": "卢顿", "Luton": "卢顿",
    "Sheffield United FC": "谢菲联", "Sheffield United": "谢菲联", "Sheffield Utd": "谢菲联",
    # 西甲
    "Deportivo Alavés": "阿拉维斯", "Alavés": "阿拉维斯", "Alaves": "阿拉维斯",
    "Athletic Club": "毕尔巴鄂", "Athletic Bilbao": "毕尔巴鄂", "Athletic": "毕尔巴鄂",
    "Club Atlético de Madrid": "马德里竞技", "Atlético Madrid": "马德里竞技", "Atletico Madrid": "马德里竞技",
    "FC Barcelona": "巴塞罗那", "Barcelona": "巴塞罗那", "Barca": "巴塞罗那",
    "RC Celta de Vigo": "塞尔塔", "Celta Vigo": "塞尔塔", "Celta": "塞尔塔",
    "RCD Espanyol de Barcelona": "西班牙人", "Espanyol": "西班牙人",
    "Getafe CF": "赫塔费", "Getafe": "赫塔费",
    "Granada CF": "格拉纳达", "Granada": "格拉纳达",
    "Girona FC": "赫罗纳", "Girona": "赫罗纳",
    "UD Las Palmas": "拉斯帕尔马斯", "Las Palmas": "拉斯帕尔马斯",
    "CD Leganés": "莱加内斯", "Leganés": "莱加内斯", "Leganes": "莱加内斯",
    "RCD Mallorca": "马洛卡", "Mallorca": "马洛卡",
    "CA Osasuna": "奥萨苏纳", "Osasuna": "奥萨苏纳",
    "Rayo Vallecano de Madrid": "巴列卡诺", "Rayo Vallecano": "巴列卡诺",
    "Real Betis Balompié": "皇家贝蒂斯", "Real Betis": "皇家贝蒂斯", "Betis": "皇家贝蒂斯",
    "Real Madrid CF": "皇家马德里", "Real Madrid": "皇家马德里",
    "Real Sociedad de Fútbol": "皇家社会", "Real Sociedad": "皇家社会",
    "Sevilla FC": "塞维利亚", "Sevilla": "塞维利亚",
    "Valencia CF": "瓦伦西亚", "Valencia": "瓦伦西亚",
    "Villarreal CF": "比利亚雷亚尔", "Villarreal": "比利亚雷亚尔",
    "Real Oviedo": "皇家奥维耶多", "Oviedo": "皇家奥维耶多",
    "Racing Club de Ferrol": "费罗尔竞赛", "Ferrol": "费罗尔竞赛",
    # 德甲
    "FC Bayern München": "拜仁慕尼黑", "Bayern Munich": "拜仁慕尼黑", "Bayern": "拜仁慕尼黑",
    "Borussia Dortmund": "多特蒙德", "Dortmund": "多特蒙德",
    "RB Leipzig": "莱比锡红牛",
    "Bayer 04 Leverkusen": "勒沃库森", "Bayer Leverkusen": "勒沃库森", "Leverkusen": "勒沃库森",
    "Eintracht Frankfurt": "法兰克福", "Frankfurt": "法兰克福",
    "SC Freiburg": "弗赖堡", "Freiburg": "弗赖堡",
    "VfL Wolfsburg": "沃尔夫斯堡", "Wolfsburg": "沃尔夫斯堡",
    "Borussia Mönchengladbach": "门兴", "M'gladbach": "门兴", "Mönchengladbach": "门兴",
    "TSG 1899 Hoffenheim": "霍芬海姆", "Hoffenheim": "霍芬海姆",
    "1. FSV Mainz 05": "美因茨", "Mainz": "美因茨",
    "1. FC Köln": "科隆", "Köln": "科隆", "Cologne": "科隆",
    "SV Werder Bremen": "不来梅", "Werder Bremen": "不来梅", "Bremen": "不来梅",
    "1. FC Union Berlin": "柏林联合", "Union Berlin": "柏林联合",
    "VfB Stuttgart": "斯图加特", "Stuttgart": "斯图加特",
    "FC Augsburg": "奥格斯堡", "Augsburg": "奥格斯堡",
    "VfL Bochum 1848": "波鸿", "Bochum": "波鸿",
    "1. FC Heidenheim 1846": "海登海姆", "Heidenheim": "海登海姆",
    "Hamburger SV": "汉堡", "Hamburger": "汉堡",
    "Eintracht Braunschweig": "不伦瑞克",
    "Holstein Kiel": "荷尔斯泰因基尔", "Holstein Kiel": "荷尔斯泰因基尔",
    "VfL Wolfsburg": "沃尔夫斯堡",
    # 意甲
    "Atalanta BC": "亚特兰大", "Atalanta": "亚特兰大",
    "Bologna FC 1909": "博洛尼亚", "Bologna": "博洛尼亚",
    "Cagliari Calcio": "卡利亚里", "Cagliari": "卡利亚里",
    "Como 1907": "科莫", "Como": "科莫",
    "ACF Fiorentina": "佛罗伦萨", "Fiorentina": "佛罗伦萨",
    "Genoa CFC": "热那亚", "Genoa": "热那亚",
    "FC Internazionale Milano": "国际米兰", "Inter Milan": "国际米兰", "Inter": "国际米兰",
    "Juventus FC": "尤文图斯", "Juventus": "尤文图斯",
    "SS Lazio": "拉齐奥", "Lazio": "拉齐奥",
    "US Lecce": "莱切", "Lecce": "莱切",
    "AC Milan": "AC米兰", "Milan": "AC米兰",
    "SSC Napoli": "那不勒斯", "Napoli": "那不勒斯",
    "Parma Calcio 1913": "帕尔马", "Parma": "帕尔马",
    "AS Roma": "罗马", "Roma": "罗马",
    "US Sassuolo Calcio": "萨索洛", "Sassuolo": "萨索洛",
    "Torino FC": "都灵", "Torino": "都灵",
    "Udinese Calcio": "乌迪内斯", "Udinese": "乌迪内斯",
    "Hellas Verona FC": "维罗纳", "Verona": "维罗纳",
    "Venezia FC": "威尼斯", "Venezia": "威尼斯",
    "US Cremonese": "克雷莫纳", "Cremonese": "克雷莫纳",
    "Empoli FC": "恩波利", "Empoli": "恩波利",
    "Frosinone Calcio": "弗罗西诺内", "Frosinone": "弗罗西诺内",
    "US Salernitana 1919": "萨勒尼塔纳", "Salernitana": "萨勒尼塔纳",
    "AC Monza": "蒙扎", "Monza": "蒙扎",
    # 法甲
    "Paris Saint-Germain FC": "巴黎圣日耳曼", "Paris Saint-Germain": "巴黎圣日耳曼", "PSG": "巴黎圣日耳曼",
    "Olympique de Marseille": "马赛", "Marseille": "马赛",
    "AS Monaco FC": "摩纳哥", "AS Monaco": "摩纳哥", "Monaco": "摩纳哥",
    "Olympique Lyonnais": "里昂", "Lyon": "里昂",
    "Lille OSC": "里尔", "Lille": "里尔",
    "OGC Nice": "尼斯", "Nice": "尼斯",
    "Stade Rennais FC": "雷恩", "Rennes": "雷恩",
    "Racing Club de Lens": "朗斯", "Lens": "朗斯",
    "Stade Brestois 29": "布雷斯特", "Brest": "布雷斯特",
    "FC Nantes": "南特", "Nantes": "南特",
    "RC Strasbourg Alsace": "斯特拉斯堡", "Strasbourg": "斯特拉斯堡",
    "MHSC Montpellier": "蒙彼利埃", "Montpellier": "蒙彼利埃",
    "Toulouse FC": "图卢兹", "Toulouse": "图卢兹",
    "FC Lorient": "洛里昂", "Lorient": "洛里昂",
    "Le Havre AC": "勒阿弗尔", "Le Havre": "勒阿弗尔",
    "FC Metz": "梅斯", "Metz": "梅斯",
    "AJ Auxerre": "欧塞尔", "Auxerre": "欧塞尔",
    "Stade de Reims": "兰斯", "Reims": "兰斯",
    "Clermont Foot 63": "克莱蒙", "Clermont": "克莱蒙",
    "ES Troyes AC": "特鲁瓦", "Troyes": "特鲁瓦",
    "Red Star FC": "圣旺红星",
    "Rodez AF": "罗德兹", "Rodez": "罗德兹",
    "Angers SCO": "昂热", "Angers": "昂热",
    "FC Nantes": "南特",
    "AS Saint-Étienne": "圣埃蒂安", "Saint-Étienne": "圣埃蒂安",
}

# 队名别名映射（简称 -> API全名）
TEAM_ALIASES = {
    "Manchester Utd": "Manchester United FC",
    "Man Utd": "Manchester United FC",
    "Man United": "Manchester United FC",
    "Man City": "Manchester City FC",
    "Newcastle": "Newcastle United FC",
    "Nottingham": "Nottingham Forest FC",
    "Tottenham": "Tottenham Hotspur FC",
    "West Ham": "West Ham United FC",
    "Wolverhampton": "Wolverhampton Wanderers FC",
    "Wolves": "Wolverhampton Wanderers FC",
    "Brighton": "Brighton & Hove Albion FC",
    "Leeds": "Leeds United FC",
    "Leicester": "Leicester City FC",
    "Crystal Palace": "Crystal Palace FC",
    "Burnley": "Burnley FC",
    "Fulham": "Fulham FC",
    "Sunderland": "Sunderland AFC",
    "Bournemouth": "AFC Bournemouth",
    "Brentford": "Brentford FC",
    "Aston Villa": "Aston Villa FC",
    "Arsenal": "Arsenal FC",
    "Liverpool": "Liverpool FC",
    "Chelsea": "Chelsea FC",
    "Everton": "Everton FC",
    "Southampton": "Southampton FC",
    "Atlético Madrid": "Club Atlético de Madrid",
    "Atletico Madrid": "Club Atlético de Madrid",
    "Real Sociedad": "Real Sociedad de Fútbol",
    "Athletic Bilbao": "Athletic Club",
    "Alavés": "Deportivo Alavés",
    "Alaves": "Deportivo Alavés",
    "Celta Vigo": "RC Celta de Vigo",
    "Espanyol": "RCD Espanyol de Barcelona",
    "Leganés": "CD Leganés",
    "Leganes": "CD Leganés",
    "Betis": "Real Betis Balompié",
    "Rayo Vallecano": "Rayo Vallecano de Madrid",
    "Valladolid": "Real Valladolid CF",
    "Almería": "UD Almería",
    "Almeria": "UD Almería",
    "Cádiz": "Cádiz CF",
    "Cadiz": "Cádiz CF",
    "Las Palmas": "UD Las Palmas",
    "Bayern Munich": "FC Bayern München",
    "Dortmund": "Borussia Dortmund",
    "Leverkusen": "Bayer 04 Leverkusen",
    "M'gladbach": "Borussia Mönchengladbach",
    "Köln": "1. FC Köln",
    "Hoffenheim": "TSG 1899 Hoffenheim",
    "Mainz": "1. FSV Mainz 05",
    "Frankfurt": "Eintracht Frankfurt",
    "Freiburg": "SC Freiburg",
    "Wolfsburg": "VfL Wolfsburg",
    "Stuttgart": "VfB Stuttgart",
    "Augsburg": "FC Augsburg",
    "Bochum": "VfL Bochum 1848",
    "Heidenheim": "1. FC Heidenheim 1846",
    "Union Berlin": "1. FC Union Berlin",
    "Werder Bremen": "SV Werder Bremen",
    "Bremen": "SV Werder Bremen",
    "Inter Milan": "FC Internazionale Milano",
    "Inter": "FC Internazionale Milano",
    "Napoli": "SSC Napoli",
    "Roma": "AS Roma",
    "Lazio": "SS Lazio",
    "Juventus": "Juventus FC",
    "Atalanta": "Atalanta BC",
    "Fiorentina": "ACF Fiorentina",
    "Bologna": "Bologna FC 1909",
    "Torino": "Torino FC",
    "Cagliari": "Cagliari Calcio",
    "Genoa": "Genoa CFC",
    "Udinese": "Udinese Calcio",
    "Sassuolo": "US Sassuolo Calcio",
    "Lecce": "US Lecce",
    "Verona": "Hellas Verona FC",
    "Empoli": "Empoli FC",
    "Como": "Como 1907",
    "Parma": "Parma Calcio 1913",
    "Venezia": "Venezia FC",
    "Monza": "AC Monza",
    "PSG": "Paris Saint-Germain FC",
    "Paris Saint Germain": "Paris Saint-Germain FC",
    "Marseille": "Olympique de Marseille",
    "Monaco": "AS Monaco FC",
    "Lyon": "Olympique Lyonnais",
    "Lille": "Lille OSC",
    "Nice": "OGC Nice",
    "Rennes": "Stade Rennais FC",
    "Lens": "Racing Club de Lens",
    "Brest": "Stade Brestois 29",
    "Nantes": "FC Nantes",
    "Strasbourg": "RC Strasbourg Alsace",
    "Montpellier": "MHSC Montpellier",
    "Toulouse": "Toulouse FC",
    "Lorient": "FC Lorient",
    "Le Havre": "Le Havre AC",
    "Metz": "FC Metz",
    "Auxerre": "AJ Auxerre",
    "Reims": "Stade de Reims",
    "Clermont": "Clermont Foot 63",
    "Oviedo": "Real Oviedo",
    "Ferrol": "Racing Club de Ferrol",
    # API返回的特殊队名
    "RC Celta de Vigo": "RC Celta de Vigo",
    "Levante UD": "Levante UD",
    "Elche CF": "Elche CF",
    "CA Osasuna": "CA Osasuna",
    # 体彩网队名
    "皇马": "Real Madrid CF",
    "巴萨": "FC Barcelona",
    "巴黎圣曼": "Paris Saint-Germain FC",
    "国米": "FC Internazionale Milano",
    "米兰": "AC Milan",
    "贝蒂斯": "Real Betis Balompié",
    "马竞": "Club Atlético de Madrid",
    "比利亚雷": "Villarreal CF",
    "毕尔巴鄂": "Athletic Club",
    "塞尔塔": "RC Celta de Vigo",
    "奥维耶多": "Real Oviedo",
    "南安普敦": "Southampton FC",
    "斯特拉斯": "RC Strasbourg Alsace",
    "圣旺红星": "Red Star FC",
}


# ========== 队名别名映射（用于复盘时匹配不同译名） ==========
# 体彩网缩写、用户赛果APP名称、系统标准名之间的对应关系
# 所有别名统一映射到系统标准名

TEAM_VARIANTS = {
    # === 沙特联 ===
    "达曼协定": "达曼协作",
    "伊地法格": "达曼协作",
    "达曼协作": "达曼协作",
    "瓜达席亚": "卡达西亚",
    "胡巴卡德": "卡达西亚",
    "卡达西亚": "卡达西亚",
    "艾哈斯姆": "哈森姆",
    "拉斯决心": "哈森姆",
    "哈森姆": "哈森姆",
    "伊蒂哈德吉达": "吉达联合",
    "吉达联合": "吉达联合",
    "利雅胜利": "利雅得胜利",
    "利雅新月": "利雅得新月",
    "迈季宽广": "迈季迈阿",
    "布赖合作": "布赖代合作",
    
    # === 西甲 ===
    "华伦西亚": "瓦伦西亚",
    "巴伦西亚": "瓦伦西亚",
    "瓦伦西亚": "瓦伦西亚",
    "皇家苏斯达": "皇家社会",
    "皇家社会": "皇家社会",
    "皇家奥维耶多": "皇家奥维耶多",
    "奥维多": "皇家奥维耶多",
    "奥维耶多": "皇家奥维耶多",
    "巴塞罗那": "巴塞罗那",
    "巴萨": "巴塞罗那",
    "皇家马德里": "皇家马德里",
    "皇马": "皇家马德里",
    
    # === 英超 ===
    "热刺": "热刺",
    "托特纳姆": "热刺",
    "曼彻斯特城": "曼城",
    "曼彻斯特联": "曼联",
    
    # === 德甲 ===
    "拜仁慕尼黑": "拜仁慕尼黑",
    "拜仁": "拜仁慕尼黑",
    "多特蒙德": "多特蒙德",
    
    # === 意甲 ===
    "国际米兰": "国际米兰",
    "AC米兰": "AC米兰",
    "尤文图斯": "尤文图斯",
    
    # === 法甲 ===
    "巴黎圣日耳曼": "巴黎圣日耳曼",
    "巴黎圣曼": "巴黎圣日耳曼",
}


# ========== 工具函数 ==========

def log(msg):
    """日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def http_get_json(url: str, headers: dict = None) -> Optional[dict]:
    """HTTP GET请求，返回JSON"""
    try:
        import urllib.request
        req = urllib.request.Request(url)
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        log(f"HTTP请求失败: {url} - {e}")
        return None


def ensure_dirs():
    """确保必要目录存在"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_cache_path(league_code: str, data_type: str, date_str: str = None) -> str:
    """获取缓存文件路径"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(CACHE_DIR, f"{data_type}_{league_code}_{date_str}.json")


def is_cache_valid(cache_path: str, max_age_hours: int = 24) -> bool:
    """检查缓存是否有效"""
    if not os.path.exists(cache_path):
        return False
    file_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
    return datetime.now() - file_time < timedelta(hours=max_age_hours)


def save_cache(data: dict, cache_path: str):
    """保存缓存"""
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"缓存保存失败: {e}")


def load_cache(cache_path: str) -> Optional[dict]:
    """加载缓存"""
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        log(f"缓存加载失败: {e}")
    return None


# ========== 队名标准化 ==========

def normalize_team_name(name: str) -> str:
    """标准化队名用于赔率匹配
    
    优先级：
    1. TEAM_VARIANTS（别名映射，用于复盘时匹配不同译名）
    2. TEAM_NAME_MAP（API英文 -> 中文）
    3. TEAM_ALIASES（简称 -> API全名）
    4. 模糊匹配
    """
    if not name:
        return name
    
    name = name.strip()
    
    # 1. 先检查 TEAM_VARIANTS（复盘别名映射）
    if name in TEAM_VARIANTS:
        return TEAM_VARIANTS[name]
    
    # 2. 检查 TEAM_NAME_MAP（英文 -> 中文）
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    
    # 3. 检查 TEAM_ALIASES（简称 -> API名 -> 中文）
    if name in TEAM_ALIASES:
        api_name = TEAM_ALIASES[name]
        if api_name in TEAM_NAME_MAP:
            return TEAM_NAME_MAP[api_name]
        return api_name
    
    # 4. 模糊匹配（英文队名）
    name_lower = name.lower()
    for en, cn in TEAM_NAME_MAP.items():
        if en.lower() == name_lower:
            return cn
    
    # 5. 未找到匹配，返回原始名称
    return name


def normalize_team_for_review(name: str) -> str:
    """专门用于复盘时标准化队名
    
    与normalize_team_name()的区别：
    - 只使用TEAM_VARIANTS进行别名映射
    - 不依赖API英文名
    - 更宽松的匹配逻辑
    """
    if not name:
        return name
    
    name = name.strip()
    
    # 直接查找别名映射
    if name in TEAM_VARIANTS:
        return TEAM_VARIANTS[name]
    
    return name


def find_team_in_standings(team_name: str, standings: dict) -> Optional[dict]:
    """在积分榜中查找球队，支持模糊匹配"""
    normalized = team_name.strip()
    
    # 1. 直接匹配
    if normalized in standings:
        return standings[normalized]
    
    # 2. 检查别名 -> API名
    alias = TEAM_ALIASES.get(normalized)
    if alias and alias in standings:
        return standings[alias]
    
    # 3. 模糊匹配 - 检查是否包含
    team_lower = normalized.lower()
    for name, data in standings.items():
        name_lower = name.lower()
        if team_lower in name_lower or name_lower in team_lower:
            return data
        # 移除空格和特殊字符后匹配
        clean_team = re.sub(r'[^a-z]', '', team_lower)
        clean_name = re.sub(r'[^a-z]', '', name_lower)
        if clean_team and clean_name and (clean_team in clean_name or clean_name in clean_team):
            return data
    
    return None


# ========== API 数据获取 ==========

def fetch_standings(league_code: str, force_refresh: bool = False) -> Optional[dict]:
    """
    获取联赛积分榜数据
    """
    cache_path = get_cache_path(league_code, "standings_api")
    
    if not force_refresh and is_cache_valid(cache_path):
        log(f"从缓存加载积分榜: {league_code}")
        return load_cache(cache_path)
    
    log(f"正在获取 {LEAGUE_CODES.get(league_code, {}).get('name', league_code)} 积分榜...")
    
    url = f"{API_BASE}/competitions/{league_code}/standings"
    headers = {
        'X-Auth-Token': API_KEY,
        'Accept': 'application/json'
    }
    
    data = http_get_json(url, headers)
    if not data:
        return None
    
    # 解析 TOTAL 积分榜
    total_standings = {}
    current_matchday = 30
    
    for standing in data.get('standings', []):
        if standing.get('type') == 'TOTAL':
            for entry in standing.get('table', []):
                team_name = entry.get('team', {}).get('name', '')
                total_standings[team_name] = {
                    'name': team_name,
                    'name_zh': TEAM_NAME_MAP.get(team_name, team_name),
                    'rank': entry.get('position', 0),
                    'played': entry.get('playedGames', 0),
                    'won': entry.get('won', 0),
                    'drawn': entry.get('draw', 0),
                    'lost': entry.get('lost', 0),
                    'gf': entry.get('goalsFor', 0),
                    'ga': entry.get('goalsAgainst', 0),
                    'points': entry.get('points', 0),
                }
            break
    
    # 尝试获取当前轮次
    season = data.get('season', {})
    current_matchday = season.get('currentMatchday', 30)
    
    if total_standings:
        save_cache({"standings": total_standings, "current_matchday": current_matchday}, cache_path)
        log(f"✓ 获取到 {len(total_standings)} 支球队的积分榜 (第{current_matchday}轮)")
    
    return {"standings": total_standings, "current_matchday": current_matchday}


def fetch_matches_in_window(league_code: str, start_time: datetime, end_time: datetime, force_refresh: bool = False) -> List[dict]:
    """
    获取指定时间窗口内的比赛
    时间窗口：当天13:00至次日12:59（24小时滚动）
    """
    cache_path = get_cache_path(league_code, "matches_window")
    
    if not force_refresh and is_cache_valid(cache_path):
        log(f"从缓存加载比赛: {league_code}")
        data = load_cache(cache_path)
        if data:
            return data.get('matches', [])
    
    log(f"正在获取 {LEAGUE_CODES.get(league_code, {}).get('name', league_code)} 比赛列表...")
    
    # 获取未来7天的比赛
    from datetime import timedelta
    date_from = (start_time - timedelta(hours=8)).strftime("%Y-%m-%d")  # 转回UTC
    date_to = (end_time - timedelta(hours=8)).strftime("%Y-%m-%d")  # 转回UTC
    
    url = f"{API_BASE}/competitions/{league_code}/matches?dateFrom={date_from}&dateTo={date_to}"
    headers = {
        'X-Auth-Token': API_KEY,
        'Accept': 'application/json'
    }
    
    data = http_get_json(url, headers)
    if not data:
        return []
    
    matches = []
    for match in data.get('matches', []):
        utc_date = match.get('utcDate', '')
        if not utc_date:
            continue
        
        # 解析时间（API返回UTC时间）
        try:
            match_time = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
            # 转换为北京时间（去掉时区信息，简化比较）
            local_time = match_time.replace(tzinfo=None) + timedelta(hours=8)
            
            # 过滤在时间窗口内的比赛
            if start_time <= local_time <= end_time:
                matches.append({
                    'id': match.get('id'),
                    'home_team': match.get('homeTeam', {}).get('name', ''),
                    'away_team': match.get('awayTeam', {}).get('name', ''),
                    'match_time_str': local_time.strftime("%Y-%m-%d %H:%M"),
                    'utc_time': utc_date,
                })
        except Exception as e:
            log(f"解析比赛时间失败: {e}")
            continue
    
    save_cache({'matches': matches}, cache_path)
    log(f"✓ 获取到 {len(matches)} 场在时间窗口内的比赛")
    
    return matches


# ========== 真实赔率获取 ==========

def fetch_real_odds_from_api() -> dict:
    """
    从体彩网API获取真实赔率数据
    API返回结构化JSON，包含竞彩编号、中文队名简称、胜平负赔率(HAD)和让球赔率(HHAD)
    
    返回格式: {
        "主队简称 vs 客队简称": {
            "home": 1.90, "draw": 3.80, "away": 4.00,  # HAD赔率（可能为None）
            "hhad_home": 2.62, "hhad_draw": 4.30, "hhad_away": 1.94,  # HHAD让球赔率
            "hhad_handicap": -1.0,  # 让球数（负数表示主队让球）
            "odds_source": "had" 或 "hhad",  # 赔率来源
            "matchNum": "2003", "league": "英超", ...
        }, ...
    }
    """
    import urllib.request
    
    log("正在从体彩网API获取赔率数据...")
    
    try:
        req = urllib.request.Request(SPORTTERY_API_URL)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        req.add_header("Referer", "https://www.lottery.gov.cn/")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        # 解析API返回的数据
        odds_data = {}
        match_count = 0
        hhad_only_count = 0  # 仅HHAD有赔率的场次
        
        value = data.get("value", {})
        match_info_list = value.get("matchInfoList", [])
        
        for day_info in match_info_list:
            sub_match_list = day_info.get("subMatchList", [])
            for match in sub_match_list:
                match_num = match.get("matchNum", "")
                home_team = match.get("homeTeamAbbName", "")
                away_team = match.get("awayTeamAbbName", "")
                
                key = f"{home_team} vs {away_team}"
                
                # 初始化赔率数据
                match_data = {
                    "home": None,
                    "draw": None,
                    "away": None,
                    "hhad_home": None,
                    "hhad_draw": None,
                    "hhad_away": None,
                    "hhad_handicap": None,
                    "odds_source": None,
                    "matchNum": match_num,
                    "league": match.get("leagueAbbName", ""),
                    "matchDate": match.get("matchDate", ""),
                    "matchTime": match.get("matchTime", ""),
                }
                
                # 获取胜平负赔率 (had)
                had = match.get("had", {})
                if had:
                    home_odds = had.get("h")
                    draw_odds = had.get("d")
                    away_odds = had.get("a")
                    
                    if home_odds and draw_odds and away_odds:
                        match_data["home"] = float(home_odds)
                        match_data["draw"] = float(draw_odds)
                        match_data["away"] = float(away_odds)
                        match_data["odds_source"] = "had"
                
                # 获取让球赔率 (hhad)
                hhad = match.get("hhad", {})
                if hhad:
                    hhad_home = hhad.get("h")
                    hhad_draw = hhad.get("d")
                    hhad_away = hhad.get("a")
                    hhad_goal_line = hhad.get("goalLine")
                    hhad_goal_line_value = hhad.get("goalLineValue")
                    
                    if hhad_home and hhad_draw and hhad_away:
                        match_data["hhad_home"] = float(hhad_home)
                        match_data["hhad_draw"] = float(hhad_draw)
                        match_data["hhad_away"] = float(hhad_away)
                        
                        # 解析让球数（goalLine是字符串如"-1"，goalLineValue是"-1.00"）
                        if hhad_goal_line_value:
                            try:
                                match_data["hhad_handicap"] = float(hhad_goal_line_value)
                            except (ValueError, TypeError):
                                try:
                                    match_data["hhad_handicap"] = float(hhad_goal_line)
                                except (ValueError, TypeError):
                                    match_data["hhad_handicap"] = None
                        elif hhad_goal_line:
                            try:
                                match_data["hhad_handicap"] = float(hhad_goal_line)
                            except (ValueError, TypeError):
                                match_data["hhad_handicap"] = None
                
                # 决定赔率来源：优先用HAD，HAD为空但HHAD有时用HHAD
                if match_data["home"] is not None:
                    # HAD赔率存在，使用HAD
                    pass
                elif match_data["hhad_home"] is not None:
                    # HAD为空但HHAD存在，标记使用HHAD
                    match_data["odds_source"] = "hhad"
                    hhad_only_count += 1
                
                # 只添加有至少一种赔率的比赛
                if match_data["home"] is not None or match_data["hhad_home"] is not None:
                    odds_data[key] = match_data
                    match_count += 1
        
        # 同时保存到缓存文件作为备份
        try:
            os.makedirs(os.path.dirname(ODDS_FILE), exist_ok=True)
            with open(ODDS_FILE, 'w', encoding='utf-8') as f:
                json.dump(odds_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"保存赔率缓存失败: {e}")
        
        log(f"✓ 从API获取到 {match_count} 场比赛的赔率")
        if hhad_only_count > 0:
            log(f"  其中 {hhad_only_count} 场仅开售让球盘(HHAD)")
        
        return odds_data
        
    except Exception as e:
        log(f"⚠️ 从API获取赔率失败: {e}")
        return {}


def load_real_odds() -> dict:
    """
    从缓存文件加载真实赔率数据（Fallback方案）
    返回格式: {"主队简称 vs 客队简称": {"home": 1.90, "draw": 3.80, "away": 4.00}, ...}
    """
    if not os.path.exists(ODDS_FILE):
        log(f"⚠️ 真实赔率文件不存在: {ODDS_FILE}")
        log(f"   提示: 脚本将尝试从体彩网API获取赔率")
        return {}
    
    try:
        with open(ODDS_FILE, 'r', encoding='utf-8') as f:
            odds_data = json.load(f)
        log(f"✓ 从缓存加载真实赔率数据: {len(odds_data)} 场比赛")
        return odds_data
    except Exception as e:
        log(f"⚠️ 加载赔率文件失败: {e}")
        return {}


def get_odds_data(prefer_api: bool = True) -> dict:
    """
    获取赔率数据的主入口函数
    优先从API获取，失败时fallback到缓存文件
    
    Args:
        prefer_api: 是否优先使用API获取（默认True）
    
    Returns:
        赔率数据字典
    """
    if prefer_api:
        # 优先尝试API
        odds_data = fetch_real_odds_from_api()
        if odds_data:
            return odds_data
        
        # API失败，尝试缓存
        log("API获取失败，尝试从缓存加载...")
        odds_data = load_real_odds()
        if odds_data:
            return odds_data
        
        return {}
    else:
        # 直接使用缓存
        return load_real_odds()


def match_odds_key(home_team: str, away_team: str, odds_data: dict) -> Optional[str]:
    """匹配赔率数据的键名"""
    # 英文队名到中文简称的直接映射（体彩网使用中文简称）
    en_to_cn_short = {
        # 英超
        "FC Barcelona": "巴萨",
        "Barcelona": "巴萨",
        "Deportivo Alavés FC": "阿拉维斯",
        "Deportivo Alavés": "阿拉维斯",
        "Alavés": "阿拉维斯",
        "Paris Saint-Germain FC": "巴黎圣曼",
        "Paris Saint-Germain": "巴黎圣曼",
        "Stade Rennais FC": "雷恩",
        "Racing Club de Lens": "朗斯",
        "RC Lens": "朗斯",
        "RC Strasbourg Alsace": "斯特拉斯",
        "Strasbourg": "斯特拉斯",
        "Strasbourg Alsace": "斯特拉斯",
        "Southampton FC": "南安普敦",
        "Middlesbrough FC": "米堡",
        "Real Club Celta de Vigo": "塞尔塔",
        "Celta Vigo": "塞尔塔",
        "Celta de Vigo": "塞尔塔",
    }
    
    # 先处理英文队名到中文简称
    home_cn = en_to_cn_short.get(home_team, home_team)
    away_cn = en_to_cn_short.get(away_team, away_team)
    
    # 再标准化为中文全称
    home_normalized = normalize_team_name(home_cn)
    away_normalized = normalize_team_name(away_cn)
    
    # 简称映射 - 包含中文全称/简称和英文名到中文简称的映射
    short_names = {
        # 中文全称 -> 中文简称
        "皇家马德里": ["皇马"],
        "巴塞罗那": ["巴萨"],
        "巴黎圣日耳曼": ["巴黎", "大巴黎", "巴黎圣曼"],
        "国际米兰": ["国米"],
        "AC米兰": ["米兰"],
        "皇家贝蒂斯": ["贝蒂斯"],
        "皇家奥维耶多": ["奥维耶多"],
        "塞尔塔": ["塞尔塔维戈"],
        "马德里竞技": ["马竞"],
        "比利亚雷亚尔": ["比利亚雷"],
        "塞维利亚": ["塞维利亚FC"],
        "毕尔巴鄂": ["毕尔巴鄂竞技"],
        "阿拉维斯": ["阿拉维斯"],
        "赫塔费": ["赫塔费"],
        "拉齐奥": ["拉齐奥"],
        "尤文图斯": ["尤文"],
        "罗马": ["罗马"],
        "那不勒斯": ["那不勒斯"],
        "布雷斯特": ["布雷斯特"],
        "斯特拉斯堡": ["斯特拉斯"],
        "朗斯": ["朗斯"],
        "南安普顿": ["南安普敦"],
        "圣旺红星": ["红星"],
        "阿斯顿维拉": ["维拉"],
    }
    
    def expand_name(name):
        """展开队名变体"""
        result = [name]
        
        # 检查是否是简称 -> 全称的映射（如 "巴萨" -> "巴塞罗那"）
        for full, shorts in short_names.items():
            if name in shorts:
                result.append(full)
                result.extend([s for s in shorts if s != name])
                break
        
        # 检查是否是全称 -> 简称的映射（如 "巴塞罗那" -> ["巴塞罗那", "巴萨"]）
        if name in short_names:
            result.extend(short_names[name])
        
        return list(set(result))
    
    home_variants = expand_name(home_normalized)
    away_variants = expand_name(away_normalized)
    
    # 尝试各种组合
    for h in home_variants:
        for a in away_variants:
            candidates = [f"{h} vs {a}", f"{h} - {a}"]
            for key in candidates:
                if key in odds_data:
                    return key
    
    # 模糊匹配
    for key in odds_data.keys():
        key_lower = key.lower()
        home_in_key = any(h.lower() in key_lower or h in key for h in home_variants)
        away_in_key = any(a.lower() in key_lower or a in key for a in away_variants)
        if home_in_key and away_in_key:
            return key
    
    return None


def get_real_odds(home_team: str, away_team: str, odds_data: dict) -> Optional[Tuple[float, float, float]]:
    """
    获取真实赔率（优先HAD，fallback到HHAD）
    
    返回: (home, draw, away) 或 None（无赔率）
    """
    if not odds_data:
        return None
    
    key = match_odds_key(home_team, away_team, odds_data)
    if not key:
        return None
    
    odds = odds_data.get(key, {})
    if not odds:
        return None
    
    home = odds.get('home')
    draw = odds.get('draw')
    away = odds.get('away')
    
    if home and draw and away:
        return (home, draw, away)
    
    return None


def get_hhad_odds(home_team: str, away_team: str, odds_data: dict) -> Optional[Tuple[float, float, float, float]]:
    """
    获取让球赔率(HHAD)
    
    返回: (hhad_home, hhad_draw, hhad_away, handicap) 或 None（无HHAD赔率）
    """
    if not odds_data:
        return None
    
    key = match_odds_key(home_team, away_team, odds_data)
    if not key:
        return None
    
    odds = odds_data.get(key, {})
    if not odds:
        return None
    
    hhad_home = odds.get('hhad_home')
    hhad_draw = odds.get('hhad_draw')
    hhad_away = odds.get('hhad_away')
    handicap = odds.get('hhad_handicap')
    
    if hhad_home and hhad_draw and hhad_away:
        return (hhad_home, hhad_draw, hhad_away, handicap)
    
    return None


def get_odds_source(home_team: str, away_team: str, odds_data: dict) -> Optional[str]:
    """
    获取赔率来源标记
    
    返回: "had" / "hhad" / None
    """
    if not odds_data:
        return None
    
    key = match_odds_key(home_team, away_team, odds_data)
    if not key:
        return None
    
    odds = odds_data.get(key, {})
    return odds.get('odds_source')


def get_match_num(home_team: str, away_team: str, odds_data: dict) -> Optional[str]:
    """获取体彩网官方编号matchNum，如"3005"转"周三005"格式"""
    if not odds_data:
        return None
    
    key = match_odds_key(home_team, away_team, odds_data)
    if not key:
        return None
    
    odds = odds_data.get(key, {})
    match_num_raw = odds.get('matchNum', '')
    
    # 转换为字符串处理
    match_num = str(match_num_raw) if match_num_raw else ''
    
    if match_num and len(match_num) == 4:
        try:
            weekday_num = int(match_num[0])  # 1-7对应周一到周日
            seq_num = match_num[1:4]  # 后3位序号
            weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
            if 1 <= weekday_num <= 7:
                return f"周{weekday_names[weekday_num - 1]}{seq_num}"
        except (ValueError, IndexError):
            pass
    
    return None


def match_num_to_display(match_num: str) -> str:
    """将matchNum（如"3005"）转换为显示格式（如"周三005"）"""
    if not match_num:
        return ""
    # 转换为字符串处理
    match_num = str(match_num)
    if len(match_num) != 4:
        return ""
    try:
        weekday_num = int(match_num[0])
        seq_num = match_num[1:4]
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        if 1 <= weekday_num <= 7:
            return f"周{weekday_names[weekday_num - 1]}{seq_num}"
    except (ValueError, IndexError):
        pass
    return ""


# ========== 泊松模型 ==========

def poisson_probability(mean: float, k: int) -> float:
    """计算泊松分布概率 P(X=k)"""
    return (mean ** k * math.exp(-mean)) / math.factorial(k)


def calculate_poisson_probs(home_lambda: float, away_lambda: float, max_goals: int = 4) -> Tuple[float, float, float, dict]:
    """计算泊松分布下的比分概率矩阵"""
    matrix = {}
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            p = poisson_probability(home_lambda, home_goals) * poisson_probability(away_lambda, away_goals)
            matrix[(home_goals, away_goals)] = p
    
    # 计算胜平负概率
    home_win = sum(matrix[(h, a)] for h in range(1, max_goals+1) for a in range(max_goals+1))
    draw = sum(matrix[(h, a)] for h in range(max_goals+1) for a in range(max_goals+1) if h == a)
    away_win = sum(matrix[(h, a)] for h in range(max_goals+1) for a in range(1, max_goals+1))
    
    # 归一化
    total_match = home_win + draw + away_win
    if total_match > 0:
        home_win /= total_match
        draw /= total_match
        away_win /= total_match
    
    return home_win, draw, away_win, matrix


def bayesian_adjustment(poisson_prob: float, market_prob: float, confidence: float = 0.7) -> float:
    """贝叶斯修正：综合泊松模型和市场概率"""
    return poisson_prob * confidence + market_prob * (1 - confidence)


def calculate_kelly(win_prob: float, odds: float, fraction: float = 0.5) -> float:
    """凯利公式计算最优投注比例"""
    b = odds - 1
    q = 1 - win_prob
    if b <= 0:
        return 0
    kelly = (b * win_prob - q) / b
    return max(0, kelly * fraction)


def calc_kelly_with_fallback(prob: float, odds: float) -> float:
    """
    计算半凯利指数，赔率为0时用概率反推隐含赔率
    隐含赔率 = 1 / 综合概率
    """
    if not prob or prob <= 0:
        return 0.0
    # 如果赔率有效（>1.01），用实际赔率
    if odds and odds > 1.01:
        b = odds - 1
    else:
        # 赔率缺失时，用综合概率反推隐含赔率
        implied_odds = 1.0 / prob
        b = implied_odds - 1
    q = 1 - prob
    if b > 0:
        kelly = (b * prob - q) / b
        return round(max(0, kelly * 0.5), 6)
    return 0.0


def get_odds_category(odds: float) -> str:
    """获取赔率区间分类"""
    if odds <= 1.50:
        return "低赔"
    elif odds <= 2.00:
        return "中低赔"
    elif odds <= 3.00:
        return "中赔"
    else:
        return "高赔"


def assess_risk(home_prob: float, draw_prob: float, away_prob: float, 
                odds_home: float, odds_draw: float, odds_away: float) -> str:
    """风险评估：高/中/低"""
    probs = [home_prob, draw_prob, away_prob]
    avg_prob = sum(probs) / 3
    variance = sum((p - avg_prob) ** 2 for p in probs) / 3
    
    kelly_home = home_prob - 1/odds_home
    kelly_draw = draw_prob - 1/odds_draw
    kelly_away = away_prob - 1/odds_away
    
    positive_kelly = sum(1 for k in [kelly_home, kelly_draw, kelly_away] if k > 0.05)
    
    if variance < 0.02 and positive_kelly <= 1:
        return "低"
    elif variance < 0.04 and positive_kelly <= 2:
        return "中"
    else:
        return "高"


def calculate_confidence(recommendation: str, rec_prob: float, 
                         odds_home: float, odds_draw: float, odds_away: float) -> Tuple[int, str, str]:
    """计算信心指数（1-5星）"""
    if recommendation == "主胜":
        rec_odds = odds_home
    elif recommendation == "平局":
        rec_odds = odds_draw
    else:
        rec_odds = odds_away
    
    odds_category = get_odds_category(rec_odds)
    
    # 5星（强烈推荐）
    if rec_prob >= 0.65 and rec_odds <= 1.25:
        return 5, "⭐⭐⭐⭐⭐", "低赔高压，命中率70%"
    if rec_prob >= 0.60 and rec_odds <= 1.30:
        return 5, "⭐⭐⭐⭐⭐", "高压低赔支持"
    
    # 4星（推荐）
    if rec_odds <= 1.40 and rec_prob >= 0.55:
        return 4, "⭐⭐⭐⭐", "强队方向合理"
    if rec_prob >= 0.60 and odds_category not in ["中赔"]:
        return 4, "⭐⭐⭐⭐", "高概率支持"
    
    # 3星（一般）
    if 0.45 <= rec_prob < 0.60:
        return 3, "⭐⭐⭐", "中概率参考"
    
    if recommendation == "平局":
        return 3, "⭐⭐⭐", "平局预测"
    
    # 2星（谨慎）
    if odds_category == "中赔":
        return 2, "⭐⭐", "中赔谨慎"
    
    if rec_odds <= 1.50 and rec_prob < 0.45:
        return 2, "⭐⭐", "低赔低概率"
    
    # 1星（不建议）
    if rec_odds > 3.00 and rec_prob < 0.50:
        return 1, "⭐", "高赔低概率"
    
    return 2, "⭐⭐", "默认谨慎"


# ========== 赔率反推xG ==========

def calculate_xg_from_odds(odds_home: float, odds_draw: float, odds_away: float) -> Tuple[float, float]:
    """
    从市场赔率反推xG值
    基于市场隐含概率推算双方进攻能力差异
    
    Args:
        odds_home: 主场赔率
        odds_draw: 平局赔率
        odds_away: 客场赔率
    
    Returns:
        (home_xg, away_xg, xg_source) - xG来源标记为"赔率反推"
    """
    # 计算市场隐含概率
    total_implied = (1/odds_home) + (1/odds_draw) + (1/odds_away)
    market_home = (1/odds_home) / total_implied
    market_away = (1/odds_away) / total_implied
    
    # 从市场概率推算xG
    # 原理：市场概率反映了双方实力差距，可转化为xG差异
    if market_home > 0.5:
        # 主场明显占优
        home_xg = 1.2 + (market_home - 0.5) * 2.0
        away_xg = 0.8 - (market_home - 0.5) * 0.8
    elif market_away > 0.5:
        # 客场明显占优
        away_xg = 1.2 + (market_away - 0.5) * 2.0
        home_xg = 0.8 - (market_away - 0.5) * 0.8
    else:
        # 势均力敌
        home_xg = 1.1
        away_xg = 1.0
    
    # 限制xG合理范围
    home_xg = max(0.5, min(2.5, home_xg))
    away_xg = max(0.3, min(2.0, away_xg))
    
    return home_xg, away_xg


def calculate_xg_from_hhad(hhad_home: float, hhad_draw: float, hhad_away: float, 
                            handicap: float) -> Tuple[float, float]:
    """
    从让球盘赔率(HHAD)反推xG值
    
    原理：HHAD赔率是让球后的胜平负概率，可以反推让球后的预期进球分布。
    例如：阿森纳让3球，HHAD: h=2.62(让球胜)/d=4.30(让球平)/a=1.94(让球负)
    这意味着：让球后阿森纳赢4+球概率=1/2.62≈38.2%，赢3球概率≈23.3%，赢2球内≈51.5%
    
    Args:
        hhad_home: 让球胜赔率（如阿森纳让3球赢4+球的赔率）
        hhad_draw: 让球平赔率（让3球后恰好赢3球的赔率）
        hhad_away: 让球负赔率（让3球后输球的赔率，即主队最多赢2球）
        handicap: 让球数（如-3表示主队让3球）
    
    Returns:
        (home_xg, away_xg) - 原始预期进球数
    """
    import math
    
    # 1. 从HHAD赔率反推让球后的隐含概率
    total_implied = (1/hhad_home) + (1/hhad_draw) + (1/hhad_away)
    implied_home = (1/hhad_home) / total_implied  # 让球后主胜概率
    implied_draw = (1/hhad_draw) / total_implied    # 让球后平局概率
    implied_away = (1/hhad_away) / total_implied    # 让球后客胜概率
    
    # 2. 让球盘赔率的含义
    # 假设主队让n球（handicap为-n）：
    # - HHAD胜：主队净胜n+1球以上
    # - HHAD平：主队净胜恰好n球
    # - HHAD负：主队净胜少于n球（即不胜）
    
    # 3. 用泊松分布拟合让球后的概率
    # 设让球后的主队预期进球为lambda_home，客队为lambda_away
    # P(主队净胜>=n+1) ≈ implied_home
    # P(主队净胜=n) ≈ implied_draw
    # P(主队净胜<=n-1) ≈ implied_away
    
    handicap_abs = abs(handicap) if handicap else 1.0
    handicap_int = max(1, round(handicap_abs))  # 让球数取整，至少为1
    
    # 简化模型：假设让球后双方实力比为 implied_home : implied_away
    # 使用二分法求解lambda值
    
    def poisson_prob(lambda_val, k):
        """泊松概率 P(X=k)"""
        return (lambda_val ** k * math.exp(-lambda_val)) / math.factorial(k) if k >= 0 else 0
    
    def solve_lambda():
        """求解让球后的lambda值"""
        # 假设客队平均进球约1.0（客场典型值）
        base_away_lambda = 1.0
        
        # 通过HHAD概率反推让球后主队进攻强度
        # implied_away ≈ P(主队净胜 <= handicap_int - 1) 
        #            ≈ P(主队进球 - 客队进球 <= handicap_int - 1)
        # 对于让球盘，通常 |handicap| >= 1
        
        # 使用数值方法求解
        best_lambda_home = 1.5
        best_lambda_away = base_away_lambda
        best_error = float('inf')
        
        for lambda_home_test in [0.5 + i * 0.1 for i in range(30)]:  # 0.5-3.5
            for lambda_away_test in [0.3 + i * 0.1 for i in range(25)]:  # 0.3-2.7
                # 计算让球后各种情况的概率
                # 让球后净胜>=handicap_int+1的概率
                prob_win = 0
                for h in range(handicap_int + 1, 10):
                    for a in range(10):
                        prob_win += poisson_prob(lambda_home_test, h) * poisson_prob(lambda_away_test, a)
                
                # 让球后净胜恰好=handicap_int的概率
                prob_draw = 0
                for a in range(10):
                    prob_draw += poisson_prob(lambda_home_test, handicap_int) * poisson_prob(lambda_away_test, a)
                
                # 让球后净胜<=handicap_int-1的概率
                prob_loss = 0
                for h in range(handicap_int):
                    for a in range(10):
                        prob_loss += poisson_prob(lambda_home_test, h) * poisson_prob(lambda_away_test, a)
                for a in range(handicap_int + 1, 10):
                    for h in range(handicap_int):
                        prob_loss += poisson_prob(lambda_home_test, h) * poisson_prob(lambda_away_test, a)
                
                # 计算误差
                total = prob_win + prob_draw + prob_loss
                if total < 0.95:  # 概率和太低，跳过
                    continue
                
                error = (abs(prob_win - implied_home) + 
                        abs(prob_draw - implied_draw) + 
                        abs(prob_loss - implied_away))
                
                if error < best_error:
                    best_error = error
                    best_lambda_home = lambda_home_test
                    best_lambda_away = lambda_away_test
        
        return best_lambda_home, best_lambda_away
    
    # 4. 求解让球后的lambda
    lambda_home_hhad, lambda_away_hhad = solve_lambda()
    
    # 5. 将让球后的lambda转换为原始lambda
    # 主队让n球，意味着原始预期主队进球 = 让球后预期 + n
    # 但由于让球盘通常用于实力悬殊的比赛，需要合理调整
    
    # 直接使用让球后的lambda作为主客队预期进球
    # 因为让球盘赔率已经包含了双方实力差距的信息
    home_xg = lambda_home_hhad
    away_xg = lambda_away_hhad
    
    # 限制xG合理范围
    home_xg = max(0.5, min(3.0, home_xg))
    away_xg = max(0.3, min(2.5, away_xg))
    
    return home_xg, away_xg


# ========== 比赛分析 ==========

def analyze_match(home_data: dict, away_data: dict, real_odds: Optional[Tuple[float, float, float]] = None,
                  use_odds_xg: bool = False,
                  hhad_odds: Optional[Tuple[float, float, float, float]] = None) -> Optional[dict]:
    """
    分析单场比赛
    无赔率则返回None（跳过该比赛）
    
    Args:
        home_data: 主队积分榜数据
        away_data: 客队积分榜数据
        real_odds: (home_odds, draw_odds, away_odds) - HAD赔率
        use_odds_xg: 是否使用赔率反推xG（False则使用积分榜数据）
        hhad_odds: (hhad_home, hhad_draw, hhad_away, handicap) - 让球赔率
    
    Returns:
        分析结果字典，包含xG来源标记、赔率来源标记
    """
    if real_odds is None and hhad_odds is None:
        return None
    
    # 决定使用哪种赔率
    use_hhad = False
    odds_home = odds_draw = odds_away = None
    
    if real_odds is not None:
        odds_home, odds_draw, odds_away = real_odds
    elif hhad_odds is not None:
        # HAD为空但HHAD存在，使用HHAD赔率
        hhad_home, hhad_draw, hhad_away, handicap = hhad_odds
        use_hhad = True
        # HHAD赔率作为分析用赔率
        odds_home = hhad_home
        odds_draw = hhad_draw
        odds_away = hhad_away
    
    if odds_home is None or odds_draw is None or odds_away is None:
        return None
    
    # 计算xG
    if use_odds_xg:
        if use_hhad and hhad_odds is not None:
            # 使用HHAD赔率反推xG
            home_xg, away_xg = calculate_xg_from_hhad(hhad_home, hhad_draw, hhad_away, handicap)
            xg_source = "让球盘反推"
        else:
            # 使用HAD赔率反推xG
            home_xg, away_xg = calculate_xg_from_odds(odds_home, odds_draw, odds_away)
            xg_source = "赔率反推"
        
        # 泊松分布计算（使用反推的xG）
        poisson_home, poisson_draw, poisson_away, score_matrix = calculate_poisson_probs(home_xg, away_xg)
    else:
        # 使用积分榜数据计算xG
        home_games = home_data.get('played', 10) or 10
        away_games = away_data.get('played', 10) or 10
        
        home_attack = home_data.get('gf', 15) / home_games if home_games > 0 else 1.5
        home_defense = home_data.get('ga', 10) / home_games if home_games > 0 else 1.2
        away_attack = away_data.get('gf', 12) / away_games if away_games > 0 else 1.2
        away_defense = away_data.get('ga', 14) / away_games if away_games > 0 else 1.5
        
        # 计算预期进球 (xG)
        home_xg = home_attack * 0.4 + away_defense * 0.4 + 0.2
        away_xg = away_attack * 0.4 + home_defense * 0.4 + 0.2
        
        # 排名调整
        home_rank = home_data.get('rank', 10)
        away_rank = away_data.get('rank', 10)
        home_rank_factor = 1 + (10 - min(home_rank, 10)) * 0.02
        away_rank_factor = 1 + (10 - min(away_rank, 10)) * 0.02
        home_xg *= home_rank_factor
        away_xg *= away_rank_factor
        
        xg_source = "积分榜"
        
        # 泊松分布计算
        poisson_home, poisson_draw, poisson_away, score_matrix = calculate_poisson_probs(home_xg, away_xg)
    
    # 市场隐含概率（从用于分析的赔率计算）
    total_implied = (1/odds_home) + (1/odds_draw) + (1/odds_away)
    market_home = (1/odds_home) / total_implied
    market_draw = (1/odds_draw) / total_implied
    market_away = (1/odds_away) / total_implied
    
    # 贝叶斯修正 (70%泊松 + 30%市场)
    final_home = bayesian_adjustment(poisson_home, market_home)
    final_draw = bayesian_adjustment(poisson_draw, market_draw)
    final_away = bayesian_adjustment(poisson_away, market_away)
    
    # 归一化
    total = final_home + final_draw + final_away
    final_home /= total
    final_draw /= total
    final_away /= total
    
    # 推荐方向（后续value_bet会覆盖为EV最高方向）
    if final_home >= final_away and final_home >= final_draw:
        recommendation = "主胜"
        rec_prob = final_home
    elif final_away >= final_home and final_away >= final_draw:
        recommendation = "客胜"
        rec_prob = final_away
    else:
        recommendation = "平局"
        rec_prob = final_draw
    
    # 风险评估（使用HAD赔率，如果存在的话）
    eval_odds_home = odds_home if not use_hhad and real_odds else odds_home
    eval_odds_draw = odds_draw if not use_hhad and real_odds else odds_draw
    eval_odds_away = odds_away if not use_hhad and real_odds else odds_away
    risk = assess_risk(final_home, final_draw, final_away, eval_odds_home, eval_odds_draw, eval_odds_away)
    
    # 在信心说明中标注xG来源和赔率来源
    odds_source_str = "让球盘" if use_hhad else "胜平负"
    reason_with_source = f"（{xg_source}，赔率来源:{odds_source_str}）"
    
    # 参考比分
    top_scores = sorted(score_matrix.items(), key=lambda x: -x[1])[:5]
    reference_scores = [f"{h}:{a}" for (h, a), _ in top_scores]
    
    # 推荐概率标注
    rec_prob_display = f"{rec_prob*100:.1f}%"
    
    home_name_zh = home_data.get('name_zh', home_data.get('name', ''))
    away_name_zh = away_data.get('name_zh', away_data.get('name', ''))
    
    # 返回结果（EV字段初始为0，后续由value_bet覆盖）
    result = {
        'home_name_zh': home_name_zh,
        'away_name_zh': away_name_zh,
        'home_xg': home_xg,
        'away_xg': away_xg,
        'xg_source': xg_source,  # xG来源标记
        'odds_source': 'hhad' if use_hhad else 'had',  # 赔率来源
        'poisson_home': poisson_home,
        'poisson_draw': poisson_draw,
        'poisson_away': poisson_away,
        'final_home': final_home,
        'final_draw': final_draw,
        'final_away': final_away,
        'odds_home': odds_home,
        'odds_draw': odds_draw,
        'odds_away': odds_away,
        'recommendation': recommendation,
        'rec_prob': rec_prob,
        'rec_prob_display': rec_prob_display,
        'risk': risk,
        'confidence_reason': reason_with_source,  # 包含xG来源和赔率来源
        'reference_scores': reference_scores,
        # EV相关字段（初始为0，后续由value_bet覆盖）
        'ev_win': 0.0,
        'ev_draw': 0.0,
        'ev_loss': 0.0,
        'ev_value': 0.0,
        'best_direction': '',
        'best_direction_cn': '',
        'avg_margin': 0.0,
    }
    
    # 添加HHAD相关字段（无论HAD是否存在都保存，供V4冷门预警双盘口lambda用）
    if hhad_odds is not None:
        result['hhad_home'] = hhad_odds[0]
        result['hhad_draw'] = hhad_odds[1]
        result['hhad_away'] = hhad_odds[2]
        result['hhad_handicap'] = hhad_odds[3]
    
    return result


# ========== 编号生成 ==========

def generate_match_id(league_name: str, match_time: datetime) -> str:
    """生成比赛编号：周X001、周X002..."""
    weekday = match_time.weekday()  # 0=周一, 6=周日
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday_str = weekday_names[weekday]
    
    # 从全局计数器获取序号（简化处理）
    return f"周{weekday_str}001"  # 实际由调用方维护序号


# ========== 交叉验证（调用football-lottery-analysis-expert的match_analyzer） ==========

# Skills目录
SKILL_MATCH_ANALYZER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".skills", "skill_football-lottery-analysis-expert", "scripts", "match_analyzer.py")

def call_skill_poisson(home_team_en: str, away_team_en: str, league_code: str = "en.1") -> Optional[dict]:
    """
    调用 football-lottery-analysis-expert 的 match_analyzer.py 获取泊松概率
    
    Returns:
        {
            "home_win": 0.45,
            "draw": 0.30,
            "away_win": 0.25,
            "power_rating": {"home": 85, "away": 80},
            "data_quality": "high"
        }
    """
    import subprocess
    import json
    import tempfile
    
    try:
        # 先获取比赛数据
        data_fetcher = os.path.join(os.path.dirname(SKILL_MATCH_ANALYZER), "data_fetcher.py")
        
        # 获取联赛近几轮比赛数据
        cmd_fetch = [sys.executable, data_fetcher, "--action", "league_matches", "--league", league_code]
        result_fetch = subprocess.run(cmd_fetch, capture_output=True, text=True, timeout=60)
        
        if result_fetch.returncode != 0:
            return None
        
        # 写入临时文件作为input
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(json.loads(result_fetch.stdout), f)
            input_file = f.name
        
        try:
            # 调用poisson分析
            cmd_analyze = [
                sys.executable, SKILL_MATCH_ANALYZER,
                "--action", "poisson",
                "--input", input_file,
                "--team1", home_team_en,
                "--team2", away_team_en
            ]
            result_analyze = subprocess.run(cmd_analyze, capture_output=True, text=True, timeout=60)
            
            if result_analyze.returncode == 0 and result_analyze.stdout:
                return json.loads(result_analyze.stdout)
        finally:
            os.unlink(input_file)
    
    except Exception as e:
        log(f"交叉验证调用失败: {e}")
    
    return None


def cross_validate_match(home_team: str, away_team: str, league: str, 
                        home_rank: int, away_rank: int,
                        our_poisson_home: float, our_poisson_draw: float, our_poisson_away: float,
                        confidence_stars: float) -> dict:
    """
    交叉验证：对比我们的泊松结果与skill的泊松结果
    
    Args:
        our_poisson_*: 我们的泊松概率
        confidence_stars: 当前信心星级
    
    Returns:
        {
            "divergence_detected": True/False,
            "偏差": {"home": 0.05, "draw": -0.03, "away": -0.02},
            "max_deviation": 0.05,
            "adjusted_stars": 4.0,  # 调整后星级
            "model_divergence": True/False,  # 是否模型分歧
            "warning": "⚠️ 模型分歧"
        }
    """
    from fundamental_analysis import CROSS_VALIDATE_ENABLED
    
    result = {
        "divergence_detected": False,
        "偏差": {"home": 0.0, "draw": 0.0, "away": 0.0},
        "max_deviation": 0.0,
        "adjusted_stars": confidence_stars,
        "model_divergence": False,
        "warning": "",
        "skill_result": None
    }
    
    # 只对4星+场次做交叉验证
    if confidence_stars < 4.0:
        return result
    
    if not CROSS_VALIDATE_ENABLED:
        return result
    
    # 获取队名英文
    home_team_en = TEAM_ALIASES.get(home_team, home_team)
    away_team_en = TEAM_ALIASES.get(away_team, away_team)
    
    # 获取联赛代码
    league_code_map = {
        "英超": "en.1", "西甲": "es.1", "德甲": "de.1", 
        "意甲": "it.1", "法甲": "fr.1", "葡超": "pt.1", "荷甲": "nl.1"
    }
    league_code = league_code_map.get(league, "en.1")
    
    # 调用skill的泊松分析
    skill_result = call_skill_poisson(home_team_en, away_team_en, league_code)
    
    if skill_result is None:
        return result
    
    result["skill_result"] = skill_result
    
    # 对比概率
    skill_home = skill_result.get("home_win", 0)
    skill_draw = skill_result.get("draw", 0)
    skill_away = skill_result.get("away_win", 0)
    
    # 计算偏差
    dev_home = our_poisson_home - skill_home
    dev_draw = our_poisson_draw - skill_draw
    dev_away = our_poisson_away - skill_away
    
    result["偏差"] = {"home": dev_home, "draw": dev_draw, "away": dev_away}
    result["max_deviation"] = max(abs(dev_home), abs(dev_draw), abs(dev_away))
    
    # 偏差>15%视为分歧
    if result["max_deviation"] > 0.15:
        result["divergence_detected"] = True
        result["model_divergence"] = True
        
        # 信心指数降0.5星
        result["adjusted_stars"] = max(1.0, confidence_stars - 0.5)
        result["warning"] = "⚠️ 模型分歧"
    
    return result


# ========== 基本面数据自动获取（整合football-lottery-analysis-expert） ==========

# Skills目录
SKILL_DATA_FETCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".skills", "skill_football-lottery-analysis-expert", "scripts", "data_fetcher.py")

# 积分榜缓存
_standings_cache = {}


def is_openfootball_supported(league_name: str) -> bool:
    """检查联赛是否被openfootball支持"""
    of_code = OPENFOOTBALL_LEAGUE_MAP.get(league_name, "")
    return of_code in OPENFOOTBALL_SUPPORTED


def call_data_fetcher_standings(league_name: str) -> Optional[dict]:
    """调用 data_fetcher 获取积分榜数据"""
    import subprocess
    import json
    
    of_code = OPENFOOTBALL_LEAGUE_MAP.get(league_name, "")
    if not of_code:
        return None
    
    cache_key = f"standings_{of_code}"
    if cache_key in _standings_cache:
        cached_data, cached_time = _standings_cache[cache_key]
        if time.time() - cached_time < 300:
            return cached_data
    
    try:
        cmd = [sys.executable, SKILL_DATA_FETCHER, "--action", "league_matches", "--league", of_code]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0 or not result.stdout:
            return None
        
        data = json.loads(result.stdout)
        matches = data.get("matches", [])
        
        team_stats = defaultdict(lambda: {"won": 0, "draw": 0, "lost": 0, "gf": 0, "ga": 0, "played": 0})
        
        for match in matches:
            score = match.get("score", {})
            home_score = score.get("home")
            away_score = score.get("away")
            if home_score is None or away_score is None:
                continue
            
            home_team = match.get("home_team", {}).get("name", "")
            away_team = match.get("away_team", {}).get("name", "")
            
            team_stats[home_team]["played"] += 1
            team_stats[away_team]["played"] += 1
            team_stats[home_team]["gf"] += home_score
            team_stats[home_team]["ga"] += away_score
            team_stats[away_team]["gf"] += away_score
            team_stats[away_team]["ga"] += home_score
            
            if home_score > away_score:
                team_stats[home_team]["won"] += 1
                team_stats[away_team]["lost"] += 1
            elif home_score < away_score:
                team_stats[home_team]["lost"] += 1
                team_stats[away_team]["won"] += 1
            else:
                team_stats[home_team]["draw"] += 1
                team_stats[away_team]["draw"] += 1
        
        standings = []
        for team, stats in team_stats.items():
            points = stats["won"] * 3 + stats["draw"]
            gd = stats["gf"] - stats["ga"]
            standings.append({
                "team_en": team, "team_cn": translate_team_to_cn(team),
                "played": stats["played"], "won": stats["won"], "draw": stats["draw"], "lost": stats["lost"],
                "gf": stats["gf"], "ga": stats["ga"], "gd": gd, "points": points
            })
        
        standings.sort(key=lambda x: (-x["points"], -x["gd"], -x["gf"]))
        for i, s in enumerate(standings):
            s["rank"] = i + 1
        
        result_data = {"standings": standings, "data_freshness": data.get("data_freshness", {}), "league_code": of_code}
        _standings_cache[cache_key] = (result_data, time.time())
        return result_data
    
    except Exception as e:
        log(f"获取{league_name}积分榜失败: {e}")
        return None


def translate_team_to_cn(team_en: str) -> str:
    """将英文队名翻译为中文"""
    if team_en in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[team_en]
    for en, cn in TEAM_NAME_MAP.items():
        en_base = en.replace(" FC", "").replace(" AFC", "").replace(" SC", "")
        team_base = team_en.replace(" FC", "").replace(" AFC", "").replace(" SC", "")
        if en_base.lower() == team_base.lower():
            return cn
    return team_en


def get_team_standings(team_name: str, league_name: str) -> Optional[dict]:
    """获取球队在联赛中的排名信息"""
    standings_data = call_data_fetcher_standings(league_name)
    if not standings_data:
        return None
    for s in standings_data.get("standings", []):
        if (s.get("team_en", "").lower() == team_name.lower() or
            s.get("team_cn", "") == team_name or
            team_name in s.get("team_en", "") or team_name in s.get("team_cn", "")):
            return s
    return None


def call_data_fetcher_h2h(home_team_en: str, away_team_en: str, league_code: str, seasons: int = 3) -> Optional[dict]:
    """调用 data_fetcher 获取交锋记录"""
    import subprocess
    import json
    
    try:
        start_year = datetime.now().year - seasons
        start_season = f"{start_year}-{str(start_year + 1)[-2:]}"
        
        cmd = [sys.executable, SKILL_DATA_FETCHER, "--action", "multi_h2h", 
               "--league", league_code, "--team1", home_team_en, "--team2", away_team_en, "--start-season", start_season]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0 or not result.stdout:
            return None
        
        data = json.loads(result.stdout)
        matches = data.get("matches", [])
        
        h2h_list, home_wins, draws, away_wins = [], 0, 0, 0
        home_goals, away_goals = 0, 0
        
        for match in matches:
            score = match.get("score", {})
            home_score, away_score = score.get("home"), score.get("away")
            if home_score is None or away_score is None:
                continue
            
            h2h_list.append({
                "date": match.get("date", ""),
                "home": match.get("home_team", {}).get("name", ""),
                "away": match.get("away_team", {}).get("name", ""),
                "score": f"{home_score}-{away_score}"
            })
            home_goals += home_score
            away_goals += away_score
            
            if home_score > away_score:
                home_wins += 1
            elif home_score < away_score:
                away_wins += 1
            else:
                draws += 1
        
        return {"h2h": h2h_list, "stats": {"home_wins": home_wins, "draws": draws, "away_wins": away_wins,
               "home_goals": home_goals, "away_goals": away_goals, "total": len(h2h_list)},
                "data_freshness": data.get("data_freshness", {})}
    
    except Exception as e:
        log(f"获取交锋记录失败: {e}")
        return None


def call_data_fetcher_form(team_en: str, league_code: str, recent: int = 6) -> Optional[dict]:
    """获取球队近期战绩（form）"""
    import subprocess
    import json
    
    try:
        cmd = [sys.executable, SKILL_DATA_FETCHER, "--action", "league_matches", "--league", league_code]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0 or not result.stdout:
            return None
        
        data = json.loads(result.stdout)
        matches = data.get("matches", [])
        
        team_matches = []
        for match in matches:
            home_team = match.get("home_team", {}).get("name", "")
            away_team = match.get("away_team", {}).get("name", "")
            
            if home_team != team_en and away_team != team_en:
                continue
            
            score = match.get("score", {})
            home_score, away_score = score.get("home"), score.get("away")
            if home_score is None or away_score is None:
                continue
            
            is_home = home_team == team_en
            team_matches.append({
                "date": match.get("date", ""), "is_home": is_home,
                "goals_for": home_score if is_home else away_score,
                "goals_against": away_score if is_home else home_score
            })
        
        team_matches.sort(key=lambda x: x["date"], reverse=True)
        recent_matches = team_matches[:recent]
        
        form_str, wins, draws, losses = "", 0, 0, 0
        gf, ga = 0, 0
        
        for m in recent_matches:
            gf += m["goals_for"]
            ga += m["goals_against"]
            if m["goals_for"] > m["goals_against"]:
                form_str += "W"; wins += 1
            elif m["goals_for"] < m["goals_against"]:
                form_str += "L"; losses += 1
            else:
                form_str += "D"; draws += 1
        
        return {"form": form_str, "recent_matches": recent_matches,
                "stats": {"wins": wins, "draws": draws, "losses": losses, "gf": gf, "ga": ga}}
    except:
        return None


def call_skill_power_rating(home_team_en: str, away_team_en: str, league_code: str) -> Optional[dict]:
    """调用 match_analyzer.py 获取实力评分"""
    import subprocess
    import json
    import tempfile
    
    try:
        data_fetcher = os.path.join(os.path.dirname(SKILL_MATCH_ANALYZER), "data_fetcher.py")
        cmd_fetch = [sys.executable, data_fetcher, "--action", "league_matches", "--league", league_code]
        result_fetch = subprocess.run(cmd_fetch, capture_output=True, text=True, timeout=60)
        
        if result_fetch.returncode != 0:
            return None
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(json.loads(result_fetch.stdout), f)
            input_file = f.name
        
        try:
            cmd_home = [sys.executable, SKILL_MATCH_ANALYZER, "--action", "power_rating", "--input", input_file, "--team", home_team_en]
            cmd_away = [sys.executable, SKILL_MATCH_ANALYZER, "--action", "power_rating", "--input", input_file, "--team", away_team_en]
            
            result_home = subprocess.run(cmd_home, capture_output=True, text=True, timeout=60)
            result_away = subprocess.run(cmd_away, capture_output=True, text=True, timeout=60)
            
            if result_home.returncode == 0 and result_away.returncode == 0:
                home_data = json.loads(result_home.stdout)
                away_data = json.loads(result_away.stdout)
                return {"home_power": home_data.get("power_rating", 70), "away_power": away_data.get("power_rating", 70),
                        "data_quality": home_data.get("data_quality", "medium")}
        finally:
            os.unlink(input_file)
    except:
        return None


def get_auto_fundamental_data(home_team: str, away_team: str, league: str, confidence_stars: float = 3.0) -> dict:
    """自动获取基本面数据（积分榜、form、交锋、实力评分）"""
    result = {"standings_data": None, "home_standings": None, "away_standings": None,
              "home_form": None, "away_form": None, "h2h": None, "power_rating": None,
              "is_openfootball": is_openfootball_supported(league), "data_freshness": {}}
    
    of_code = OPENFOOTBALL_LEAGUE_MAP.get(league, "")
    if not of_code:
        result["is_openfootball"] = False
        return result
    
    standings_data = call_data_fetcher_standings(league)
    result["standings_data"] = standings_data
    
    if standings_data:
        result["data_freshness"] = standings_data.get("data_freshness", {})
        result["home_standings"] = get_team_standings(home_team, league)
        result["away_standings"] = get_team_standings(away_team, league)
        
        if result["home_standings"]:
            home_form_data = call_data_fetcher_form(result["home_standings"].get("team_en", ""), of_code)
            if home_form_data:
                result["home_form"] = home_form_data.get("form", "")
        
        if result["away_standings"]:
            away_form_data = call_data_fetcher_form(result["away_standings"].get("team_en", ""), of_code)
            if away_form_data:
                result["away_form"] = away_form_data.get("form", "")
        
        if confidence_stars >= 4.0:
            home_team_en = TEAM_ALIASES.get(home_team, home_team)
            away_team_en = TEAM_ALIASES.get(away_team, away_team)
            result["h2h"] = call_data_fetcher_h2h(home_team_en, away_team_en, of_code)
            result["power_rating"] = call_skill_power_rating(home_team_en, away_team_en, of_code)
    
    freshness = result["data_freshness"]
    if freshness.get("warning"):
        log(f"⚠️ {league}数据延迟: {freshness.get('warning')}")
    
    return result


def use_auto_fundamental_for_analysis(auto_data: dict, home_rank: int = 0, away_rank: int = 0) -> dict:
    """使用自动获取的基本面数据辅助分析"""
    result = {"home_rank": home_rank, "away_rank": away_rank, "home_points": 0, "away_points": 0,
              "home_form": None, "away_form": None, "h2h_summary": None, "power_diff": 0, "data_used": False}
    
    if not auto_data.get("is_openfootball"):
        return result
    
    home_standings = auto_data.get("home_standings")
    away_standings = auto_data.get("away_standings")
    
    if home_standings:
        result["home_rank"] = home_standings.get("rank", home_rank)
        result["home_points"] = home_standings.get("points", 0)
        result["data_used"] = True
    
    if away_standings:
        result["away_rank"] = away_standings.get("rank", away_rank)
        result["away_points"] = away_standings.get("points", 0)
        result["data_used"] = True
    
    result["home_form"] = auto_data.get("home_form")
    result["away_form"] = auto_data.get("away_form")
    
    h2h = auto_data.get("h2h")
    if h2h and h2h.get("stats") and h2h["stats"].get("total", 0) > 0:
        stats = h2h["stats"]
        result["h2h_summary"] = f"{stats.get('home_wins', 0)}胜{stats.get('draws', 0)}平{stats.get('away_wins', 0)}负"
    
    power = auto_data.get("power_rating")
    if power:
        result["power_diff"] = power.get("home_power", 70) - power.get("away_power", 70)
        result["data_used"] = True
    
    return result


# ========== 输出 ==========

def format_result_for_output(match_info: dict, analysis: dict, seq_num: int, league_name: str, 
                            match_num_display: str = None,
                            fundamental_data: dict = None,
                            tactical_data: dict = None,
                            qtx_home_data: dict = None,
                            qtx_away_data: dict = None) -> dict:
    """格式化输出结果
    优先使用体彩网官方编号(match_num_display)，否则根据开赛时间计算编号
    
    Args:
        match_info: 比赛信息
        analysis: 分析结果
        seq_num: 序号
        league_name: 联赛名称
        match_num_display: 体彩网官方编号
        fundamental_data: 基本面分析数据（可选）
        tactical_data: 战术分析数据（可选）
        qtx_home_data: qtx主队积分榜数据（可选，包含场均进球/失球）
        qtx_away_data: qtx客队积分榜数据（可选）
    """
    # 如果有体彩网官方编号，优先使用
    if match_num_display:
        match_id = match_num_display
    else:
        # Fallback：根据开赛时间计算编号
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        match_dt = datetime.strptime(match_info['match_time_str'], "%Y-%m-%d %H:%M")
        weekday_str = weekday_names[match_dt.weekday()]
        match_id = f"周{weekday_str}{seq_num:03d}"
    
    # 推荐方向优先使用EV最佳方向，其次使用概率最高方向
    best_dir = analysis.get('best_direction_cn', '')
    if best_dir:
        display_direction = best_dir
    else:
        display_direction = analysis.get('recommendation', '')
    
    result = {
        "编号": match_id,
        "联赛": league_name,
        "开赛时间": match_info['match_time_str'],
        "主队": analysis['home_name_zh'],
        "客队": analysis['away_name_zh'],
        "推荐方向": display_direction,
        "推荐概率": analysis['rec_prob_display'],
        "EV": f"{analysis.get('ev_value', 0)*100:.1f}%" if analysis.get('ev_value', 0) > 0 else "-",
        "EV_主胜": f"{analysis.get('ev_win', 0)*100:.1f}%" if analysis.get('ev_win', 0) > 0 else "-",
        "EV_平局": f"{analysis.get('ev_draw', 0)*100:.1f}%" if analysis.get('ev_draw', 0) > 0 else "-",
        "EV_客胜": f"{analysis.get('ev_loss', 0)*100:.1f}%" if analysis.get('ev_loss', 0) > 0 else "-",
        "EV说明": analysis['confidence_reason'],
        "xG来源": analysis['xg_source'],  # xG来源列
        "参考比分": "/".join(analysis['reference_scores']),
        # 赔率和泊松概率仅用于内部计算，不再输出
        "综合_主胜": f"{analysis['final_home']*100:.1f}%",
        "综合_平局": f"{analysis['final_draw']*100:.1f}%",
        "综合_客胜": f"{analysis['final_away']*100:.1f}%",
        # HHAD让球盘字段（供数据库存储和V4冷门预警）
        "hhad_home": analysis.get('hhad_home'),
        "hhad_draw": analysis.get('hhad_draw'),
        "hhad_away": analysis.get('hhad_away'),
        "hhad_handicap": analysis.get('hhad_handicap'),
        "odds_source": analysis.get('odds_source', ''),
    }
    
    # 添加基本面增强列（如果可用）
    if fundamental_data and HAS_FUNDAMENTAL:
        # 风险预警列
        result["风险预警"] = fundamental_data.get("risk_warning", "")
        
        # 伤停摘要（简短）
        injury_info = fundamental_data.get("injury_info", {})
        if injury_info.get("has_data"):
            result["伤停信息"] = injury_info.get("summary", "")
        else:
            result["伤停信息"] = ""
        
        # 战意评估（简短）
        motivation = fundamental_data.get("motivation", {})
        if motivation.get("derby_match"):
            result["战意分析"] = "🔥德比战"
        elif motivation.get("must_win", {}).get("home"):
            result["战意分析"] = "💪主队必胜"
        elif motivation.get("must_win", {}).get("away"):
            result["战意分析"] = "💪客队必胜"
        else:
            summary = motivation.get("summary", "")
            result["战意分析"] = summary[:20] if summary else ""
        
        # 冷门风险
        cold_risk = fundamental_data.get("cold_risk", {})
        cold_risk_val = cold_risk.get("cold_risk", "")
        result["冷门风险"] = cold_risk_val
        
        # 诱盘检测
        trap_odds = fundamental_data.get("trap_odds", {})
        if trap_odds.get("is_trap"):
            result["诱盘预警"] = f"🎯{trap_odds.get('trap_type', '疑似')}"
        else:
            result["诱盘预警"] = ""
        
        # 基本面摘要
        result["基本面摘要"] = format_fundamental_summary(fundamental_data)
    else:
        result["风险预警"] = ""
        result["伤停信息"] = ""
        result["战意分析"] = ""
        result["冷门风险"] = ""
        result["诱盘预警"] = ""
        result["基本面摘要"] = ""
    
    # 添加战术分析（如果可用）
    if tactical_data and HAS_FUNDAMENTAL:
        result["战术阵型"] = tactical_data.get("formation", "")
        result["战术提示"] = tactical_data.get("summary", "")[:50] if tactical_data.get("summary") else ""
    else:
        result["战术阵型"] = ""
        result["战术提示"] = ""
    
    # 添加qtx积分榜数据（用于LGBM模型新特征）
    if qtx_home_data:
        result["home_points"] = qtx_home_data.get('points', 0)
        result["home_avg_goals"] = qtx_home_data.get('avg_gf', 0)
        result["home_avg_conceded"] = qtx_home_data.get('avg_ga', 0)
    else:
        result["home_points"] = 0
        result["home_avg_goals"] = 0
        result["home_avg_conceded"] = 0
    
    if qtx_away_data:
        result["away_points"] = qtx_away_data.get('points', 0)
        result["away_avg_goals"] = qtx_away_data.get('avg_gf', 0)
        result["away_avg_conceded"] = qtx_away_data.get('avg_ga', 0)
    else:
        result["away_points"] = 0
        result["away_avg_goals"] = 0
        result["away_avg_conceded"] = 0
    
    return result


def save_to_excel(results: List[dict], filepath: str):
    """保存结果到Excel"""
    if not results:
        log("⚠️ 无数据，跳过Excel生成")
        return
    
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    except ImportError:
        log("⚠️ openpyxl未安装，跳过Excel生成")
        return
    
    # 移除胜赔、平赔、负赔、泊松_主胜、泊松_平局、泊松_客胜列（仅保留内部计算）
    headers = [
        "编号", "联赛", "开赛时间", "主队", "客队",
        "推荐方向", "推荐概率", "EV", "EV_主胜", "EV_平局", "EV_客胜", "EV说明", "xG来源",
        "参考比分",
        "综合_主胜", "综合_平局", "综合_客胜",
        "冷门预警"
    ]
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "竞彩日报"
    
    # 表头样式
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, size=11, color="FFFFFF")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    
    # 写表头
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    
    # 写数据
    direction_colors = {
        "主胜": "FFEB9C",  # 浅黄
        "平局": "C6EFCE",  # 浅绿
        "客胜": "FFC7CE",  # 浅红
    }
    
    for row_idx, row_data in enumerate(results, 2):
        direction = row_data.get("推荐方向", "")
        direction_fill = PatternFill(
            start_color=direction_colors.get(direction, "FFFFFF"),
            end_color=direction_colors.get(direction, "FFFFFF"),
            fill_type="solid"
        )
        
        for col, header in enumerate(headers, 1):
            value = row_data.get(header, '')
            cell = ws.cell(row=row_idx, column=col, value=value)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border
            
            # 推荐方向列高亮
            if header == "推荐方向":
                cell.fill = direction_fill
                cell.font = Font(bold=True)
    
    # 自动列宽
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except:
                pass
        ws.column_dimensions[col_letter].width = min(max_length + 4, 22)
    
    # 冻结首行
    ws.freeze_panes = 'A2'
    
    wb.save(filepath)
    log(f"✓ Excel已保存: {filepath}")


def save_to_csv(results: List[dict], filepath: str):
    """保存结果到CSV"""
    if not results:
        return
    
    # 移除胜赔、平赔、负赔、泊松_主胜、泊松_平局、泊松_客胜列（仅保留内部计算）
    headers = [
        "编号", "联赛", "开赛时间", "主队", "客队",
        "推荐方向", "推荐概率", "EV", "EV_主胜", "EV_平局", "EV_客胜", "EV说明", "xG来源",
        "参考比分",
        "综合_主胜", "综合_平局", "综合_客胜",
        "百家初盘胜", "百家初盘平", "百家初盘负",
        "百家最新胜", "百家最新平", "百家最新负"
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    
    log(f"✓ CSV已保存: {filepath}")


def save_to_database(results: List[dict], date_str: str, dry_run: bool = False):
    """
    将预测结果写入SQLite数据库的poisson_predictions表
    
    Args:
        results: 预测结果列表
        date_str: 预测日期 (YYYY-MM-DD格式)
        dry_run: 是否仅测试不实际写入
    """
    if not results:
        log("⚠️ 无数据，跳过数据库写入")
        return
    
    try:
        import sqlite3
        
        # 确保数据库目录存在
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 确保表存在（如果不存在则创建）
        # 增加HHAD让球赔率字段和odds_source赔率来源字段
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS poisson_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                date TEXT,
                exec_time TEXT,
                league TEXT,
                home_team TEXT,
                away_team TEXT,
                kickoff_time TEXT,
                prediction TEXT,
                prediction_prob REAL,
                odds_win REAL,
                odds_draw REAL,
                odds_loss REAL,
                poisson_win REAL,
                poisson_draw REAL,
                poisson_loss REAL,
                market_win REAL,
                market_draw REAL,
                market_loss REAL,
                final_win REAL,
                final_draw REAL,
                final_loss REAL,
                risk_level TEXT,
                kelly_win REAL,
                kelly_draw REAL,
                kelly_loss REAL,
                expected_win REAL,
                expected_draw REAL,
                expected_loss REAL,
                recommended TEXT,
                recommended_kelly REAL,
                home_ranking INTEGER,
                away_ranking INTEGER,
                home_lambda REAL,
                away_lambda REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                actual_outcome TEXT,
                deviation_analysis TEXT,
                odds_source TEXT,
                hhad_win REAL,
                hhad_draw REAL,
                hhad_loss REAL,
                hhad_handicap REAL
            )
        """)
        
        # 如果旧表没有HHAD字段和odds_source字段，添加它们
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN odds_source TEXT")
            log("  数据库迁移: 已添加 odds_source 字段")
        except sqlite3.OperationalError:
            pass  # 字段已存在
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_win REAL")
            log("  数据库迁移: 已添加 hhad_win 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_draw REAL")
            log("  数据库迁移: 已添加 hhad_draw 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_loss REAL")
            log("  数据库迁移: 已添加 hhad_loss 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_handicap REAL")
            log("  数据库迁移: 已添加 hhad_handicap 字段")
        except sqlite3.OperationalError:
            pass
        
        # EV相关字段迁移
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN ev_win REAL")
            log("  数据库迁移: 已添加 ev_win 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN ev_draw REAL")
            log("  数据库迁移: 已添加 ev_draw 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN ev_loss REAL")
            log("  数据库迁移: 已添加 ev_loss 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN best_direction TEXT")
            log("  数据库迁移: 已添加 best_direction 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN best_direction_cn TEXT")
            log("  数据库迁移: 已添加 best_direction_cn 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_margin REAL")
            log("  数据库迁移: 已添加 avg_margin 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_open_w REAL")
            log("  数据库迁移: 已添加 avg_odds_open_w 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_open_d REAL")
            log("  数据库迁移: 已添加 avg_odds_open_d 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_open_l REAL")
            log("  数据库迁移: 已添加 avg_odds_open_l 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_close_w REAL")
            log("  数据库迁移: 已添加 avg_odds_close_w 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_close_d REAL")
            log("  数据库迁移: 已添加 avg_odds_close_d 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN avg_odds_close_l REAL")
            log("  数据库迁移: 已添加 avg_odds_close_l 字段")
        except sqlite3.OperationalError:
            pass
        
        # V4冷门预警：双盘口lambda字段
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN had_lambda_h REAL")
            log("  数据库迁移: 已添加 had_lambda_h 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN had_lambda_a REAL")
            log("  数据库迁移: 已添加 had_lambda_a 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_lambda_h REAL")
            log("  数据库迁移: 已添加 hhad_lambda_h 字段")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE poisson_predictions ADD COLUMN hhad_lambda_a REAL")
            log("  数据库迁移: 已添加 hhad_lambda_a 字段")
        except sqlite3.OperationalError:
            pass
        
        exec_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        records_to_insert = []
        
        for idx, r in enumerate(results, 1):
            # 使用体彩网官方编号作为match_id（稳定，不随运行次数变化）
            # 优先使用编号字段（如周六001），fallback到主队_客队_开赛时间
            match_id = r.get('编号', '')
            if not match_id or match_id == '未知':
                # fallback: 用主队+客队+开赛时间生成稳定ID
                home = r.get('主队', '').replace(' ', '_')[:10]
                away = r.get('客队', '').replace(' ', '_')[:10]
                kickoff = r.get('开赛时间', '').replace('-', '').replace(' ', '_').replace(':', '')[:13]
                match_id = f"{home}_{away}_{kickoff}" if home and away else f"{date_str.replace('-', '')}_{idx:03d}"
            
            # 解析概率字符串（如 "45.2%(中)"）获取数值
            def parse_prob(prob_str):
                if not prob_str:
                    return None
                import re
                match = re.match(r'([\d.]+)', str(prob_str))
                return float(match.group(1)) / 100 if match else None
            
            # 解析赔率
            try:
                odds_win = float(r.get('胜赔', 0))
                odds_draw = float(r.get('平赔', 0))
                odds_loss = float(r.get('负赔', 0))
            except:
                odds_win = odds_draw = odds_loss = 0
            
            # 解析综合概率
            final_home = parse_prob(r.get('综合_主胜', ''))
            final_draw = parse_prob(r.get('综合_平局', ''))
            final_loss = parse_prob(r.get('综合_客胜', ''))
            
            # 对三个方向都计算凯利（赔率为0时用概率反推）- 已废弃，不再使用
            kelly_win = 0
            kelly_draw = 0
            kelly_loss = 0
            
            # 解析泊松概率
            poisson_home = parse_prob(r.get('泊松_主胜', ''))
            poisson_draw = parse_prob(r.get('泊松_平局', ''))
            poisson_away = parse_prob(r.get('泊松_客胜', ''))
            final_away = parse_prob(r.get('综合_客胜', ''))
            
            # 用开赛日期而非生成日期，确保复盘时能按比赛日匹配
            kickoff_str = r.get('开赛时间', '')
            match_date = date_str  # 默认用生成日期
            if kickoff_str and kickoff_str != '待定':
                try:
                    match_date = kickoff_str.split(' ')[0]  # 取"2026-05-16 03:00"的日期部分
                except:
                    pass
            
            # 计算HHAD让球赔率
            hhad_win = r.get('hhad_home')
            hhad_draw = r.get('hhad_draw')
            hhad_loss = r.get('hhad_away')
            hhad_handicap = r.get('hhad_handicap')
            
            # 计算双盘口lambda值（用于V4冷门预警）
            had_lambda_h, had_lambda_a = None, None
            hhad_lambda_h, hhad_lambda_a = None, None
            
            if odds_win > 0 and odds_draw > 0 and odds_loss > 0:
                # 从HAD赔率反推lambda
                had_lambda_h, had_lambda_a = odds_to_lambda_fast(odds_win, odds_draw, odds_loss)
            
            if hhad_win and hhad_draw and hhad_loss:
                try:
                    hhad_lambda_h, hhad_lambda_a = odds_to_lambda_fast(
                        float(hhad_win), float(hhad_draw), float(hhad_loss), 
                        float(hhad_handicap) if hhad_handicap else None
                    )
                except:
                    pass
            
            record = {
                'match_id': match_id,
                'date': match_date,
                'exec_time': exec_time,
                'league': r.get('联赛', ''),
                'home_team': r.get('主队', ''),
                'away_team': r.get('客队', ''),
                'kickoff_time': r.get('开赛时间', ''),
                'prediction': r.get('推荐方向', ''),
                'prediction_prob': parse_prob(r.get('推荐概率', '')),
                'odds_win': odds_win,
                'odds_draw': odds_draw,
                'odds_loss': odds_loss,
                'poisson_win': poisson_home,
                'poisson_draw': poisson_draw,
                'poisson_loss': poisson_away,
                'market_win': None,  # 市场概率从赔率反推
                'market_draw': None,
                'market_loss': None,
                'final_win': final_home,
                'final_draw': final_draw,
                'final_loss': final_away,
                'risk_level': '',  # 保留字段但不再使用
                'kelly_win': 0,  # 保留字段但不再使用
                'kelly_draw': 0,
                'kelly_loss': 0,
                # 冷门预警字段
                'cold_risk': r.get('冷门风险', ''),
                'cold_signals': r.get('冷门信号', ''),
                'risk_warning': r.get('风险预警', ''),
                # HHAD让球赔率字段
                'odds_source': r.get('odds_source', ''),  # 赔率来源: had/hhad
                'hhad_win': hhad_win,  # 让球胜赔率
                'hhad_draw': hhad_draw,  # 让球平赔率
                'hhad_loss': hhad_loss,  # 让球负赔率
                'hhad_handicap': hhad_handicap,  # 让球数
                # V4冷门预警：双盘口lambda
                'had_lambda_h': had_lambda_h,
                'had_lambda_a': had_lambda_a,
                'hhad_lambda_h': hhad_lambda_h,
                'hhad_lambda_a': hhad_lambda_a,
                # qtx积分榜数据（LGBM新特征）
                'home_points': r.get('home_points', 0),
                'away_points': r.get('away_points', 0),
                'home_avg_goals': r.get('home_avg_goals', 0),
                'away_avg_goals': r.get('away_avg_goals', 0),
                'home_avg_conceded': r.get('home_avg_conceded', 0),
                'away_avg_conceded': r.get('away_avg_conceded', 0),
                # 价值投注字段（百家欧赔）
                'pinnacle_open_w': 0,
                'pinnacle_open_d': 0,
                'pinnacle_open_l': 0,
                'pinnacle_close_w': 0,
                'pinnacle_close_d': 0,
                'pinnacle_close_l': 0,
                'pinnacle_movement': '',
                'pinnacle_margin': 0,
                'implied_prob_w': 0,
                'implied_prob_d': 0,
                'implied_prob_l': 0,
                'ev_value': 0,
                'kelly_stake': 0,  # 保留字段但不再使用
                'value_flag': 0,  # 保留字段但不再使用
                'pod_signal': '',  # 保留字段但不再使用
                'clv': 0,  # 保留字段但不再使用
                # 新EV字段
                'ev_win': _to_float(r.get('EV_主胜', '')),
                'ev_draw': _to_float(r.get('EV_平局', '')),
                'ev_loss': _to_float(r.get('EV_客胜', '')),
                'best_direction': '',
                'best_direction_cn': r.get('推荐方向', ''),
                'avg_margin': 0,
                'avg_odds_open_w': 0,
                'avg_odds_open_d': 0,
                'avg_odds_open_l': 0,
                'avg_odds_close_w': 0,
                'avg_odds_close_d': 0,
                'avg_odds_close_l': 0,
                'actual_outcome': '',
                'deviation_analysis': '',
            }
            
            # 计算EV（百家欧赔）
            if HAS_VALUE_BET:
                # 从百家初盘/最新赔率字段获取数据
                avg_open = {
                    'w': _safe_float(r.get('百家初盘胜', 0)),
                    'd': _safe_float(r.get('百家初盘平', 0)),
                    'l': _safe_float(r.get('百家初盘负', 0)),
                }
                avg_close = {
                    'w': _safe_float(r.get('百家最新胜', 0)),
                    'd': _safe_float(r.get('百家最新平', 0)),
                    'l': _safe_float(r.get('百家最新负', 0)),
                }
                
                # 构建EV计算数据
                vb_data = {
                    'odds_win': odds_win,
                    'odds_draw': odds_draw,
                    'odds_loss': odds_loss,
                    'avg_odds_open_w': avg_open.get('w', 0),
                    'avg_odds_open_d': avg_open.get('d', 0),
                    'avg_odds_open_l': avg_open.get('l', 0),
                    'avg_odds_close_w': avg_close.get('w', 0),
                    'avg_odds_close_d': avg_close.get('d', 0),
                    'avg_odds_close_l': avg_close.get('l', 0),
                    'fusion_win': 0,  # LGBM融合概率由update_db_fusion.py填充，V3降级用final
                    'fusion_draw': 0,
                    'fusion_loss': 0,
                    'cold_risk': r.get('冷门风险', 0),
                    'ev_adjust': r.get('ev_adjust', 0),
                }
                
                # 计算三向EV
                vb_result = calculate_value_bet(vb_data)
                
                # 更新record中的EV字段
                record['ev_win'] = vb_result.get('ev_win', 0)
                record['ev_draw'] = vb_result.get('ev_draw', 0)
                record['ev_loss'] = vb_result.get('ev_loss', 0)
                record['ev_value'] = vb_result.get('ev_value', 0)
                record['best_direction'] = vb_result.get('best_direction', '')
                record['best_direction_cn'] = vb_result.get('best_direction_cn', '')
                record['avg_margin'] = vb_result.get('avg_margin', 0)
                record['implied_prob_w'] = vb_result.get('implied_prob_w', 0)
                record['implied_prob_d'] = vb_result.get('implied_prob_d', 0)
                record['implied_prob_l'] = vb_result.get('implied_prob_l', 0)
                # 百家初盘/最新赔率
                record['avg_odds_open_w'] = avg_open.get('w', 0)
                record['avg_odds_open_d'] = avg_open.get('d', 0)
                record['avg_odds_open_l'] = avg_open.get('l', 0)
                record['avg_odds_close_w'] = avg_close.get('w', 0)
                record['avg_odds_close_d'] = avg_close.get('d', 0)
                record['avg_odds_close_l'] = avg_close.get('l', 0)
            
            # 冷门风险：高/中风险时自动追加"防X"到cold_signals
            cold_risk_val = record.get('cold_risk', '')
            if cold_risk_val in ('高', '中'):
                probs = {
                    '平局': _to_float(record.get('final_draw', 0)),
                    '客胜': _to_float(record.get('final_loss', 0)),
                    '主胜': _to_float(record.get('final_win', 0)),
                }
                # 找第二大概率方向
                sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
                defend_target = sorted_probs[1][0]
                defend_prob = sorted_probs[1][1] * 100
                defend_tip = f"防{defend_target}({defend_prob:.0f}%)"
                # 追加到cold_signals
                existing_signals = record.get('cold_signals', '')
                if existing_signals:
                    record['cold_signals'] = f"{existing_signals}|{defend_tip}"
                else:
                    record['cold_signals'] = defend_tip
            
            records_to_insert.append(record)
            
            # Dry-run模式：仅打印预览
            if dry_run:
                log(f"[DRY-RUN] 将写入数据库: match_id={match_id}, {r.get('主队')} vs {r.get('客队')}")
                log(f"  -> 推荐: {r.get('推荐方向')} {r.get('推荐概率')}, EV: {r.get('EV', '-')}")
                log(f"  -> 赔率: 胜{odds_win}, 平{odds_draw}, 负{odds_loss}")
                log(f"  -> 泊松: 主胜{poisson_home}, 平局{poisson_draw}, 客胜{poisson_away}")
                log(f"  -> 综合: 主胜{final_home}, 平局{final_draw}, 客胜{final_away}")
        
        # 实际写入数据库
        if not dry_run and records_to_insert:
            # 按match_id的日期部分清除旧记录（而非按date字段）
            # 原因：同一天的比赛可能date字段不同（周六白天 vs 周日凌晨）
            #       但match_id的日期部分（如20260516）保持一致
            # 示例：周六白天比赛date=2026-05-16，周日凌晨比赛date=2026-05-17
            #       但都是20260516_XXX体彩网编号，应该一起处理
            match_id_dates = set()
            for record in records_to_insert:
                # 从match_id提取日期部分：20260516_001 -> 20260516
                match_id = record['match_id']
                date_part = match_id.split('_')[0] if '_' in match_id else ''
                if date_part:
                    match_id_dates.add(date_part)
            
            # 已存在→UPDATE关键字段（保留pinnacle/hkjc/EV/fusion等数据）
            # 不存在→INSERT新记录
            inserted = 0
            updated = 0
            for record in records_to_insert:
                cursor.execute(
                    "SELECT match_id FROM poisson_predictions WHERE match_id = ?",
                    (record['match_id'],)
                )
                exists = cursor.fetchone()
                
                # 如果match_id未匹配，尝试按主队+客队+开赛时间匹配（兼容旧格式match_id）
                if not exists:
                    cursor.execute(
                        "SELECT match_id FROM poisson_predictions WHERE home_team = ? AND away_team = ? AND kickoff_time = ?",
                        (record['home_team'], record['away_team'], record['kickoff_time'])
                    )
                    existing_row = cursor.fetchone()
                    if existing_row:
                        record['match_id'] = existing_row[0]  # 保留旧match_id，执行UPDATE
                
                if exists:
                    # 已存在：只UPDATE日报负责的字段，不动其他脚本写入的字段
                    cursor.execute("""
                        UPDATE poisson_predictions SET
                            date = :date, exec_time = :exec_time, league = :league,
                            home_team = :home_team, away_team = :away_team,
                            kickoff_time = :kickoff_time,
                            prediction = :prediction, prediction_prob = :prediction_prob,
                            odds_win = :odds_win, odds_draw = :odds_draw, odds_loss = :odds_loss,
                            poisson_win = :poisson_win, poisson_draw = :poisson_draw, poisson_loss = :poisson_loss,
                            market_win = :market_win, market_draw = :market_draw, market_loss = :market_loss,
                            final_win = :final_win, final_draw = :final_draw, final_loss = :final_loss,
                            risk_level = :risk_level, kelly_win = :kelly_win, kelly_draw = :kelly_draw, kelly_loss = :kelly_loss,
                            odds_source = :odds_source,
                            hhad_win = :hhad_win, hhad_draw = :hhad_draw, hhad_loss = :hhad_loss,
                            hhad_handicap = :hhad_handicap,
                            had_lambda_h = :had_lambda_h, had_lambda_a = :had_lambda_a,
                            hhad_lambda_h = :hhad_lambda_h, hhad_lambda_a = :hhad_lambda_a,
                            home_points = :home_points, away_points = :away_points,
                            home_avg_goals = :home_avg_goals, away_avg_goals = :away_avg_goals,
                            home_avg_conceded = :home_avg_conceded, away_avg_conceded = :away_avg_conceded
                        WHERE match_id = :match_id
                    """, record)
                    updated += 1
                else:
                    # 不存在：INSERT完整记录
                    cursor.execute("""
                        INSERT INTO poisson_predictions (
                            match_id, date, exec_time, league, home_team, away_team,
                            kickoff_time, prediction, prediction_prob, odds_win, odds_draw, odds_loss,
                            poisson_win, poisson_draw, poisson_loss, market_win, market_draw, market_loss,
                            final_win, final_draw, final_loss, risk_level, kelly_win, kelly_draw, kelly_loss,
                            cold_risk, cold_signals, risk_warning,
                            odds_source, hhad_win, hhad_draw, hhad_loss, hhad_handicap,
                            had_lambda_h, had_lambda_a, hhad_lambda_h, hhad_lambda_a,
                            home_points, away_points, home_avg_goals, away_avg_goals,
                            home_avg_conceded, away_avg_conceded,
                            pinnacle_open_w, pinnacle_open_d, pinnacle_open_l,
                            pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
                            pinnacle_movement, pinnacle_margin,
                            implied_prob_w, implied_prob_d, implied_prob_l,
                            ev_value, kelly_stake, value_flag, pod_signal, clv,
                            ev_win, ev_draw, ev_loss, best_direction, best_direction_cn,
                            avg_margin, avg_odds_open_w, avg_odds_open_d, avg_odds_open_l,
                            avg_odds_close_w, avg_odds_close_d, avg_odds_close_l,
                            actual_outcome, deviation_analysis
                        ) VALUES (
                            :match_id, :date, :exec_time, :league, :home_team, :away_team,
                            :kickoff_time, :prediction, :prediction_prob, :odds_win, :odds_draw, :odds_loss,
                            :poisson_win, :poisson_draw, :poisson_loss, :market_win, :market_draw, :market_loss,
                            :final_win, :final_draw, :final_loss, :risk_level, :kelly_win, :kelly_draw, :kelly_loss,
                            :cold_risk, :cold_signals, :risk_warning,
                            :odds_source, :hhad_win, :hhad_draw, :hhad_loss, :hhad_handicap,
                            :had_lambda_h, :had_lambda_a, :hhad_lambda_h, :hhad_lambda_a,
                            :home_points, :away_points, :home_avg_goals, :away_avg_goals,
                            :home_avg_conceded, :away_avg_conceded,
                            :pinnacle_open_w, :pinnacle_open_d, :pinnacle_open_l,
                            :pinnacle_close_w, :pinnacle_close_d, :pinnacle_close_l,
                            :pinnacle_movement, :pinnacle_margin,
                            :implied_prob_w, :implied_prob_d, :implied_prob_l,
                            :ev_value, :kelly_stake, :value_flag, :pod_signal, :clv,
                            :ev_win, :ev_draw, :ev_loss, :best_direction, :best_direction_cn,
                            :avg_margin, :avg_odds_open_w, :avg_odds_open_d, :avg_odds_open_l,
                            :avg_odds_close_w, :avg_odds_close_d, :avg_odds_close_l,
                            :actual_outcome, :deviation_analysis
                        )
                    """, record)
                    inserted += 1
            
            conn.commit()
            log(f"✓ 已写入数据库: 新增{inserted}条, 更新{updated}条")
            log(f"  数据库: {DB_PATH}")
        elif dry_run:
            log(f"[DRY-RUN] 共 {len(records_to_insert)} 条记录待写入")
        
        conn.close()
        
    except Exception as e:
        log(f"⚠️ 数据库写入失败: {e}")
        import traceback
        traceback.print_exc()


# ========== 主流程 ==========

def get_time_window(date_str: str = None) -> Tuple[datetime, datetime]:
    """
    获取时间窗口
    当天12:00至次日11:59（用户指定时间窗口）
    """
    if date_str:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        target_date = datetime.now()
    
    # 当天12:00
    start_time = target_date.replace(hour=12, minute=0, second=0, microsecond=0)
    # 次日11:59
    end_time = (target_date + timedelta(days=1)).replace(hour=11, minute=59, second=0, microsecond=0)
    
    return start_time, end_time


def process_league(league_code: str, odds_data: dict, start_time: datetime, end_time: datetime, 
                   force_refresh: bool = False,
                   use_fundamental: bool = True,
                   tactical_only_4star: bool = False,
                   use_dim6: bool = True) -> Tuple[List[dict], List[dict]]:
    """
    处理单个联赛
    返回: (匹配成功的比赛列表, 跳过无赔率的比赛列表)
    
    Args:
        league_code: 联赛代码
        odds_data: 赔率数据
        start_time: 时间窗口开始
        end_time: 时间窗口结束
        force_refresh: 是否强制刷新缓存
        use_fundamental: 是否使用基本面分析
        tactical_only_4star: 是否只对4星+场次做战术分析
        use_dim6: 是否使用6维评分
    """
    league_name = LEAGUE_CODES.get(league_code, {}).get('name', league_code)
    log(f"\n{'='*60}")
    log(f"处理 {league_name} ({league_code})")
    log(f"{'='*60}")
    
    # 获取积分榜
    standings_data = fetch_standings(league_code, force_refresh)
    if not standings_data:
        log(f"⚠️ 无法获取 {league_name} 积分榜")
        return [], []
    
    standings = standings_data.get('standings', {})
    
    # 获取qtx积分榜数据（补充场均进球/失球等额外数据）
    qtx_standings = None
    if HAS_QTX_STANDINGS:
        qtx_standings = fetch_qtx_standings(league_name)
        if qtx_standings:
            log(f"✓ 获取 {league_name} qtx积分榜数据（{len(qtx_standings.get('teams', []))}支球队）")
        else:
            log(f"⚠️ 无法获取 {league_name} qtx积分榜数据")
    
    # 获取时间窗口内的比赛
    matches = fetch_matches_in_window(league_code, start_time, end_time, force_refresh)
    if not matches:
        log(f"⚠️ {league_name} 在时间窗口内无比赛")
        return [], []
    
    # 分析每场比赛
    successful = []
    skipped = []
    
    for match in matches:
        home_team = match['home_team']
        away_team = match['away_team']
        
        # 查找积分榜数据
        home_data = find_team_in_standings(home_team, standings)
        away_data = find_team_in_standings(away_team, standings)
        
        if not home_data:
            log(f"⚠️ 未找到主队积分榜数据: {home_team}")
            home_data = {
                'name': home_team,
                'name_zh': TEAM_NAME_MAP.get(home_team, home_team),
                'rank': 10,
                'played': 10,
                'gf': 12,
                'ga': 12,
            }
        
        if not away_data:
            log(f"⚠️ 未找到客队积分榜数据: {away_team}")
            away_data = {
                'name': away_team,
                'name_zh': TEAM_NAME_MAP.get(away_team, away_team),
                'rank': 10,
                'played': 10,
                'gf': 12,
                'ga': 12,
            }
        
        # 获取真实赔率
        real_odds = get_real_odds(home_team, away_team, odds_data)
        # 获取HHAD让球赔率
        hhad_odds = get_hhad_odds(home_team, away_team, odds_data)
        # 获取体彩网官方编号
        match_num_display = get_match_num(home_team, away_team, odds_data)
        
        if real_odds is None and hhad_odds is None:
            log(f"⏭️ 跳过（无赔率）: {home_data['name_zh']} vs {away_data['name_zh']}")
            skipped.append({
                'home': home_data['name_zh'],
                'away': away_data['name_zh'],
                'league': league_name,
                'time': match['match_time_str'],
            })
            continue
        
        # 如果HAD赔率存在，优先使用HAD
        # 如果HAD赔率为空但HHAD存在，使用HHAD赔率
        analysis = None
        if real_odds is not None:
            # HAD赔率存在，使用HAD
            analysis = analyze_match(home_data, away_data, real_odds, hhad_odds=hhad_odds)
            if analysis:
                analysis['odds_source'] = 'had'
        elif hhad_odds is not None:
            # HAD为空但HHAD存在，使用HHAD
            analysis = analyze_match(home_data, away_data, None, hhad_odds=hhad_odds)
            if analysis:
                analysis['odds_source'] = 'hhad'
                log(f"  [HHAD] {home_data['name_zh']} 仅开售让球盘")
        
        if analysis is None:
            skipped.append({
                'home': home_data['name_zh'],
                'away': away_data['name_zh'],
                'league': league_name,
                'time': match['match_time_str'],
            })
            continue
        
        # 基本面分析（可选）
        fundamental_data = None
        tactical_data = None
        
        if HAS_FUNDAMENTAL and use_fundamental:
            home_rank = home_data.get('rank', 0)
            away_rank = away_data.get('rank', 0)
            home_points = home_data.get('pts', 0)
            away_points = away_data.get('pts', 0)
            
            try:
                # 获取基本面数据
                fundamental_data = analyze_fundamental(
                    home_data['name_zh'], away_data['name_zh'], league_name,
                    home_rank, away_rank, home_points, away_points,
                    odds=real_odds
                )
                
                # 计算EV修正因子
                ev_adjust = 0.0
                
                # 冷门风险：高/中风险时标记
                cold_risk_level = fundamental_data.get("cold_risk", {}).get("cold_risk", "")
                if cold_risk_level in ("高", "中"):
                    probs = {
                        "主胜": analysis.get('final_home', 0) or 0,
                        "平局": analysis.get('final_draw', 0) or 0,
                        "客胜": analysis.get('final_away', 0) or 0,
                    }
                    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
                    defend_target = sorted_probs[1][0]  # 第二大概率方向
                    defend_prob = sorted_probs[1][1] * 100  # 转百分比
                    analysis['defend_tip'] = f"防{defend_target}({defend_prob:.0f}%)"
                    # 追加到EV说明
                    if analysis.get('confidence_reason'):
                        analysis['confidence_reason'] += f" | {analysis['defend_tip']}"
                    else:
                        analysis['confidence_reason'] = analysis['defend_tip']
                    log(f"   冷门风险{cold_risk_level}: {analysis['defend_tip']}")
                
                # 打印基本面摘要
                log(f"   基本面: {fundamental_data.get('risk_warning', '正常')}")
                
                # 战术分析（只对高EV场次）
                if tactical_only_4star:
                    try:
                        tactical_data = get_tactical_preview(
                            home_data['name_zh'], away_data['name_zh'], league_name
                        )
                        if tactical_data.get('has_data'):
                            log(f"   战术: {tactical_data.get('summary', '')[:50]}")
                    except Exception as e:
                        log(f"   战术分析失败: {str(e)[:50]}")
                
                # 6维评分 → EV修正因子
                dim6_result = None
                if use_dim6:
                    try:
                        dim6_result = calculate_6dim_score(
                            home_data['name_zh'], away_data['name_zh'], league_name,
                            home_rank=home_rank, away_rank=away_rank,
                            home_points=home_points, away_points=away_points,
                            home_injuries=fundamental_data.get('injury_info', {}).get('home_injuries', []),
                            away_injuries=fundamental_data.get('injury_info', {}).get('away_injuries', []),
                            home_key_out=fundamental_data.get('injury_info', {}).get('key_players_out', {}).get('home', []),
                            away_key_out=fundamental_data.get('injury_info', {}).get('key_players_out', {}).get('away', []),
                            home_recent=fundamental_data.get('home_form', {}),
                            away_recent=fundamental_data.get('away_form', {}),
                            odds_home=real_odds[0] if real_odds else 2.0,
                            odds_draw=real_odds[1] if real_odds and len(real_odds) > 1 else 3.0,
                            odds_away=real_odds[2] if real_odds and len(real_odds) > 2 else 3.0
                        )
                        
                        if dim6_result and dim6_result.get('dim6_enabled'):
                            # 6维评分调整 → EV修正因子（正面+0.02，负面-0.02）
                            dim6_adjust = dim6_result.get('confidence_adjust', 0.0)
                            if dim6_adjust >= 0.5:
                                ev_adjust = 0.02  # 正面优势
                            elif dim6_adjust <= -0.5:
                                ev_adjust = -0.02  # 负面劣势
                            
                            # 添加6维评分摘要到EV说明
                            dim6_summary = f"{dim6_result.get('score_diff', 0):+.1f}分/{dim6_result.get('advantage_level', '')}"
                            analysis['confidence_reason'] = f"{analysis['confidence_reason']} | 6维:{dim6_summary}"
                            
                            # 警告信息
                            dim6_warnings = dim6_result.get('warnings', [])
                            if dim6_warnings:
                                existing_warning = fundamental_data.get('risk_warning', '')
                                if existing_warning and existing_warning != '正常':
                                    analysis['risk_warning'] = f"{existing_warning}; {'; '.join(dim6_warnings[:1])}"
                                else:
                                    analysis['risk_warning'] = '; '.join(dim6_warnings[:1])
                    except Exception as e:
                        log(f"   6维评分失败: {str(e)[:50]}")
                
                # 保存EV修正因子到analysis
                analysis['ev_adjust'] = ev_adjust
                
                # 避免搜索过快
                time.sleep(2)
                
            except Exception as e:
                log(f"   基本面分析失败: {str(e)[:50]}")
        
        # 获取qtx积分榜数据（补充场均进球/失球）
        qtx_home_data = None
        qtx_away_data = None
        if qtx_standings:
            from fetch_standings_qtx import get_team_stats
            qtx_home_data = get_team_stats(qtx_standings, home_team)
            qtx_away_data = get_team_stats(qtx_standings, away_team)
        
        # 传递matchNum和基本面数据给格式化函数
        result = format_result_for_output(
            match, analysis, len(successful) + 1, league_name, match_num_display,
            fundamental_data, tactical_data, qtx_home_data, qtx_away_data
        )
        successful.append(result)
        
        log(f"✓ {result['编号']} {result['主队']} vs {result['客队']}")
        log(f"   推荐: {result['推荐方向']} | 概率: {result['推荐概率']} | EV: {result.get('EV', '-')}")
    
    return successful, skipped


def main():
    parser = argparse.ArgumentParser(description='竞彩足球泊松分布分析系统 - 每日日报生成（基本面增强版）')
    parser.add_argument('--date', type=str, help='指定日期 (YYYY-MM-DD格式)，默认今天')
    parser.add_argument('--dry-run', action='store_true', help='仅测试不生成文件')
    parser.add_argument('--force-refresh', action='store_true', help='强制刷新API缓存')
    parser.add_argument('--no-fundamental', action='store_true', help='跳过基本面分析（节省时间）')
    parser.add_argument('--tactical-only', action='store_true', help='只对4星+场次做战术分析')
    parser.add_argument('--no-cross-validate', action='store_true', help='禁用交叉验证（节省资源）')
    parser.add_argument('--no-v34', action='store_true', help='禁用V3.4量化变量（节省搜索时间）')
    parser.add_argument('--no-6dim', action='store_true', help='禁用6维评分（节省搜索时间）')
    parser.add_argument('--incremental', action='store_true', help='增量模式：覆盖更新已有预测+补新增晚场比赛（用于18:00补跑）')
    
    args = parser.parse_args()
    
    # 基本面分析开关
    use_fundamental = not args.no_fundamental
    tactical_only_4star = args.tactical_only
    use_cross_validate = not args.no_cross_validate
    use_v34 = not args.no_v34
    use_dim6 = not args.no_6dim
    
    # 设置fundamental_analysis模块的开关
    if use_fundamental:
        try:
            from fundamental_analysis import set_cross_validate_enabled, set_v34_enabled, set_dim6_enabled
            set_cross_validate_enabled(use_cross_validate)
            set_v34_enabled(use_v34)
            set_dim6_enabled(use_dim6)
        except ImportError:
            log("⚠️ 基本面分析模块导入失败")
    
    if use_fundamental:
        log(f"✓ 基本面分析: 已启用")
        if tactical_only_4star:
            log(f"✓ 战术分析: 仅4星+场次")
        else:
            log(f"✓ 战术分析: 所有场次")
        if use_cross_validate:
            log(f"✓ 交叉验证: 已启用（仅4星+场次）")
        else:
            log(f"⚠️ 交叉验证: 已禁用")
        if use_v34:
            log(f"✓ V3.4变量: 已启用（仅4星+场次）")
        else:
            log(f"⚠️ V3.4变量: 已禁用")
        if use_dim6:
            log(f"✓ 6维评分: 已启用（仅3星+场次）")
        else:
            log(f"⚠️ 6维评分: 已禁用")
    else:
        log(f"⚠️ 基本面分析: 已禁用（使用 --no-fundamental）")
    
    # 确保目录存在
    ensure_dirs()
    
    # 计算时间窗口
    start_time, end_time = get_time_window(args.date)
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    
    log(f"=" * 60)
    log(f"竞彩足球泊松分布分析系统 - 每日日报")
    log(f"=" * 60)
    log(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"分析日期: {date_str}")
    log(f"时间窗口: {start_time.strftime('%Y-%m-%d %H:%M')} 至 {end_time.strftime('%Y-%m-%d %H:%M')}")
    log(f"模式: {'Dry-Run（仅测试）' if args.dry_run else '正式运行'}")
    log(f"{'=' * 60}")
    
    # 增量模式：查询DB中已有比赛（按主队+客队+开赛时间匹配，不受match_id格式影响）
    existing_match_keys = set()
    if args.incremental:
        try:
            import sqlite3 as _sql
            _conn = _sql.connect(DB_PATH)
            _cur = _conn.cursor()
            _cur.execute("SELECT home_team, away_team, kickoff_time FROM poisson_predictions WHERE kickoff_time >= ? AND kickoff_time <= ?",
                        (start_time.strftime('%Y-%m-%d %H:%M'), end_time.strftime('%Y-%m-%d %H:%M')))
            for row in _cur.fetchall():
                key = f"{row[0]}_{row[1]}_{row[2]}"
                existing_match_keys.add(key)
            _conn.close()
            log(f"🔄 增量模式：DB中已有 {len(existing_match_keys)} 场预测，将覆盖更新+补新增")
        except Exception as e:
            log(f"⚠️ 增量模式查询失败，将全量运行: {e}")
    
    # 加载真实赔率（优先API，fallback到缓存文件）
    odds_data = get_odds_data()
    if not odds_data:
        log("⚠️ 警告：未加载到真实赔率，将跳过所有比赛")
    
    # 处理五大联赛
    all_results = []
    all_skipped = []
    
    for league_code in LEAGUE_CODES.keys():
        results, skipped = process_league(
            league_code, odds_data, start_time, end_time, args.force_refresh,
            use_fundamental=use_fundamental,
            tactical_only_4star=tactical_only_4star,
            use_dim6=use_dim6
        )
        all_results.extend(results)
        all_skipped.extend(skipped)
        
        # API速率限制：每个联赛之间间隔7秒
        if not args.dry_run:
            time.sleep(7)
    
    # 处理其他联赛（使用赔率反推xG）
    log(f"\n{'='*60}")
    log(f"处理其他联赛（赔率反推xG模式）")
    log(f"{'='*60}")
    
    # 从赔率数据中提取其他联赛的比赛
    other_league_matches = {}
    # 记录五大联赛已分析的比赛（用于去重）
    # 使用赔率键名和编号去重
    processed_keys = set()
    for r in all_results:
        processed_keys.add(r.get('编号', ''))
        # 也添加赔率键名（如果有）
        home_team = r.get('主队', '')
        away_team = r.get('客队', '')
        if home_team and away_team:
            # 标准化队名进行匹配
            normalized_home = normalize_team_name(home_team)
            normalized_away = normalize_team_name(away_team)
            processed_keys.add(f"{normalized_home} vs {normalized_away}")
    
    for key, odds_info in odds_data.items():
        league_name = odds_info.get('league', '')
        
        if not league_name:
            continue
        
        # 检查是否已分析过（五大联赛渠道已处理）
        # 通过编号检查（同时检查数字和显示格式）
        match_num = str(odds_info.get('matchNum', ''))
        should_skip = False
        
        if match_num and len(match_num) == 4:
            # 检查数字格式和显示格式
            display_num = match_num_to_display(match_num)
            if match_num in processed_keys or display_num in processed_keys:
                should_skip = True
        
        # 也通过赔率键名检查
        if not should_skip and key in processed_keys:
            should_skip = True
        
        if should_skip:
            continue
        
        # 基于开赛时间过滤：开赛时间必须在 start_time 到 end_time 窗口内
        match_num_raw = str(odds_info.get('matchNum', ''))
        skip_reason = None
        match_date = odds_info.get('matchDate', '')
        match_time = odds_info.get('matchTime', '')
        
        if match_date and match_time:
            try:
                # 解析开赛时间
                kickoff_str = f"{match_date} {match_time[:8]}"
                kickoff_dt = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M:%S")
                
                # 判断是否在时间窗口内
                if not (start_time <= kickoff_dt <= end_time):
                    skip_reason = f"跳过非时间窗口场次: {match_num_raw} (开赛{kickoff_dt.strftime('%m-%d %H:%M')}，窗口{start_time.strftime('%m-%d %H:%M')}-{end_time.strftime('%m-%d %H:%M')})"
            except (ValueError, TypeError) as e:
                pass  # 解析失败则不过滤，保留
        
        if skip_reason:
            log(f"  [过滤] {league_name} {skip_reason}")
            continue
        
        if league_name not in other_league_matches:
            other_league_matches[league_name] = []
        # 提取开赛时间
        match_date = odds_info.get('matchDate', '')
        match_time = odds_info.get('matchTime', '')
        kickoff_time = ''
        if match_date and match_time:
            try:
                kickoff_time = f"{match_date} {match_time[:5]}"
            except:
                kickoff_time = ''
        
        other_league_matches[league_name].append({
            'key': key,
            'odds': (odds_info.get('home'), odds_info.get('draw'), odds_info.get('away')),
            'hhad_odds': (odds_info.get('hhad_home'), odds_info.get('hhad_draw'), 
                         odds_info.get('hhad_away'), odds_info.get('hhad_handicap')),
            'odds_source': odds_info.get('odds_source', ''),
            'match_num': odds_info.get('matchNum', ''),
            'kickoff_time': kickoff_time,
        })
    
    log(f"发现 {len(other_league_matches)} 个其他联赛: {', '.join(other_league_matches.keys())}")
    
    # 处理每个其他联赛
    for league_name, matches in other_league_matches.items():
        log(f"\n处理 {league_name} ({len(matches)} 场比赛)")
        
        for match_info in matches:
            key = match_info['key']
            odds = match_info['odds']
            match_num = match_info['match_num']
            
            # 解析队名
            try:
                parts = key.split(' vs ')
                if len(parts) != 2:
                    continue
                home_team_cn = parts[0].strip()
                away_team_cn = parts[1].strip()
            except:
                continue
            
            # 创建模拟的积分榜数据（用于统一接口）
            home_data = {
                'name': home_team_cn,
                'name_zh': home_team_cn,
                'rank': 10,
                'played': 10,
                'gf': 12,
                'ga': 12,
            }
            away_data = {
                'name': away_team_cn,
                'name_zh': away_team_cn,
                'rank': 10,
                'played': 10,
                'gf': 12,
                'ga': 12,
            }
            
            # 决定使用HAD还是HHAD赔率
            real_odds = odds if odds[0] is not None else None
            hhad_odds = match_info['hhad_odds']
            
            # 使用赔率反推xG模式分析
            # 优先使用HAD赔率，如果HAD为空则使用HHAD
            analysis = None
            odds_source = "had"
            
            if real_odds is not None:
                # HAD赔率存在
                analysis = analyze_match(home_data, away_data, real_odds, use_odds_xg=True, hhad_odds=hhad_odds)
                odds_source = "had"
            elif hhad_odds[0] is not None:
                # HAD为空但HHAD存在
                analysis = analyze_match(home_data, away_data, None, use_odds_xg=True, hhad_odds=hhad_odds)
                odds_source = "hhad"
                log(f"  [HHAD] {home_team_cn} vs {away_team_cn} 仅开售让球盘")
            
            if analysis is None:
                continue
            
            # 标记赔率来源
            analysis['odds_source'] = odds_source
            
            # 格式化结果
            match_id = match_num_to_display(match_num) if match_num else f"未知"
            
            result = {
                "编号": match_id,
                "_match_id": f"{date_str.replace('-','')}_{len(all_results)+1:03d}",  # DB内部match_id，用于回写
                "联赛": league_name,
                "开赛时间": match_info.get('kickoff_time', '') or "待定",
                "主队": analysis['home_name_zh'],
                "客队": analysis['away_name_zh'],
                "推荐方向": analysis['best_direction_cn'] or analysis['recommendation'],
                "推荐概率": analysis['rec_prob_display'],
                "EV": f"{analysis.get('ev_value', 0)*100:.1f}%" if analysis.get('ev_value', 0) > 0 else "-",
                "EV_主胜": f"{analysis.get('ev_win', 0)*100:.1f}%" if analysis.get('ev_win', 0) > 0 else "-",
                "EV_平局": f"{analysis.get('ev_draw', 0)*100:.1f}%" if analysis.get('ev_draw', 0) > 0 else "-",
                "EV_客胜": f"{analysis.get('ev_loss', 0)*100:.1f}%" if analysis.get('ev_loss', 0) > 0 else "-",
                "EV说明": analysis['confidence_reason'],
                "xG来源": analysis['xg_source'],
                "参考比分": "/".join(analysis['reference_scores']),
                "胜赔": f"{analysis['odds_home']:.2f}",
                "平赔": f"{analysis['odds_draw']:.2f}",
                "负赔": f"{analysis['odds_away']:.2f}",
                "泊松_主胜": f"{analysis['poisson_home']*100:.1f}%",
                "泊松_平局": f"{analysis['poisson_draw']*100:.1f}%",
                "泊松_客胜": f"{analysis['poisson_away']*100:.1f}%",
                "综合_主胜": f"{analysis['final_home']*100:.1f}%",
                "综合_平局": f"{analysis['final_draw']*100:.1f}%",
                "综合_客胜": f"{analysis['final_away']*100:.1f}%",
                # HHAD字段
                "odds_source": odds_source,
                "hhad_home": analysis.get('hhad_home'),
                "hhad_draw": analysis.get('hhad_draw'),
                "hhad_away": analysis.get('hhad_away'),
                "hhad_handicap": analysis.get('hhad_handicap'),
                "ev_adjust": analysis.get('ev_adjust', 0),
            }
            
            all_results.append(result)
            log(f"✓ {result['编号']} {result['主队']} vs {result['客队']} [赔率反推-{'HHAD' if odds_source == 'hhad' else 'HAD'}]")
            log(f"   推荐: {result['推荐方向']} | 概率: {result['推荐概率']} | xG来源: {result['xG来源']}")
    
    # 汇总结果
    log(f"\n{'=' * 60}")
    log(f"处理完成")
    log(f"{'=' * 60}")
    log(f"成功分析: {len(all_results)} 场")
    log(f"跳过（无赔率）: {len(all_skipped)} 场")
    
    if all_skipped:
        log("\n跳过比赛列表:")
        for s in all_skipped:
            log(f"  - {s['league']} {s['home']} vs {s['away']} ({s['time']})")
    
    if not all_results:
        log("\n⚠️ 警告：无成功分析的比赛，无法生成报表")
        return
    
    # 按时间排序
    all_results.sort(key=lambda x: x['开赛时间'])
    
    # 增量模式：统计覆盖/新增数量（不过滤，全部进入save_to_database做UPSERT）
    if existing_match_keys and all_results:
        update_count = 0
        new_count = 0
        for r in all_results:
            key = f"{r.get('主队', '')}_{r.get('客队', '')}_{r.get('开赛时间', '')}"
            if key in existing_match_keys:
                update_count += 1
            else:
                new_count += 1
        log(f"🔄 增量模式：覆盖更新 {update_count} 场，新增 {new_count} 场")
    
    # 编号已在format_result_for_output中设置为体彩网官方编号，不再重新计算
    # （如果有比赛未获取到matchNum，编号可能仍为自动计算格式）
    
    # 打印结果摘要
    log("\n分析结果摘要:")
    for r in all_results:
        log(f"  {r['编号']} {r['联赛']} {r['主队']} vs {r['客队']}")
        log(f"    推荐: {r['推荐方向']} {r['推荐概率']} | EV: {r.get('EV', '-')}")
    
    # 保存文件
    if not args.dry_run:
        excel_path = os.path.join(OUTPUT_DIR, f"{date_str}.xlsx")
        csv_path = os.path.join(OUTPUT_DIR, f"{date_str}.csv")
        
        # 先获取百家指数赔率数据并计算EV（在保存文件前）
        try:
            from fetch_pinnacle_odds import fetch_pinnacle_odds, team_name_similarity
            
            log("\n正在获取百家指数赔率数据...")
            pinnacle_data = fetch_pinnacle_odds(date_str)
            
            # 时间窗口跨两天，也要抓次日的百家指数
            try:
                next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                pinnacle_data_next = fetch_pinnacle_odds(next_day)
                if pinnacle_data_next:
                    # 去重：按home+away合并
                    existing_keys = {(p.get('home',''), p.get('away','')) for p in (pinnacle_data or [])}
                    for p in pinnacle_data_next:
                        key = (p.get('home',''), p.get('away',''))
                        if key not in existing_keys:
                            pinnacle_data.append(p)
                            existing_keys.add(key)
                    log(f"合并次日百家指数: {len(pinnacle_data_next)} 场，去重后总计 {len(pinnacle_data)} 场")
            except Exception as e_next:
                log(f"[WARN] 抓取次日百家指数失败: {e_next}")
            
            if pinnacle_data:
                # 匹配百家指数数据到all_results
                ev_count = 0
                for result in all_results:
                    home = result.get('主队', '')
                    away = result.get('客队', '')
                    
                    best_match = None
                    best_score = 0
                    for pin in pinnacle_data:
                        score_home = team_name_similarity(home, pin.get('home', ''))
                        score_away = team_name_similarity(away, pin.get('away', ''))
                        score = (score_home + score_away) / 2
                        if score > best_score:
                            best_score = score
                            best_match = pin
                    
                    if best_score >= 0.6 and best_match:
                        # 获取百家指数数据
                        avg_open = best_match.get('avg_odds_open', {})
                        avg_close = best_match.get('avg_odds_close', {})
                        
                        # 构建EV计算数据
                        vb_data = {
                            'odds_win': _safe_float(result.get('胜赔', 0)),
                            'odds_draw': _safe_float(result.get('平赔', 0)),
                            'odds_loss': _safe_float(result.get('负赔', 0)),
                            'avg_odds_open_w': avg_open.get('w', 0),
                            'avg_odds_open_d': avg_open.get('d', 0),
                            'avg_odds_open_l': avg_open.get('l', 0),
                            'avg_odds_close_w': avg_close.get('w', 0),
                            'avg_odds_close_d': avg_close.get('d', 0),
                            'avg_odds_close_l': avg_close.get('l', 0),
                            'fusion_win': _to_float(result.get('综合_主胜', 0)),
                            'fusion_draw': _to_float(result.get('综合_平局', 0)),
                            'fusion_loss': _to_float(result.get('综合_客胜', 0)),
                            'cold_risk': result.get('冷门风险', 0),
                            'ev_adjust': result.get('ev_adjust', 0),
                        }
                        
                        # 计算三向EV
                        vb_result = calculate_value_bet(vb_data)
                        
                        # 写入结果字段
                        result['EV'] = f"{vb_result.get('ev_value', 0)*100:.1f}%"
                        result['EV_主胜'] = f"{vb_result.get('ev_win', 0)*100:.1f}%"
                        result['EV_平局'] = f"{vb_result.get('ev_draw', 0)*100:.1f}%"
                        result['EV_客胜'] = f"{vb_result.get('ev_loss', 0)*100:.1f}%"
                        result['推荐方向'] = vb_result.get('best_direction_cn', result.get('推荐方向', ''))
                        
                        # 添加百家初盘/最新赔率
                        result['百家初盘胜'] = avg_open.get('w', 0)
                        result['百家初盘平'] = avg_open.get('d', 0)
                        result['百家初盘负'] = avg_open.get('l', 0)
                        result['百家最新胜'] = avg_close.get('w', 0)
                        result['百家最新平'] = avg_close.get('d', 0)
                        result['百家最新负'] = avg_close.get('l', 0)
                        
                        ev_count += 1
                
                log(f"✓ 成功计算 {ev_count}/{len(all_results)} 场EV")

                # 将Pinnacle/SB/百家赔率写入DB（通过缓存）
                try:
                    from fetch_pinnacle_odds import apply_odds_to_db
                    jc_updated = apply_odds_to_db(DB_PATH, date_str=date_str)
                    log(f"✓ 百家指数赔率写入DB: {jc_updated} 场")
                except Exception as e_db:
                    log(f"⚠️ 百家指数赔率写入DB失败: {e_db}")
        except Exception as e:
            import traceback
            log(f"⚠️ 获取平博数据失败: {e}")
            traceback.print_exc()
        
        # 保存文件（此时已有价值投注字段）
        save_to_excel(all_results, excel_path)
        save_to_csv(all_results, csv_path)
        
        # 同时写入数据库
        save_to_database(all_results, date_str, dry_run=False)
        
        # V3: 写入DB后执行LGBM融合概率填充 + EV全量重算
        try:
            import sqlite3 as _sqlite3
            log("🔄 执行LGBM融合概率填充...")
            import subprocess
            result_fusion = subprocess.run(
                ['python3', os.path.join(os.path.dirname(__file__), 'update_db_fusion.py')],
                capture_output=True, text=True, timeout=120
            )
            if result_fusion.returncode == 0:
                log("✓ LGBM融合概率填充完成")
            else:
                log(f"⚠️ LGBM融合概率填充失败: {result_fusion.stderr[:200]}")
            
            log("🔄 执行EV全量重算(V3概率优势法)...")
            result_ev = subprocess.run(
                ['python3', os.path.join(os.path.dirname(__file__), 'value_bet.py'), '--all'],
                capture_output=True, text=True, timeout=120
            )
            if result_ev.returncode == 0:
                log("✓ EV全量重算完成")
            else:
                log(f"⚠️ EV重算失败: {result_ev.stderr[:200]}")
            
            # 从DB读回最新EV数据更新results并重写CSV
            # 用主队+客队作为匹配key，避免编号顺序不一致
            conn_r = _sqlite3.connect(DB_PATH)
            cursor_r = conn_r.cursor()
            cursor_r.execute('''SELECT home_team, away_team, ev_win, ev_draw, ev_loss, best_direction_cn, 
                avg_odds_open_w, avg_odds_open_d, avg_odds_open_l,
                avg_odds_close_w, avg_odds_close_d, avg_odds_close_l, cold_risk
                FROM poisson_predictions WHERE kickoff_time >= ? AND kickoff_time <= ?''', 
                (start_time.strftime('%Y-%m-%d %H:%M'), end_time.strftime('%Y-%m-%d %H:%M')))
            db_ev = {f"{row[0]}_{row[1]}": row[2:] for row in cursor_r.fetchall()}
            conn_r.close()
            
            updated = 0
            for result in all_results:
                key = f"{result.get('主队','')}_{result.get('客队','')}"
                if key in db_ev:
                    evw, evd, evl, bdir, aow, aod, aol, acw, acd, acl, cr = db_ev[key]
                    if evw is not None:
                        result['EV_主胜'] = f'{evw*100:.1f}%'
                        result['EV_平局'] = f'{evd*100:.1f}%' if evd is not None else '-'
                        result['EV_客胜'] = f'{evl*100:.1f}%' if evl is not None else '-'
                    if bdir:
                        result['推荐方向'] = bdir
                    if aow and aow > 0: result['百家初盘胜'] = f'{aow:.2f}'
                    if aod and aod > 0: result['百家初盘平'] = f'{aod:.2f}'
                    if aol and aol > 0: result['百家初盘负'] = f'{aol:.2f}'
                    if acw and acw > 0: result['百家最新胜'] = f'{acw:.2f}'
                    if acd and acd > 0: result['百家最新平'] = f'{acd:.2f}'
                    if acl and acl > 0: result['百家最新负'] = f'{acl:.2f}'
                    if cr and cr not in ('无', '低', ''):
                        result['EV说明'] = f'冷门风险{cr}' + ('|' + result.get('EV说明', '') if result.get('EV说明') else '')
                    updated += 1
            
            if updated > 0:
                save_to_csv(all_results, csv_path)
                save_to_excel(all_results, excel_path)
                log(f"✓ 已从DB回写{updated}场EV数据，CSV/Excel已更新")
        except Exception as e:
            import traceback
            log(f"⚠️ fusion+EV后处理失败: {e}")
            traceback.print_exc()
        
        log(f"\n✓ 报表生成完成!")
        log(f"  Excel: {excel_path}")
        log(f"  CSV: {csv_path}")
        
        # ========== 自动部署：push_db + align + build + git push ==========
        try:
            import subprocess as _sp
            dashboard_dir = os.path.join(SCRIPT_DIR, 'football-dashboard')
            
            # Step 1: 推送DB到GitHub Release
            log("\n🔄 推送DB到GitHub Release...")
            push_db_result = _sp.run(
                ['python3', 'scripts/push_db.py', '--db', DB_PATH],
                cwd=dashboard_dir, capture_output=True, text=True, timeout=60
            )
            if push_db_result.returncode == 0:
                log("  ✅ DB已推送到Release")
            else:
                log(f"  ⚠️ DB推送失败: {push_db_result.stderr[:80]}")
            
            # Step 2: 对齐合并
            log("🔄 对齐合并DB数据...")
            align_result = _sp.run(
                ['python3', 'scripts/align_and_merge.py', '--date', date_str, '--db', DB_PATH],
                cwd=dashboard_dir, capture_output=True, text=True, timeout=60
            )
            if align_result.returncode != 0:
                log(f"  ⚠️ align失败: {align_result.stderr[:80]}")
            
            # Step 3: 构建看板
            log("🔄 构建看板...")
            build_result = _sp.run(
                ['python3', 'scripts/merge_and_build.py', '--db', DB_PATH, '--output', '.'],
                cwd=dashboard_dir, capture_output=True, text=True, timeout=60
            )
            if build_result.returncode == 0:
                # git commit + push
                _sp.run(['git', 'add', '-A'], cwd=dashboard_dir, capture_output=True)
                commit_result = _sp.run(
                    ['git', 'commit', '-m', f'update dashboard {date_str}'],
                    cwd=dashboard_dir, capture_output=True, text=True
                )
                if 'nothing to commit' in commit_result.stdout:
                    log("  看板无变化，跳过推送")
                else:
                    push_result = _sp.run(
                        ['git', 'push', 'origin', 'main'],
                        cwd=dashboard_dir, capture_output=True, text=True, timeout=120
                    )
                    if push_result.returncode == 0:
                        log("✅ 看板已推送 → https://bily1258-design.github.io/football-dashboard/")
                    else:
                        log(f"  ⚠️ git push失败: {push_result.stderr[:100]}")
            else:
                log(f"  ⚠️ build失败: {build_result.stderr[:100]}")
        except Exception as e:
            log(f"⚠️ 自动部署失败: {e}")
    else:
        log("\n[DRY-RUN] 跳过文件生成")
        # Dry-run模式仍测试数据库写入逻辑
        save_to_database(all_results, date_str, dry_run=True)


if __name__ == "__main__":
    main()
