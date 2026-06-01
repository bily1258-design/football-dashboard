#!/usr/bin/env python3
"""
oddsmagnet 原始数据转换为 real_odds.json
增强版：添加 matchDate/matchTime 字段（从 kickoff 解析）

Usage:
    python oddsmagnet_to_realodds.py                    # 默认今天
    python oddsmagnet_to_realodds.py --date 2026-05-31  # 指定日期
"""
import json, os, math
from datetime import datetime, timedelta
# dateutil not needed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)


def parse_kickoff_to_datetime(kickoff_str: str, default_date: str = None) -> tuple:
    """
    解析 kickoff 字符串（如 "05-31 18:00"）为 matchDate 和 matchTime
    
    Args:
        kickoff_str: kickoff 字符串，如 "05-31 18:00"
        default_date: 默认日期字符串，如 "2026-05-31"
    
    Returns:
        (matchDate: str, matchTime: str) 或 (None, None)
        matchDate 格式: "YYYY-MM-DD"
        matchTime 格式: "HH:MM"
    """
    if not kickoff_str:
        return None, None
    
    try:
        # kickoff 格式: "MM-DD HH:MM"
        parts = kickoff_str.strip().split()
        if len(parts) != 2:
            return None, None
        
        date_part = parts[0]  # "MM-DD"
        time_part = parts[1]  # "HH:MM"
        
        # 补全年份：使用 default_date 的年份
        if default_date:
            year = default_date[:4]
        else:
            year = datetime.now().year
        
        # 构建完整日期时间字符串
        # date_part 格式是 "MM-DD"，需要添加年份
        full_str = f"{year}-{date_part} {time_part}"
        
        # 使用 datetime 解析
        kickoff_dt = datetime.strptime(full_str, "%Y-%m-%d %H:%M")
        
        # 如果 kickoff 时间早于数据获取时间（通常在当天），可能是下一天的凌晨比赛
        # 但这需要根据上下文判断，这里简单处理：直接使用解析结果
        
        return kickoff_dt.strftime("%Y-%m-%d"), kickoff_dt.strftime("%H:%M")
        
    except Exception as e:
        # Fallback: 尝试直接解析
        try:
            # 尝试各种格式
            for fmt in ["%m-%d %H:%M", "%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
                kickoff_dt = datetime.strptime(kickoff_str.strip(), fmt)
                return kickoff_dt.strftime("%Y-%m-%d"), kickoff_dt.strftime("%H:%M")
        except:
            pass
        return None, None


def convert(date_str=None):
    """
    转换 oddsmagnet 原始数据为 real_odds.json
    
    Args:
        date_str: 数据日期，格式 "YYYY-MM-DD"，用于：
                  1. 定位原始数据文件 data/raw/oddsmagnet/{YYYYMMDD}.json
                  2. 补全 kickoff 的年份
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    # 构建原始数据路径
    dc = date_str.replace("-", "")
    om_path = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet", f"{dc}.json")
    
    if not os.path.exists(om_path):
        print(f"no data: {om_path}")
        return False
    
    with open(om_path, "r", encoding="utf-8") as fh:
        om = json.load(fh)
    
    matches = om.get("matches", {})
    
    # 兼容列表格式
    if isinstance(matches, list):
        matches = {f"{m.get('info',{}).get('home','')}_{m.get('info',{}).get('away','')}": m for m in matches}
    
    # 获取原始数据的日期
    source_date = om.get("date", date_str)
    
    real_odds = {}
    for k, match in matches.items():
        info = match.get("info", {})
        odds = match.get("odds", {})
        
        home = info.get("home", "")
        away = info.get("away", "")
        
        if not home or not away:
            continue
        
        avg = odds.get("avg", {})
        
        # 解析 kickoff 为 matchDate/matchTime
        kickoff = info.get("kickoff", "")
        match_date, match_time = parse_kickoff_to_datetime(kickoff, source_date)
        
        entry = {
            "home": avg.get("odds_w"),
            "draw": avg.get("odds_d"),
            "away": avg.get("odds_l"),
            "avg_margin": avg.get("margin"),
            "matchNum": info.get("number", ""),
            "league": info.get("league", ""),
            "kickoff": kickoff,
            "matchDate": match_date,
            "matchTime": match_time,
            "odds_source": "oddsmagnet",  # 标记数据来源
        }
        
        # 添加各庄家的开盘赔率
        for src in ["pinnacle", "hkjc"]:
            s = odds.get(src, {})
            if s:
                entry[f"{src}_open_w"] = s.get("odds_w")
                entry[f"{src}_open_d"] = s.get("odds_d")
                entry[f"{src}_open_l"] = s.get("odds_l")
        
        # 添加让球盘赔率 (hhad)
        # 注意：oddsmagnet 的 hhad 数据可能在不同结构中
        hhad = odds.get("hhad", {})
        if hhad:
            # hhad 结构: {odds_w, odds_d, odds_l, handicap}
            entry["hhad_home"] = hhad.get("odds_w")
            entry["hhad_draw"] = hhad.get("odds_d")
            entry["hhad_away"] = hhad.get("odds_l")
            entry["hhad_handicap"] = hhad.get("handicap")
        
        # 移除 None 值
        entry = {k2: v for k2, v in entry.items() if v is not None}
        real_odds[f"{home} vs {away}"] = entry
    
    # 输出到 cache/real_odds.json
    cache_dir = os.path.join(REPO_DIR, "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, "real_odds.json")
    
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(real_odds, fh, ensure_ascii=False, indent=2)
    
    print(f"OK {len(real_odds)} matches -> {out}")
    
    # 打印比赛时间分布
    date_counts = {}
    for entry in real_odds.values():
        md = entry.get("matchDate", "unknown")
        date_counts[md] = date_counts.get(md, 0) + 1
    
    if date_counts:
        print("比赛时间分布:")
        for d, cnt in sorted(date_counts.items()):
            print(f"  {d}: {cnt} 场")
    
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="转换 oddsmagnet 数据为 real_odds.json")
    p.add_argument("--date", help="数据日期，格式 YYYY-MM-DD，默认今天")
    a = p.parse_args()
    convert(a.date)
