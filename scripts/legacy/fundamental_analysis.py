#!/usr/bin/env python3
"""
基本面分析模块 - 整合足彩专家预测推荐技能 + V3.4量化变量
包含伤停信息搜索、战意评估、近期状态、诱盘检测、冷门预警、V3.4量化变量等功能

功能:
- search_injury_info(): 搜索伤停信息
- assess_motivation(): 战意评估
- check_recent_form(): 近期状态
- detect_trap_odds(): 诱盘检测
- assess_cold_risk(): 冷门风险评估
- generate_risk_warning(): 生成风险预警标注
- analyze_fundamental(): 综合基本面分析
- calculate_ev_adjust(): EV修正因子计算（替代原星级调整）
- V3.4变量整合:
  - check_var_controversy(): VAR争议指数检查
  - check_midtable_away(): 中游无欲客场分类
  - check_secondary_league(): 次级联赛赛季末规则
  - check_warm_welcome(): 送温暖意愿指数

使用方式:
    from fundamental_analysis import analyze_fundamental, calculate_ev_adjust
    
    # 分析一场比赛的基本面（包含V3.4变量）
    result = analyze_fundamental(home_team, away_team, league, home_rank, away_rank, odds)
    # result包含: injury_info, motivation, recent_form, trap_odds, cold_risk, risk_warning
    # V3.4变量: var_controversy, midtable_away, secondary_league, warm_welcome
    
    # 根据基本面计算EV修正因子（替代信心星级调整）
    ev_adjust, reason = calculate_ev_adjust(result)
    # ev_adjust: ±0.02范围的EV修正因子（用于泊松分布模型）
    
参数:
    --no-v34: 禁用V3.4变量（节省搜索时间）
    --no-cross-validate: 禁用交叉验证（在daily_report.py中使用）
"""

import re
import time
from typing import Optional, Dict, List, Tuple
from datetime import datetime

# ========== V3.4变量全局开关 ==========
V34_ENABLED = True  # 默认启用
CROSS_VALIDATE_ENABLED = True  # 默认启用交叉验证

# ========== 联网搜索 ==========

def search_injury_info(home_team: str, away_team: str, league: str = "") -> Dict:
    """
    搜索伤停信息
    
    Args:
        home_team: 主队名称（中文或英文）
        away_team: 客队名称（中文或英文）
        league: 联赛名称（可选）
    
    Returns:
        {
            "home_injuries": [{"player": "球员名", "status": "伤/疑/停", "position": "位置"}, ...],
            "away_injuries": [...],
            "key_players_out": {"home": [], "away": []},  # 核心球员缺阵列表
            "summary": "伤停总结"
        }
    """
    from tools import search_web
    
    result = {
        "home_injuries": [],
        "away_injuries": [],
        "key_players_out": {"home": [], "away": []},
        "summary": "未获取到伤停信息",
        "has_data": False
    }
    
    try:
        # 构建搜索关键词
        keywords = [
            f"{home_team} {away_team} 伤停",
            f"{home_team} {away_team} 伤病名单",
            f"{home_team} {away_team} lineup injury",
        ]
        
        for keyword in keywords[:1]:  # 只搜索最重要的关键词
            search_results = search_web(
                query_list=[keyword],
                response_length="medium"
            )
            
            if search_results and len(search_results) > 0:
                for item in search_results[:3]:
                    content = item.get("content", "")
                    if any(team[:2] in content for team in [home_team, away_team] if len(team) >= 2):
                        # 解析伤停信息
                        injury_pattern = r"([^\s，。、]+?)(?:伤|缺|停|出战成疑)"
                        injuries = re.findall(injury_pattern, content)
                        
                        if "主场" in content or home_team[:2] in content:
                            for player in injuries[:5]:
                                if player not in [i["player"] for i in result["home_injuries"]]:
                                    status = "伤" if "伤" in content else ("疑" if "疑" in content else "停")
                                    result["home_injuries"].append({
                                        "player": player.strip(),
                                        "status": status,
                                        "position": "未知"
                                    })
                        
                        if "客场" in content or away_team[:2] in content:
                            for player in injuries[:5]:
                                if player not in [i["player"] for i in result["away_injuries"]]:
                                    status = "伤" if "伤" in content else ("疑" if "疑" in content else "停")
                                    result["away_injuries"].append({
                                        "player": player.strip(),
                                        "status": status,
                                        "position": "未知"
                                    })
                        
                        if result["home_injuries"] or result["away_injuries"]:
                            result["has_data"] = True
                            break
        
        # 生成总结
        if result["home_injuries"] or result["away_injuries"]:
            home_count = len(result["home_injuries"])
            away_count = len(result["away_injuries"])
            result["summary"] = f"主队{home_count}人/客队{away_count}人伤停"
            
            # 标记关键球员缺阵（简单heuristic：名字长度>2且非常见词）
            key_players = ["德布劳内", "姆巴佩", "哈兰德", "贝林厄姆", "萨拉赫", "凯恩", 
                          "莱万", "内马尔", "梅西", "C罗", "孙兴慜", "厄德高"]
            for team_key in ["home", "away"]:
                for injury in result.get(f"{team_key}_injuries", []):
                    if any(kp in injury["player"] for kp in key_players):
                        result["key_players_out"][team_key].append(injury["player"])
        
        time.sleep(1)  # 避免搜索过快
    
    except Exception as e:
        result["summary"] = f"伤停信息获取失败: {str(e)}"
    
    return result


def assess_motivation(home_team: str, away_team: str, league: str = "", 
                      home_rank: int = 0, away_rank: int = 0,
                      home_points: int = 0, away_points: int = 0,
                      season_matches: int = 30) -> Dict:
    """
    战意评估
    
    Args:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
        home_rank: 主队排名
        away_rank: 客队排名
        home_points: 主队积分
        away_points: 客队积分
        season_matches: 联赛总场次
    
    Returns:
        {
            "home_motivation": {"level": "高/中/低", "factors": [], "boost": 0.0~0.5},
            "away_motivation": {"level": "高/中/低", "factors": [], "boost": 0.0~0.5},
            "derby_match": True/False,
            "must_win": {"home": True/False, "away": True/False},
            "summary": "战意总结"
        }
    """
    result = {
        "home_motivation": {"level": "中", "factors": [], "boost": 0.0},
        "away_motivation": {"level": "中", "factors": [], "boost": 0.0},
        "derby_match": False,
        "must_win": {"home": False, "away": False},
        "summary": "双方战意正常"
    }
    
    # 德比战检测（简化版）
    derby_pairs = [
        ("曼联", "曼城", "曼彻斯特德比"),
        ("利物浦", "埃弗顿", "默西塞德德比"),
        ("阿森纳", "热刺", "北伦敦德比"),
        ("皇马", "巴萨", "国家德比"),
        ("国米", "AC米兰", "米兰德比"),
        ("拜仁", "多特", "国家德比"),
    ]
    
    for pair in derby_pairs:
        if pair[0] in home_team and pair[1] in away_team:
            result["derby_match"] = True
            result["summary"] = f"{pair[2]}，双方战意高涨"
            result["home_motivation"]["boost"] += 0.2
            result["away_motivation"]["boost"] += 0.2
            result["home_motivation"]["factors"].append("德比战")
            result["away_motivation"]["factors"].append("德比战")
            break
    
    # 保级区判断（假设联赛后4名降级）
    relegation_line = max(1, season_matches - 6)  # 约等于倒数第3-4名
    
    # 欧战区判断（假设前4名）
    europe_line = 4
    
    # 主队战意评估
    if home_rank > 0:
        if home_rank <= 4:  # 争冠/欧冠区
            result["home_motivation"]["level"] = "高"
            result["home_motivation"]["factors"].append("争冠/欧冠资格")
            result["home_motivation"]["boost"] += 0.3
        elif home_rank >= relegation_line:  # 保级区
            result["home_motivation"]["level"] = "高"
            result["home_motivation"]["factors"].append("保级压力")
            result["home_motivation"]["boost"] += 0.4
            result["must_win"]["home"] = True
        elif home_rank <= 7:  # 欧联/欧协联区
            result["home_motivation"]["level"] = "中高"
            result["home_motivation"]["factors"].append("欧战资格竞争")
            result["home_motivation"]["boost"] += 0.2
        else:
            result["home_motivation"]["factors"].append("无欲无求")
            result["home_motivation"]["boost"] -= 0.1
    
    # 客队战意评估
    if away_rank > 0:
        if away_rank <= 4:
            result["away_motivation"]["level"] = "高"
            result["away_motivation"]["factors"].append("争冠/欧冠资格")
            result["away_motivation"]["boost"] += 0.3
        elif away_rank >= relegation_line:
            result["away_motivation"]["level"] = "高"
            result["away_motivation"]["factors"].append("保级压力")
            result["away_motivation"]["boost"] += 0.4
            result["must_win"]["away"] = True
        elif away_rank <= 7:
            result["away_motivation"]["level"] = "中高"
            result["away_motivation"]["factors"].append("欧战资格竞争")
            result["away_motivation"]["boost"] += 0.2
        else:
            result["away_motivation"]["factors"].append("无欲无求")
            result["away_motivation"]["boost"] -= 0.1
    
    # 积分差距判断（高分 vs 低分）
    if home_points > 0 and away_points > 0:
        points_diff = home_points - away_points
        if points_diff >= 15:
            result["home_motivation"]["factors"].append("积分遥遥领先")
        elif points_diff <= -15:
            result["away_motivation"]["factors"].append("积分遥遥领先")
    
    # 生成总结
    if result["must_win"]["home"] or result["must_win"]["away"]:
        result["summary"] = "存在必胜战意球队"
    elif result["derby_match"]:
        pass  # 已在上面设置
    else:
        factors = []
        if result["home_motivation"]["factors"]:
            factors.append(f"主队: {'/'.join(result['home_motivation']['factors'])}")
        if result["away_motivation"]["factors"]:
            factors.append(f"客队: {'/'.join(result['away_motivation']['factors'])}")
        if factors:
            result["summary"] = "; ".join(factors)
    
    return result


def check_recent_form(team_name: str, league: str = "", is_home: bool = True) -> Dict:
    """
    检查近期状态
    
    Args:
        team_name: 球队名称
        league: 联赛名称（可选）
        is_home: 是否为主场
    
    Returns:
        {
            "recent_form": "5胜1平4负",
            "goals_for": 12,
            "goals_against": 8,
            "clean_sheets": 1,
            "form_rating": "良好/一般/低迷",
            "trend": "上升/平稳/下滑",
            "summary": "近6场4胜2平，状态良好"
        }
    """
    from tools import search_web
    
    result = {
        "recent_form": "未知",
        "goals_for": 0,
        "goals_against": 0,
        "clean_sheets": 0,
        "form_rating": "未知",
        "trend": "平稳",
        "summary": "未获取到近期状态",
        "has_data": False
    }
    
    try:
        keyword = f"{team_name} 近期战绩 近5场"
        search_results = search_web(
            query_list=[keyword],
            response_length="short"
        )
        
        if search_results:
            content = search_results[0].get("content", "")
            
            # 解析战绩模式
            form_pattern = r"(\d+)胜(\d+)平(\d+)负"
            match = re.search(form_pattern, content)
            if match:
                wins, draws, losses = int(match.group(1)), int(match.group(2)), int(match.group(3))
                total = wins + draws + losses
                if total > 0:
                    result["recent_form"] = f"{wins}胜{draws}平{losses}负"
                    result["has_data"] = True
                    
                    # 计算form rating
                    points = wins * 3 + draws
                    points_per_game = points / total
                    
                    if points_per_game >= 2.0:  # 场均2分以上
                        result["form_rating"] = "良好"
                    elif points_per_game >= 1.3:  # 场均1.3分以上
                        result["form_rating"] = "一般"
                    else:
                        result["form_rating"] = "低迷"
                    
                    # 最近3场趋势判断
                    if losses == 0 and wins >= draws:
                        result["trend"] = "上升"
                    elif losses >= 2:
                        result["trend"] = "下滑"
                    
                    result["summary"] = f"近{total}场{result['recent_form']}，状态{result['form_rating']}"
            
            # 尝试解析进球数
            goals_pattern = r"进(\d+)球失(\d+)球"
            goals_match = re.search(goals_pattern, content)
            if goals_match:
                result["goals_for"] = int(goals_match.group(1))
                result["goals_against"] = int(goals_match.group(2))
        
        time.sleep(1)
    
    except Exception as e:
        result["summary"] = f"近期状态获取失败: {str(e)}"
    
    return result


def detect_trap_odds(odds_home: float, odds_draw: float, odds_away: float,
                     market_home: float = 0.0, market_draw: float = 0.0, market_away: float = 0.0,
                     initial_odds: Tuple[float, float, float] = None) -> Dict:
    """
    诱盘检测
    
    Args:
        odds_home: 当前主胜赔率
        odds_draw: 当前平局赔率
        odds_away: 当前客胜赔率
        market_home: 市场隐含主胜概率
        market_draw: 市场隐含平局概率
        market_away: 市场隐含客胜概率
        initial_odds: 初始赔率（用于检测变化）
    
    Returns:
        {
            "is_trap": True/False,
            "trap_type": "降水诱上/升水赶下/升盘阻上/降盘诱下/无明显诱盘",
            "trap_probability": 0.0~1.0,
            "signals": ["信号1", "信号2", ...],
            "summary": "诱盘总结"
        }
    """
    result = {
        "is_trap": False,
        "trap_type": "无明显诱盘",
        "trap_probability": 0.0,
        "signals": [],
        "summary": "未检测到明显诱盘信号"
    }
    
    # 计算市场隐含概率（如果未提供）
    if market_home <= 0:
        total_implied = (1/odds_home) + (1/odds_draw) + (1/odds_away)
        market_home = (1/odds_home) / total_implied if odds_home > 0 else 0
        market_draw = (1/odds_draw) / total_implied if odds_draw > 0 else 0
        market_away = (1/odds_away) / total_implied if odds_away > 0 else 0
    
    # 检测赔率异常
    # 1. 低赔方赔付水位检测
    low_odds = min(odds_home, odds_draw, odds_away)
    if low_odds <= 1.5:
        implied_prob = (1/low_odds)
        # 正常返还率约0.90-0.95，过低可能有问题
        total_implied_prob = (1/odds_home) + (1/odds_draw) + (1/odds_away)
        payout_rate = 1.0 / total_implied_prob if total_implied_prob > 0 else 0
        
        if payout_rate < 0.88:
            result["signals"].append(f"返还率偏低({payout_rate:.2%})")
            result["trap_probability"] += 0.15
    
    # 2. 赔率变化检测
    if initial_odds:
        init_home, init_draw, init_away = initial_odds
        
        # 计算变化幅度
        if init_home > 0:
            home_change = (odds_home - init_home) / init_home
            draw_change = (odds_draw - init_draw) / init_draw
            away_change = (odds_away - init_away) / init_away
            
            # 降水诱上检测（主胜降水且幅度较大）
            if home_change < -0.03:  # 降水超过3%
                result["signals"].append(f"主胜降水{abs(home_change):.1%}")
                result["trap_probability"] += 0.25
                result["trap_type"] = "降水诱上"
            
            # 升水赶下检测（客胜/平局升水）
            if draw_change > 0.05 or away_change > 0.05:
                result["signals"].append(f"平赔↑{draw_change:.1%}/客赔↑{away_change:.1%}")
                result["trap_probability"] += 0.2
                result["trap_type"] = "升水赶下"
            
            # 反向变化检测（某项降水其他项升水）
            if home_change < 0 and draw_change > 0 and away_change > 0:
                result["signals"].append("反向变化(主降水/其他升水)")
                result["trap_probability"] += 0.3
                result["trap_type"] = "反向诱盘"
    
    # 3. 概率与赔率不匹配检测
    # 低赔方概率是否偏低（庄家不看好但赔率低）
    if low_odds == odds_home and market_home < 0.45:
        result["signals"].append("主胜赔率低但概率不支撑")
        result["trap_probability"] += 0.2
    
    if low_odds == odds_away and market_away < 0.45:
        result["signals"].append("客胜赔率低但概率不支撑")
        result["trap_probability"] += 0.2
    
    # 4. 深盘检测
    # 赔率小于1.30通常是让球深盘
    if odds_home <= 1.30:
        result["signals"].append("深盘让球(主胜≤1.30)")
        result["trap_probability"] += 0.15
    
    # 判断是否构成诱盘
    if result["trap_probability"] >= 0.5:
        result["is_trap"] = True
        if result["trap_probability"] >= 0.7:
            result["summary"] = f"⚠️ 诱盘风险高({result['trap_type']})"
        else:
            result["summary"] = f"⚠️ 疑似诱盘({result['trap_type']})"
    else:
        result["summary"] = "未检测到明显诱盘信号"
    
    # 限制在0-1范围内
    result["trap_probability"] = min(1.0, max(0.0, result["trap_probability"]))
    
    return result


def assess_cold_risk(home_team: str, away_team: str, league: str = "",
                     home_rank: int = 0, away_rank: int = 0,
                     home_injuries: List = None, away_injuries: List = None,
                     recent_matches_days: int = 0) -> Dict:
    """
    冷门风险评估
    
    Args:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
        home_rank: 主队排名
        away_rank: 客队排名
        home_injuries: 主队伤停列表
        away_injuries: 客队伤停列表
        recent_matches_days: 最近一场比赛距今天数
    
    Returns:
        {
            "cold_risk": "高/中/低",
            "cold_probability": 0.0~1.0,
            "factors": [{"type": "factor", "impact": 0.0~0.3}, ...],
            "cold_signals": ["信号1", "信号2", ...],
            "summary": "冷门风险总结"
        }
    """
    result = {
        "cold_risk": "低",
        "cold_probability": 0.0,
        "factors": [],
        "cold_signals": [],
        "summary": "冷门风险较低"
    }
    
    home_injuries = home_injuries or []
    away_injuries = away_injuries or []
    
    # 1. 核心球员伤停检测
    key_players = ["德布劳内", "姆巴佩", "哈兰德", "贝林厄姆", "萨拉赫", "凯恩",
                   "莱万", "内马尔", "梅西", "C罗", "孙兴慜", "厄德高", "罗德里",
                   "维尼修斯", "姆巴佩", "萨卡", "赖斯"]
    
    for team_key, injuries in [("home", home_injuries), ("away", away_injuries)]:
        key_out_count = 0
        for injury in injuries:
            if any(kp in injury.get("player", "") for kp in key_players):
                key_out_count += 1
        
        if key_out_count >= 2:
            result["cold_signals"].append(f"{'主队' if team_key == 'home' else '客队'}核心球员伤停≥2人")
            result["cold_probability"] += 0.35
            result["factors"].append({"type": "核心伤停", "impact": 0.35})
        elif key_out_count == 1:
            result["cold_signals"].append(f"{'主队' if team_key == 'home' else '客队'}1名核心球员伤停")
            result["cold_probability"] += 0.15
            result["factors"].append({"type": "核心伤停", "impact": 0.15})
    
    # 2. 排名差距检测（强队客场或弱队主场可能爆冷）
    if home_rank > 0 and away_rank > 0:
        rank_diff = home_rank - away_rank
        if rank_diff <= -5:  # 主队排名低5名以上
            result["cold_signals"].append(f"主队排名({home_rank})低于客队({away_rank})")
            result["cold_probability"] += 0.2
            result["factors"].append({"type": "排名差距", "impact": 0.2})
        elif rank_diff >= 5:  # 主队排名高5名以上
            result["cold_signals"].append(f"主队排名({home_rank})高于客队({away_rank})")
            result["cold_probability"] += 0.15
            result["factors"].append({"type": "强队可能轮换", "impact": 0.15})
    
    # 3. 赛程密集度检测
    if recent_matches_days <= 3 and recent_matches_days > 0:
        result["cold_signals"].append(f"近期赛程密集({recent_matches_days}天内有比赛)")
        result["cold_probability"] += 0.25
        result["factors"].append({"type": "赛程密集", "impact": 0.25})
    
    # 4. 无欲无求检测
    relegation_line = 17  # 假设倒数第4开始无欲无求
    if home_rank >= relegation_line:
        result["cold_signals"].append("主队无欲无求")
        result["cold_probability"] += 0.3
        result["factors"].append({"type": "无欲无求", "impact": 0.3})
    
    # 5. 德比战/特殊比赛
    derby_teams = ["德比", "德比战"]
    if any(d in f"{home_team}vs{away_team}" for d in derby_teams):
        result["cold_signals"].append("德比战不确定性高")
        result["cold_probability"] += 0.2
        result["factors"].append({"type": "德比战", "impact": 0.2})
    
    # 判断风险等级
    result["cold_probability"] = min(1.0, max(0.0, result["cold_probability"]))
    
    if result["cold_probability"] >= 0.6:
        result["cold_risk"] = "高"
        result["summary"] = "❄️ 冷门风险高"
    elif result["cold_probability"] >= 0.4:
        result["cold_risk"] = "中"
        result["summary"] = "❄️ 存在一定冷门风险"
    elif result["cold_probability"] >= 0.2:
        result["cold_risk"] = "低"
        result["summary"] = "⚠️ 冷门风险较低"
    else:
        result["summary"] = "✅ 冷门风险低"
    
    return result


def generate_risk_warning(fundamental_data: Dict, trap_data: Dict = None, 
                          cold_data: Dict = None) -> str:
    """
    生成风险预警标注
    
    Args:
        fundamental_data: 基本面分析数据
        trap_data: 诱盘检测数据
        cold_data: 冷门风险数据
    
    Returns:
        风险预警标注字符串，如 "⚠️ 诱盘风险 | 🎯 冷门风险"
    """
    warnings = []
    
    # 诱盘预警
    if trap_data and trap_data.get("is_trap"):
        warnings.append(f"🎯{trap_data.get('trap_type', '诱盘')}")
    
    # 冷门预警
    if cold_data and cold_data.get("cold_risk") == "高":
        warnings.append("❄️ 冷门风险")
    elif cold_data and cold_data.get("cold_risk") == "中":
        warnings.append("⚠️ 冷门注意")
    
    # 基本面重要信息
    motivation = fundamental_data.get("motivation", {})
    if motivation.get("derby_match"):
        warnings.append("🔥 德比战")
    
    if motivation.get("must_win", {}).get("home"):
        warnings.append("💪 主队必胜")
    if motivation.get("must_win", {}).get("away"):
        warnings.append("💪 客队必胜")
    
    # 伤停信息
    key_out = fundamental_data.get("injury_info", {}).get("key_players_out", {})
    if key_out.get("home"):
        warnings.append(f"⚠️ 主队{'/'.join(key_out['home'][:2])}缺阵")
    if key_out.get("away"):
        warnings.append(f"⚠️ 客队{'/'.join(key_out['away'][:2])}缺阵")
    
    if warnings:
        return " | ".join(warnings[:3])  # 最多3个预警
    return ""


# ========== 综合分析入口 ==========

def analyze_fundamental(home_team: str, away_team: str, league: str = "",
                       home_rank: int = 0, away_rank: int = 0,
                       home_points: int = 0, away_points: int = 0,
                       season_matches: int = 30,
                       odds: Tuple[float, float, float] = None,
                       use_cache: bool = True) -> Dict:
    """
    综合基本面分析（整合所有分析模块）
    
    Args:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
        home_rank: 主队排名
        away_rank: 客队排名
        home_points: 主队积分
        away_points: 客队积分
        season_matches: 联赛总场次
        odds: (主胜赔率, 平局赔率, 客胜赔率)
        use_cache: 是否使用缓存（避免重复搜索）
    
    Returns:
        综合分析结果字典
    """
    # 简单内存缓存
    if not hasattr(analyze_fundamental, "_cache"):
        analyze_fundamental._cache = {}
    
    cache_key = f"{home_team}|{away_team}|{league}"
    if use_cache and cache_key in analyze_fundamental._cache:
        return analyze_fundamental._cache[cache_key]
    
    result = {
        "injury_info": {},
        "motivation": {},
        "home_form": {},
        "away_form": {},
        "trap_odds": {},
        "cold_risk": {},
        "risk_warning": "",
        "ev_adjust": 0.0,  # EV修正因子（替代原星级调整）
        "ev_adjust_reason": "",  # 修正原因
    }
    
    # 1. 伤停信息
    result["injury_info"] = search_injury_info(home_team, away_team, league)
    
    # 2. 战意评估
    result["motivation"] = assess_motivation(
        home_team, away_team, league,
        home_rank, away_rank, home_points, away_points, season_matches
    )
    
    # 3. 近期状态
    result["home_form"] = check_recent_form(home_team, league, is_home=True)
    result["away_form"] = check_recent_form(away_team, league, is_home=False)
    
    # 4. 诱盘检测（如果有赔率数据）
    if odds:
        result["trap_odds"] = detect_trap_odds(
            odds[0], odds[1], odds[2]
        )
    
    # 5. 冷门风险评估
    home_injuries = result["injury_info"].get("home_injuries", [])
    away_injuries = result["injury_info"].get("away_injuries", [])
    result["cold_risk"] = assess_cold_risk(
        home_team, away_team, league,
        home_rank, away_rank,
        home_injuries, away_injuries
    )
    
    # 6. 生成风险预警
    result["risk_warning"] = generate_risk_warning(result)
    
    # 7. 计算EV修正因子
    result["ev_adjust"], result["ev_adjust_reason"] = calculate_ev_adjust_factor(result)
    
    # 缓存结果
    analyze_fundamental._cache[cache_key] = result
    
    return result


def calculate_ev_adjust_factor(fundamental_data: Dict) -> Tuple[float, str]:
    """
    根据基本面数据计算EV修正因子（替代原信心星级调整）
    
    EV修正因子用于泊松分布模型，范围约±0.02
    
    规则:
    - 基本面正面（战意强、伤停有利）→ ev_adjust += 0.02
    - 基本面负面（伤停严重、战意低）→ ev_adjust -= 0.02
    - 冷门风险高 → ev_adjust -= 0.01（轻微叠加，冷门打折已在value_bet.py实现）
    - 诱盘预警 → ev_adjust -= 0.015
    
    Returns:
        (ev_adjust, reason_str) - EV修正因子和建议原因
    """
    ev_adjust = 0.0
    reasons = []
    
    # 1. 伤停影响（EV修正）
    injury_info = fundamental_data.get("injury_info", {})
    key_out = injury_info.get("key_players_out", {})
    
    if key_out.get("home") and len(key_out["home"]) >= 2:
        ev_adjust -= 0.02
        reasons.append(f"主队核心伤停{len(key_out['home'])}人")
    elif key_out.get("away") and len(key_out["away"]) >= 2:
        ev_adjust -= 0.02
        reasons.append(f"客队核心伤停{len(key_out['away'])}人")
    elif key_out.get("home") or key_out.get("away"):
        ev_adjust -= 0.01
        reasons.append("单队核心球员伤停")
    
    # 2. 战意影响（EV修正）
    motivation = fundamental_data.get("motivation", {})
    if motivation.get("must_win", {}).get("home"):
        ev_adjust += 0.02
        reasons.append("主队必胜战意")
    if motivation.get("must_win", {}).get("away"):
        ev_adjust += 0.02
        reasons.append("客队必胜战意")
    if motivation.get("derby_match"):
        ev_adjust -= 0.01
        reasons.append("德比战不确定性")
    
    # 3. 诱盘影响（EV修正）
    trap = fundamental_data.get("trap_odds", {})
    if trap.get("is_trap"):
        trap_prob = trap.get("trap_probability", 0)
        if trap_prob >= 0.6:
            ev_adjust -= 0.015
            reasons.append(f"诱盘风险高({trap_prob:.0%})")
        elif trap_prob >= 0.4:
            ev_adjust -= 0.01
            reasons.append(f"疑似诱盘({trap_prob:.0%})")
    
    # 4. 冷门风险（EV修正，轻微叠加）
    cold = fundamental_data.get("cold_risk", {})
    if cold.get("cold_risk") == "高":
        ev_adjust -= 0.01
        reasons.append("冷门风险高")
    elif cold.get("cold_risk") == "中":
        ev_adjust -= 0.005
        reasons.append("冷门风险中等")
    
    # 限制EV调整范围（通常在±0.05以内）
    ev_adjust = max(-0.05, min(0.05, ev_adjust))
    
    return ev_adjust, "; ".join(reasons) if reasons else "基本面无明显调整"


def adjust_confidence_stars(original_stars: float, fundamental_data: Dict) -> Tuple[float, str]:
    """
    [已废弃] 根据基本面调整信心星级
    
    ⚠️ 已废弃，请使用 calculate_ev_adjust_factor() 替代
    此函数保留仅用于向后兼容，value_bet.py可能仍在使用
    
    Args:
        original_stars: 原始星级（1-5）
        fundamental_data: 基本面分析数据
    
    Returns:
        (调整后星级, 调整说明) - 仅保留签名兼容，实际使用EV修正
    """
    # 内部调用新的EV修正因子计算
    ev_adjust, reason = calculate_ev_adjust_factor(fundamental_data)
    
    # 将EV修正因子转换回近似星级调整（仅用于兼容输出）
    # EV调整±0.02约对应星级±0.5的逻辑
    adjustment = ev_adjust * 25  # 0.02 * 25 = 0.5星
    
    adjusted_stars = original_stars + adjustment
    
    # 限制范围
    adjusted_stars = max(1.0, min(5.0, adjusted_stars))
    
    # 四舍五入到0.5
    adjusted_stars = round(adjusted_stars * 2) / 2
    
    return adjusted_stars, reason


def calculate_ev_adjust(fundamental_data: Dict) -> Tuple[float, str]:
    """
    根据基本面计算EV修正因子的便捷包装函数
    
    ⚠️ 此函数是 calculate_ev_adjust_factor 的别名，推荐直接使用后者
    
    Returns:
        (ev_adjust, reason_str) - EV修正因子和建议原因
    """
    return calculate_ev_adjust_factor(fundamental_data)


def get_confidence_stars_text(stars: float) -> str:
    """将星级数值转换为显示文本"""
    if stars >= 5:
        return "⭐⭐⭐⭐⭐"
    elif stars >= 4.5:
        return "⭐⭐⭐⭐☆"
    elif stars >= 4:
        return "⭐⭐⭐⭐"
    elif stars >= 3.5:
        return "⭐⭐⭐☆"
    elif stars >= 3:
        return "⭐⭐⭐"
    elif stars >= 2.5:
        return "⭐⭐☆"
    elif stars >= 2:
        return "⭐⭐"
    elif stars >= 1.5:
        return "⭐☆"
    else:
        return "⭐"


# ========== 战术分析（简化版） ==========

def get_tactical_preview(home_team: str, away_team: str, league: str = "") -> Dict:
    """
    获取战术前瞻信息（用于4星+场次）
    
    Args:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
    
    Returns:
        {
            "formation": "预期阵型",
            "key_matchup": "关键对位",
            "tactical_hint": "战术提示",
            "summary": "战术总结"
        }
    """
    from tools import search_web
    
    result = {
        "formation": "未知",
        "key_matchup": "",
        "tactical_hint": "",
        "summary": "未获取到战术信息",
        "has_data": False
    }
    
    try:
        keyword = f"{home_team} vs {away_team} 战术 阵型 分析"
        search_results = search_web(
            query_list=[keyword],
            response_length="medium"
        )
        
        if search_results:
            content = search_results[0].get("content", "")
            
            # 解析阵型
            formation_pattern = r"(\d+-\d+-\d+|\d+\+\d+\+\d+)"
            formations = re.findall(formation_pattern, content)
            if formations:
                result["formation"] = f"{home_team[:3]}:{formations[0]} vs {away_team[:3]}:{formations[1] if len(formations) > 1 else formations[0]}"
                result["has_data"] = True
            
            # 解析关键对位
            matchup_keywords = ["对位", "对决", "关键", "克制"]
            for kw in matchup_keywords:
                if kw in content:
                    result["key_matchup"] = content[max(0, content.index(kw)-20):content.index(kw)+30]
                    break
            
            # 生成总结
            if result["has_data"]:
                result["summary"] = f"战术：{result['formation']}"
                if result["key_matchup"]:
                    result["summary"] += f" | {result['key_matchup']}"
        
        time.sleep(1)
    
    except Exception as e:
        result["summary"] = f"战术信息获取失败: {str(e)}"
    
    return result


# ========== 工具函数 ==========

def clear_cache():
    """清除基本面分析缓存"""
    if hasattr(analyze_fundamental, "_cache"):
        analyze_fundamental._cache.clear()


def format_fundamental_summary(fundamental_data: Dict) -> str:
    """
    格式化基本面摘要（用于输出）
    
    Args:
        fundamental_data: 基本面分析数据
    
    Returns:
        格式化的摘要字符串
    """
    lines = []
    
    # 伤停
    injury = fundamental_data.get("injury_info", {})
    if injury.get("has_data"):
        lines.append(f"伤停: {injury.get('summary', '')}")
    else:
        lines.append("伤停: 未获取")
    
    # 战意
    motivation = fundamental_data.get("motivation", {})
    summary = motivation.get("summary", "")
    if summary:
        lines.append(f"战意: {summary}")
    
    # 近期状态
    home_form = fundamental_data.get("home_form", {})
    away_form = fundamental_data.get("away_form", {})
    if home_form.get("has_data"):
        lines.append(f"主队状态: {home_form.get('summary', '')}")
    if away_form.get("has_data"):
        lines.append(f"客队状态: {away_form.get('summary', '')}")
    
    # 诱盘
    trap = fundamental_data.get("trap_odds", {})
    if trap.get("is_trap"):
        lines.append(f"⚠️ {trap.get('trap_type', '诱盘风险')}")
    
    # 冷门
    cold = fundamental_data.get("cold_risk", {})
    if cold.get("cold_risk") != "低":
        lines.append(f"{cold.get('summary', '')}")
    
    return " | ".join(lines) if lines else "基本面正常"


# ========== V3.4量化变量整合 ==========

def check_var_controversy(home_team: str, away_team: str, league: str = "") -> Dict:
    """
    V3.4: 检查VAR争议指数
    重大改判后15分钟内不利方防守-0.20
    
    VAR判罚类型权重:
    - 点球改判: ×1.5
    - 红牌改判: ×1.5
    - 进球取消: ×1.3
    - 进球确认: ×1.2
    - 越位取消: ×1.0
    
    争议指数: 0-1正常 / 2争议 / ≥3高争议
    
    Returns:
        {
            "has_var_incident": True/False,
            "var_type": "点球/红牌/进球...",
            "var_weight": 1.0~1.5,
            "controversy_index": 0~3,
            "defensive_penalty": 0.0~0.36,
            "summary": "描述"
        }
    """
    from tools import search_web
    
    result = {
        "has_var_incident": False,
        "var_type": "",
        "var_weight": 1.0,
        "controversy_index": 0,
        "defensive_penalty": 0.0,
        "summary": "未检测到VAR争议",
        "ev_adjust": 0.0  # EV修正因子
    }
    
    try:
        # 搜索VAR争议
        keywords = [
            f"{home_team} {away_team} VAR 争议",
            f"{home_team} {away_team} VAR 改判",
            f"{home_team} {away_team} var controversy penalty",
        ]
        
        for keyword in keywords[:2]:
            search_results = search_web(
                query_list=[keyword],
                response_length="short"
            )
            
            if search_results and len(search_results) > 0:
                content = search_results[0].get("content", "")
                
                # 检测VAR类型
                if "点球" in content and "改判" in content:
                    result["has_var_incident"] = True
                    result["var_type"] = "点球改判"
                    result["var_weight"] = 1.5
                elif "红牌" in content and ("改判" in content or "VAR" in content):
                    result["has_var_incident"] = True
                    result["var_type"] = "红牌改判"
                    result["var_weight"] = 1.5
                elif "进球取消" in content:
                    result["has_var_incident"] = True
                    result["var_type"] = "进球取消"
                    result["var_weight"] = 1.3
                elif "进球确认" in content or "VAR确认" in content:
                    result["has_var_incident"] = True
                    result["var_type"] = "进球确认"
                    result["var_weight"] = 1.2
                
                # 检测争议指数（球员抗议次数等）
                controversy_indicators = ["抗议", "不满", "争议", "申诉", "抗议", "愤怒"]
                result["controversy_index"] = sum(1 for ind in controversy_indicators if ind in content)
                result["controversy_index"] = min(3, result["controversy_index"])
                
                if result["has_var_incident"]:
                    # 计算防守惩罚
                    # 最终偏移 = 基础偏移(-0.20) × 判罚权重 × (1 + 争议指数×0.1)
                    base_penalty = -0.20
                    result["defensive_penalty"] = base_penalty * result["var_weight"] * (1 + result["controversy_index"] * 0.1)
                    
                    # 争议指数>2时，EV下调
                    if result["controversy_index"] >= 2:
                        result["summary"] = f"⚠️ VAR{result['var_type']}争议({result['controversy_index']}级)"
                        result["ev_adjust"] = -0.015  # EV修正-0.015
                    else:
                        result["summary"] = f"VAR{result['var_type']}，动量偏移有效"
                        result["ev_adjust"] = -0.01
                    break
        
        time.sleep(0.5)
    
    except Exception as e:
        result["summary"] = f"VAR检查失败: {str(e)}"
    
    return result


def check_midtable_away(home_team: str, away_team: str, league: str = "",
                        home_rank: int = 0, away_rank: int = 0,
                        season_matches: int = 30, league_matches_done: int = 0) -> Dict:
    """
    V3.4: 中游无欲客场分类与赛季末强化
    
    分类:
    - A类(认真): 排名8-12，无压力，有合同 → 进攻-0.05，防守-0.05
    - B类(放松): 排名8-12，无压力，心不在焉 → 进攻-0.10，防守-0.15
    - C类(崩盘): 排名8-12 + 近3场1平2负 → 进攻-0.15，防守-0.20
    
    赛季末强化: ×1.3
    - A类赛季末: 进攻-0.065，防守-0.065
    - B类赛季末: 进攻-0.13，防守-0.20
    - C类赛季末: 进攻-0.20，防守-0.26
    
    Returns:
        {
            "is_midtable_away": True/False,
            "team_type": "A/B/C",
            "team_type_name": "认真/放松/崩盘",
            "is_season_end": True/False,
            "offensive_penalty": 0.0~0.20,
            "defensive_penalty": 0.0~0.26,
            "confidence_adjust": 0.0,
            "summary": "描述"
        }
    """
    from tools import search_web
    
    result = {
        "is_midtable_away": False,
        "team_type": "",
        "team_type_name": "",
        "is_season_end": False,
        "offensive_penalty": 0.0,
        "defensive_penalty": 0.0,
        "ev_adjust": 0.0,
        "summary": "非中游无欲客场"
    }
    
    # 判断赛季末（赛程≥85%）
    if league_matches_done > 0:
        season_progress = league_matches_done / season_matches
        if season_progress >= 0.85:
            result["is_season_end"] = True
    
    # 判断客队是否中游队（排名6-14，无保级无争冠）
    if away_rank > 0:
        relegation_line = max(1, season_matches - 6)
        europe_line = 4
        
        # 中游队判断: 排名6-14，不在降级区也不在欧战区
        is_midtable = (6 <= away_rank <= 14)
        is_safe = (away_rank > relegation_line)
        is_not_top = (away_rank > europe_line)
        
        if is_midtable and is_safe and is_not_top:
            result["is_midtable_away"] = True
            
            # 检测类型
            try:
                keyword = f"{away_team} 近期战绩 近3场"
                search_results = search_web(
                    query_list=[keyword],
                    response_length="short"
                )
                
                has_bad_form = False
                if search_results:
                    content = search_results[0].get("content", "")
                    # 检测近3场1平2负的崩盘迹象
                    if any(x in content for x in ["1平2负", "2负1平", "连败", "不胜"]):
                        has_bad_form = True
                
                # 分类
                if has_bad_form:
                    result["team_type"] = "C"
                    result["team_type_name"] = "赛季末崩盘"
                    base_off = -0.15
                    base_def = -0.20
                else:
                    result["team_type"] = "B"
                    result["team_type_name"] = "无欲且放松"
                    base_off = -0.10
                    base_def = -0.15
                
                # 赛季末强化
                if result["is_season_end"]:
                    result["offensive_penalty"] = base_off * 1.3
                    result["defensive_penalty"] = base_def * 1.3
                    result["summary"] = f"⚠️ 客队({away_team})赛季末中游-{result['team_type_name']}，胜率×0.65"
                    result["ev_adjust"] = -0.02  # EV修正-0.02
                else:
                    result["offensive_penalty"] = base_off
                    result["defensive_penalty"] = base_def
                    result["summary"] = f"客队({away_team})中游-{result['team_type_name']}"
                    result["ev_adjust"] = -0.01
                
                time.sleep(0.5)
            
            except Exception as e:
                result["summary"] = f"中游无欲检查失败: {str(e)}"
    
    return result


def check_secondary_league(home_team: str, league: str = "",
                           home_rank: int = 0, season_matches: int = 30,
                           league_matches_done: int = 0) -> Dict:
    """
    V3.4: 次级联赛赛季末强队规则
    
    葡超/荷甲赛季末规则:
    - 赛季末(赛程≥80%) + 主队排名联赛前4 → xG×0.80
    
    北欧联赛:
    - 降级规则差异，保级驱动提前触发
    
    Returns:
        {
            "is_secondary_league": True/False,
            "league_type": "葡超/荷甲/北欧/其他",
            "is_season_end": True/False,
            "is_top4_home": True/False,
            "xg_multiplier": 1.0,  # 0.80或其他
            "confidence_adjust": 0.0,
            "summary": "描述"
        }
    """
    result = {
        "is_secondary_league": False,
        "league_type": "",
        "is_season_end": False,
        "is_top4_home": False,
        "xg_multiplier": 1.0,
        "ev_adjust": 0.0,
        "summary": "非次级联赛或非赛季末"
    }
    
    # 次级联赛列表
    secondary_leagues = {
        "葡超": ["葡超", "葡萄牙", "pt.1"],
        "荷甲": ["荷甲", "荷兰", "nl.1"],
        "北欧": ["瑞超", "挪超", "芬超", "瑞典超"]
    }
    
    # 判断联赛类型
    for league_type, keywords in secondary_leagues.items():
        if any(kw.lower() in league.lower() for kw in keywords):
            result["is_secondary_league"] = True
            result["league_type"] = league_type
            break
    
    if not result["is_secondary_league"]:
        return result
    
    # 判断赛季末
    if league_matches_done > 0:
        season_progress = league_matches_done / season_matches
        # 葡超/荷甲: 80%赛季末 / 北欧: 75%
        threshold = 0.75 if result["league_type"] == "北欧" else 0.80
        if season_progress >= threshold:
            result["is_season_end"] = True
    
    # 判断主队是否强队（排名1-4）
    if home_rank > 0 and home_rank <= 4:
        result["is_top4_home"] = True
    
    # 应用规则
    if result["is_secondary_league"] and result["is_season_end"] and result["is_top4_home"]:
        if result["league_type"] in ["葡超", "荷甲"]:
            result["xg_multiplier"] = 0.80
            result["summary"] = f"⚠️ {result['league_type']}赛季末强队，主队xG×0.80"
            result["ev_adjust"] = -0.02  # EV修正-0.02
        elif result["league_type"] == "北欧":
            result["xg_multiplier"] = 0.85  # 北欧稍宽松
            result["summary"] = f"⚠️ {result['league_type']}赛季末，保级驱动提前触发"
            result["ev_adjust"] = -0.01
    
    return result


def check_warm_welcome(home_team: str, away_team: str, league: str = "",
                       home_rank: int = 0, away_rank: int = 0,
                       season_matches: int = 30) -> Dict:
    """
    V3.4: 送温暖意愿指数
    
    情景:
    - 中游主队无欲 + 客队强驱动（保级/争冠） → 客队进攻+0.15~0.20
    - 主队放水给保级对手 → 客队进攻+0.20
    - 双方有历史恩怨 → 无送温暖可能
    
    Returns:
        {
            "is_warm_welcome": True/False,
            "warm_index": 0.0~0.20,
            "away_attack_boost": 0.0~0.20,
            "confidence_adjust": 0.0,
            "summary": "描述"
        }
    """
    result = {
        "is_warm_welcome": False,
        "warm_index": 0.0,
        "away_attack_boost": 0.0,
        "ev_adjust": 0.0,
        "summary": "无送温暖迹象"
    }
    
    # 判断条件
    relegation_line = max(1, season_matches - 6)
    
    # 主队中游无欲（排名8-14，无保级无争冠）
    home_midtable = (8 <= home_rank <= 14) and (home_rank > relegation_line) and (home_rank > 4)
    
    # 客队强驱动（保级或争冠）
    away_strong_motive = (away_rank <= relegation_line) or (away_rank <= 4)
    
    if home_midtable and away_strong_motive:
        result["is_warm_welcome"] = True
        
        # 判断驱动类型
        if away_rank <= relegation_line:
            # 客队保级，动力更强
            result["warm_index"] = 0.20
            result["away_attack_boost"] = 0.20
            result["summary"] = f"💡 主队送温暖，客队({away_team})保级驱动+0.20"
            result["ev_adjust"] = 0.015  # EV修正+0.015（客队机会大）
        else:
            # 客队争冠
            result["warm_index"] = 0.15
            result["away_attack_boost"] = 0.15
            result["summary"] = f"💡 主队送温暖，客队({away_team})争冠驱动+0.15"
            result["ev_adjust"] = 0.01
    
    return result


def set_v34_enabled(enabled: bool):
    """设置V3.4变量开关"""
    global V34_ENABLED
    V34_ENABLED = enabled


def set_cross_validate_enabled(enabled: bool):
    """设置交叉验证开关"""
    global CROSS_VALIDATE_ENABLED
    CROSS_VALIDATE_ENABLED = enabled


# ========== V3.4变量综合评估 ==========

def analyze_v34_variables(home_team: str, away_team: str, league: str = "",
                          home_rank: int = 0, away_rank: int = 0,
                          home_points: int = 0, away_points: int = 0,
                          season_matches: int = 30, league_matches_done: int = 0,
                          use_cache: bool = True) -> Dict:
    """
    V3.4变量综合分析
    
    只对4星+场次调用（节省资源）
    
    Returns:
        {
            "var_controversy": {...},
            "midtable_away": {...},
            "secondary_league": {...},
            "warm_welcome": {...},
            "total_ev_adjust": 0.0,  # EV修正因子汇总
            "warnings": [],
            "summary": "描述"
        }
    """
    if not V34_ENABLED:
        return {
            "var_controversy": {},
            "midtable_away": {},
            "secondary_league": {},
            "warm_welcome": {},
            "total_ev_adjust": 0.0,
            "warnings": [],
            "summary": "V3.4变量已禁用"
        }
    
    # 简单缓存
    if not hasattr(analyze_v34_variables, "_cache"):
        analyze_v34_variables._cache = {}
    
    cache_key = f"v34|{home_team}|{away_team}|{league}"
    if use_cache and cache_key in analyze_v34_variables._cache:
        return analyze_v34_variables._cache[cache_key]
    
    result = {
        "var_controversy": check_var_controversy(home_team, away_team, league),
        "midtable_away": check_midtable_away(home_team, away_team, league, home_rank, away_rank, season_matches, league_matches_done),
        "secondary_league": check_secondary_league(home_team, league, home_rank, season_matches, league_matches_done),
        "warm_welcome": check_warm_welcome(home_team, away_team, league, home_rank, away_rank, season_matches),
        "total_ev_adjust": 0.0,
        "warnings": [],
        "summary": ""
    }
    
    # 汇总EV修正因子
    for key in ["var_controversy", "midtable_away", "secondary_league", "warm_welcome"]:
        ev_adj = result[key].get("ev_adjust", 0.0)
        result["total_ev_adjust"] += ev_adj
        
        # 收集警告
        summary = result[key].get("summary", "")
        if summary and "失败" not in summary and summary != "非中游无欲客场" and summary != "无送温暖迹象":
            result["warnings"].append(summary)
    
    # 缓存
    analyze_v34_variables._cache[cache_key] = result
    
    return result


# ========== 竞猜足球分析助手6维评分整合 ==========

# 6维评分全局开关
DIM6_ENABLED = True


def set_dim6_enabled(enabled: bool):
    """设置6维评分开关"""
    global DIM6_ENABLED
    DIM6_ENABLED = enabled


def search_market_value(home_team: str, away_team: str, league: str = "") -> Dict:
    """
    搜索身价数据
    
    Returns:
        {
            "home_value": 身价(万欧元)或None,
            "away_value": 身价(万欧元)或None,
            "value_ratio": 主队/客队比例,
            "summary": "描述"
        }
    """
    from tools import search_web
    
    result = {
        "home_value": None,
        "away_value": None,
        "value_ratio": 1.0,
        "summary": "未获取到身价数据"
    }
    
    try:
        # 搜索身价
        keyword = f"{home_team} {away_team} 球队总身价 万欧元"
        search_results = search_web(query_list=[keyword], response_length="medium")
        
        if search_results:
            for item in search_results[:3]:
                content = item.get("content", "")
                
                # 提取身价（尝试匹配数值）
                value_pattern = r"(\d+(?:\.\d+)?)\s*(?:亿|万)\s*欧元"
                values = re.findall(value_pattern, content)
                
                # 简单解析：如果同时提到两队，取较近的
                if len(values) >= 2:
                    try:
                        result["home_value"] = float(values[0])
                        result["away_value"] = float(values[1])
                        result["summary"] = f"主队{values[0]}万/客队{values[1]}万欧元"
                    except:
                        pass
                elif len(values) == 1:
                    # 只找到一个
                    pass
                
                if result["home_value"] and result["away_value"]:
                    break
        
        # 计算比例
        if result["home_value"] and result["away_value"] and result["away_value"] > 0:
            result["value_ratio"] = result["home_value"] / result["away_value"]
        
        time.sleep(1)
    
    except Exception as e:
        result["summary"] = f"身价获取失败: {str(e)}"
    
    return result


def calculate_value_score(home_value: Optional[float], away_value: Optional[float]) -> Tuple[int, int, str]:
    """
    身价评分（权重20%）
    
    规则:
    - 领先超10%得12-20分（主场）
    - 领先超10%得12-20分（客场）
    - 相近8-10分
    
    Returns: (home_score, away_score, summary)
    """
    if home_value is None or away_value is None or away_value == 0:
        return 10, 10, "身价数据缺失，双方各10分"
    
    ratio = home_value / away_value
    
    if ratio >= 1.10:  # 主场领先超10%
        home_score = 12 + int(min((ratio - 1.10) * 40, 8))  # 12-20分
        away_score = 20 - home_score + 20  # 保持总分20
    elif ratio >= 0.90:  # 相近（90%-110%）
        home_score = 8 + int((ratio - 0.90) * 10)  # 8-10分
        away_score = 20 - home_score
    else:  # 客场领先
        away_score = 12 + int((1/ratio - 1.10) * 40)
        away_score = min(away_score, 20)
        home_score = 20 - away_score
    
    summary = f"身价比例{ratio:.2f}，主队{home_score}分/客队{away_score}分"
    return home_score, away_score, summary


def calculate_squad_score(home_injuries: List[Dict], away_injuries: List[Dict], 
                         home_key_out: List, away_key_out: List) -> Tuple[int, int, str]:
    """
    阵容完整度评分（权重20%）
    
    规则:
    - 无伤停12-20分
    - 轻伤9-11分
    - 核心伤停5-8分
    
    Returns: (home_score, away_score, summary)
    """
    def calc_team_score(injuries: List, key_out: List) -> Tuple[int, str]:
        injury_count = len(injuries)
        key_count = len(key_out)
        
        if injury_count == 0:
            return 18, "无伤停"
        elif key_count >= 2:
            return 5, f"核心球员{key_count}人缺阵"
        elif key_count == 1:
            return 7, f"核心球员缺阵"
        elif injury_count <= 2:
            return 11, f"轻伤{injury_count}人"
        else:
            return 9, f"伤停{injury_count}人"
    
    home_score, home_status = calc_team_score(home_injuries, home_key_out)
    away_score, away_status = calc_team_score(away_injuries, away_key_out)
    
    summary = f"主队({home_status}){home_score}分/客队({away_status}){away_score}分"
    return home_score, away_score, summary


def calculate_fitness_score(home_rest_days: int, away_rest_days: int) -> Tuple[int, int, str]:
    """
    体能评分（权重15%）
    
    规则:
    - 休息7天+得12-15分
    - 5-6天9-12分
    - 4天内5-8分
    
    Returns: (home_score, away_score, summary)
    """
    def calc_team_fitness(rest_days: int) -> Tuple[int, str]:
        if rest_days >= 7:
            return 14, "充分休整"
        elif rest_days >= 5:
            return 10, "正常休息"
        else:
            return 6, "休息不足"
    
    home_score, home_status = calc_team_fitness(home_rest_days)
    away_score, away_status = calc_team_fitness(away_rest_days)
    
    summary = f"主队休息{home_rest_days}天({home_status}){home_score}分/客队休息{away_rest_days}天({away_status}){away_score}分"
    return home_score, away_score, summary


def calculate_points_score(home_rank: int, away_rank: int, season_matches: int = 30) -> Tuple[int, int, str]:
    """
    积分形势评分（权重15%）
    
    规则:
    - 排名更高12-15分
    - 接近10-12分
    
    Returns: (home_score, away_score, summary)
    """
    if home_rank <= 0 or away_rank <= 0:
        return 10, 10, "排名数据缺失，双方各10分"
    
    if home_rank < away_rank:
        home_score = 14 if home_rank <= 4 else (13 if home_rank <= 8 else 12)
        away_score = 20 - home_score
    elif home_rank > away_rank:
        away_score = 14 if away_rank <= 4 else (13 if away_rank <= 8 else 12)
        home_score = 20 - away_score
    else:
        home_score = away_score = 10
    
    summary = f"主队第{home_rank}/{season_matches}名{home_score}分/客队第{away_rank}/{season_matches}名{away_score}分"
    return home_score, away_score, summary


def calculate_form_score(home_wins: int, home_draws: int, home_losses: int,
                         away_wins: int, away_draws: int, away_losses: int) -> Tuple[int, int, str]:
    """
    近期状态评分（权重15%）
    
    规则:
    - 胜率60%+得12-15分
    - 50-60%得10-12分
    - 50%以下8-10分
    
    Returns: (home_score, away_score, summary)
    """
    def calc_win_rate(wins: int, draws: int, losses: int) -> Tuple[float, int, str]:
        total = wins + draws + losses
        if total == 0:
            return 0.5, 10, "无数据"
        
        win_rate = (wins + draws * 0.5) / total
        
        if win_rate >= 0.6:
            score = 13 + int((win_rate - 0.6) * 5)  # 13-15分
            status = f"良好({win_rate*100:.0f}%)"
        elif win_rate >= 0.5:
            score = 10 + int((win_rate - 0.5) * 20)  # 10-12分
            status = f"一般({win_rate*100:.0f}%)"
        else:
            score = 8 + int(win_rate * 10)  # 8-10分
            status = f"低迷({win_rate*100:.0f}%)"
        
        return win_rate, score, status
    
    _, home_score, home_status = calc_win_rate(home_wins, home_draws, home_losses)
    _, away_score, away_status = calc_win_rate(away_wins, away_draws, away_losses)
    
    summary = f"主队{home_status}{home_score}分/客队{away_status}{away_score}分"
    return home_score, away_score, summary


def calculate_odds_score(odds_home: float, odds_draw: float, odds_away: float) -> Tuple[int, int, str]:
    """
    赔率评分（权重15%）
    
    规则:
    - 热门1.4-1.6得12-15分
    - 中性1.6-2.0得9-12分
    - 劣势2.0+得5-8分
    
    Returns: (home_score, away_score, summary)
    """
    def calc_team_odds_score(odds: float) -> Tuple[int, str]:
        if odds <= 1.6:  # 热门
            score = 14 - int((1.6 - odds) * 5)  # 12-15分
            status = "热门"
        elif odds <= 2.0:  # 中性
            score = 10 - int((2.0 - odds) * 2.5)  # 9-12分
            status = "中性"
        else:  # 劣势
            score = max(5, 8 - int((odds - 2.0) * 0.5))  # 5-8分
            status = "劣势"
        
        return score, status
    
    home_score, home_status = calc_team_odds_score(odds_home)
    away_score, away_status = calc_team_odds_score(odds_away)
    
    summary = f"主队赔率{odds_home}({home_status}){home_score}分/客队赔率{odds_away}({away_status}){away_score}分"
    return home_score, away_score, summary


def calculate_6dim_score(
    home_team: str, away_team: str, league: str = "",
    home_rank: int = 0, away_rank: int = 0,
    home_points: int = 0, away_points: int = 0,
    home_value: Optional[float] = None, away_value: Optional[float] = None,
    home_injuries: Optional[List[Dict]] = None, away_injuries: Optional[List[Dict]] = None,
    home_key_out: Optional[List] = None, away_key_out: Optional[List] = None,
    home_rest_days: int = 5, away_rest_days: int = 5,
    home_recent: Optional[Dict] = None, away_recent: Optional[Dict] = None,
    odds_home: float = 2.0, odds_draw: float = 3.0, odds_away: float = 3.0,
    season_matches: int = 30
) -> Dict:
    """
    竞猜足球分析助手6维量化评分
    
    整合身价、阵容、体能、积分、状态、赔率6个维度
    
    Args:
        home_team/away_team: 球队名称
        home_rank/away_rank: 排名
        home_points/away_points: 积分
        home_value/away_value: 身价(万欧元)
        home_injuries/away_injuries: 伤停列表
        home_key_out/away_key_out: 核心缺阵列表
        home_rest_days/away_rest_days: 休息天数
        home_recent/away_recent: 近期战绩字典
        odds_home/odds_draw/odds_away: 赔率
        season_matches: 联赛总场次
    
    Returns:
        {
            "dim6_enabled": True/False,
            "home_scores": {"身价": x, "阵容": x, "体能": x, "积分": x, "状态": x, "赔率": x, "总分": x},
            "away_scores": {...},
            "score_diff": x,  # 主队-客队
            "advantage_level": "极接近/边界/优势明显/压倒性",
            "ev_adjust": 0.0,  # EV修正因子
            "warnings": [],
            "summary": "描述"
        }
    """
    if not DIM6_ENABLED:
        return {
            "dim6_enabled": False,
            "summary": "6维评分已禁用"
        }
    
    # 简单缓存
    cache_key = f"dim6|{home_team}|{away_team}"
    if not hasattr(calculate_6dim_score, "_cache"):
        calculate_6dim_score._cache = {}
    if cache_key in calculate_6dim_score._cache:
        return calculate_6dim_score._cache[cache_key]
    
    # 默认值处理
    if home_injuries is None:
        home_injuries = []
    if away_injuries is None:
        away_injuries = []
    if home_key_out is None:
        home_key_out = []
    if away_key_out is None:
        away_key_out = []
    if home_recent is None:
        home_recent = {"wins": 0, "draws": 0, "losses": 0}
    if away_recent is None:
        away_recent = {"wins": 0, "draws": 0, "losses": 0}
    
    # 计算各维度得分
    value_home, value_away, value_summary = calculate_value_score(home_value, away_value)
    squad_home, squad_away, squad_summary = calculate_squad_score(
        home_injuries, away_injuries, home_key_out, away_key_out
    )
    fitness_home, fitness_away, fitness_summary = calculate_fitness_score(
        home_rest_days, away_rest_days
    )
    points_home, points_away, points_summary = calculate_points_score(
        home_rank, away_rank, season_matches
    )
    form_home, form_away, form_summary = calculate_form_score(
        home_recent.get("wins", 0), home_recent.get("draws", 0), home_recent.get("losses", 0),
        away_recent.get("wins", 0), away_recent.get("draws", 0), away_recent.get("losses", 0)
    )
    odds_home_score, odds_away_score, odds_summary = calculate_odds_score(
        odds_home, odds_draw, odds_away
    )
    
    # 计算总分（权重：身价20%+阵容20%+体能15%+积分15%+状态15%+赔率15%）
    home_total = (
        value_home * 0.20 +
        squad_home * 0.20 +
        fitness_home * 0.15 +
        points_home * 0.15 +
        form_home * 0.15 +
        odds_home_score * 0.15
    )
    away_total = (
        value_away * 0.20 +
        squad_away * 0.20 +
        fitness_away * 0.15 +
        points_away * 0.15 +
        form_away * 0.15 +
        odds_away_score * 0.15
    )
    
    score_diff = home_total - away_total
    
    # 判断优势等级
    abs_diff = abs(score_diff)
    if abs_diff < 2:
        advantage_level = "极接近优先平局"
    elif abs_diff < 5:
        advantage_level = "边界"
    elif abs_diff < 10:
        advantage_level = "优势明显"
    else:
        advantage_level = "压倒性"
    
    # EV修正因子（基于6维评分差距）
    if abs_diff >= 10:
        ev_adjust = 0.02
    elif abs_diff >= 8:
        ev_adjust = 0.015
    elif abs_diff >= 5:
        ev_adjust = 0.01
    else:
        ev_adjust = 0.0
    
    # 警告检测
    warnings = []
    if home_injuries and len(home_key_out) >= 2:
        warnings.append("主队核心阵容严重受损")
    if away_injuries and len(away_key_out) >= 2:
        warnings.append("客队核心阵容严重受损")
    if home_rest_days < 4 and away_rest_days >= 7:
        warnings.append("主队休息不足 vs 客队充分休整")
    if away_rest_days < 4 and home_rest_days >= 7:
        warnings.append("客队休息不足 vs 主队充分休整")
    
    result = {
        "dim6_enabled": True,
        "home_scores": {
            "身价": value_home,
            "阵容": squad_home,
            "体能": fitness_home,
            "积分": points_home,
            "状态": form_home,
            "赔率": odds_home_score,
            "总分": round(home_total, 1)
        },
        "away_scores": {
            "身价": value_away,
            "阵容": squad_away,
            "体能": fitness_away,
            "积分": points_away,
            "状态": form_away,
            "赔率": odds_away_score,
            "总分": round(away_total, 1)
        },
        "score_diff": round(score_diff, 1),
        "advantage_level": advantage_level,
        "ev_adjust": ev_adjust,
        "warnings": warnings,
        "summary": (
            f"6维评分: 主队{home_total:.1f}分/客队{away_total:.1f}分，"
            f"分差{score_diff:.1f}分→{advantage_level}，"
            f"EV修正{ev_adjust:+.3f}"
        ),
        "detail_summaries": {
            "身价": value_summary,
            "阵容": squad_summary,
            "体能": fitness_summary,
            "积分": points_summary,
            "状态": form_summary,
            "赔率": odds_summary
        }
    }
    
    calculate_6dim_score._cache[cache_key] = result
    return result


# ========== 主函数（测试用） ==========

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="基本面分析测试")
    parser.add_argument("--home", default="曼城", help="主队名称")
    parser.add_argument("--away", default="阿森纳", help="客队名称")
    parser.add_argument("--league", default="英超", help="联赛名称")
    parser.add_argument("--home-rank", type=int, default=2, help="主队排名")
    parser.add_argument("--away-rank", type=int, default=1, help="客队排名")
    parser.add_argument("--no-v34", action="store_true", help="禁用V3.4变量")
    args = parser.parse_args()
    
    if args.no_v34:
        set_v34_enabled(False)
    
    print(f"基本面分析: {args.home} vs {args.away}")
    print("-" * 50)
    
    result = analyze_fundamental(
        args.home, args.away, args.league,
        args.home_rank, args.away_rank
    )
    
    print(f"伤停信息: {result['injury_info'].get('summary', '无')}")
    print(f"战意评估: {result['motivation'].get('summary', '无')}")
    print(f"主队状态: {result['home_form'].get('summary', '无')}")
    print(f"客队状态: {result['away_form'].get('summary', '无')}")
    print(f"冷门风险: {result['cold_risk'].get('summary', '无')}")
    print(f"风险预警: {result['risk_warning']}")
    print(f"EV修正: {result['ev_adjust']:+.3f}")
    print(f"修正原因: {result['ev_adjust_reason']}")
    
    if V34_ENABLED:
        print("\n" + "=" * 50)
        print("V3.4变量分析:")
        v34 = analyze_v34_variables(args.home, args.away, args.league,
                                     args.home_rank, args.away_rank)
        print(f"  VAR争议: {v34['var_controversy'].get('summary', '无')}")
        print(f"  中游无欲: {v34['midtable_away'].get('summary', '无')}")
        print(f"  次级联赛: {v34['secondary_league'].get('summary', '无')}")
        print(f"  送温暖: {v34['warm_welcome'].get('summary', '无')}")
        print(f"  V3.4总EV修正: {v34['total_ev_adjust']:+.3f}")
        if v34['warnings']:
            print(f"  警告: {' | '.join(v34['warnings'])}")
