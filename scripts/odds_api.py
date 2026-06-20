#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
odds_api.py — 足彩网赔率抓取 + BSD分析引擎

数据源: 中国足彩网 zgzcw.com
支持: 百家平均(GET) / Pinnacle / HKJC / SB (POST)
BSD: 隐含概率 → 去抽水 → 真实胜率 → 公平赔率 → EV

依赖: requests (pip install requests)
用法:
  python odds_api.py                      # 抓今天+明天
  python odds_api.py --date 2026-05-30    # 指定日期
  python odds_api.py --load 20260530      # 读缓存
  python odds_api.py --source avg         # 只抓百家平均
  python odds_api.py --compare            # 抓完自动BSD对比
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ====== 路径 ======
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")
CACHE_DIR = os.path.join(REPO_DIR, "data", "cache")

# ====== 配置 ======
BASE_URL = "https://plzx.zgzcw.com/bjzs"
SLEEP_SEC = 5.0  # 请求间隔，避免触发WAF
MAX_RETRY = 2    # WAF拦截时最大重试次数

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://plzx.zgzcw.com/bjzs",
}

COMPANY_MAP = {
    "0": "百家平均",
    "106": "Pinnacle",
    # "136": "HKJC",   # 已取消抓取
    # "3": "SB",        # 已取消抓取
    "56": "Betfair",
}


# ================================================================
#  BSD 模型 (Bookmaker Statistical Deviation)
# ================================================================

def implied_prob(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """隐含概率（含抽水）"""
    if w <= 0 or d <= 0 or l <= 0:
        return 0.0, 0.0, 0.0
    rw, rd, rl = 1.0/w, 1.0/d, 1.0/l
    return round(rw, 4), round(rd, 4), round(rl, 4)


def margin(w: float, d: float, l: float) -> float:
    """庄家抽水率 (overround)"""
    pw, pd, pl = implied_prob(w, d, l)
    return round(pw + pd + pl - 1.0, 4)


def fair_prob(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """去抽水后的真实胜率（BSD核心）"""
    pw, pd, pl = implied_prob(w, d, l)
    total = pw + pd + pl
    if total <= 0:
        return 0.0, 0.0, 0.0
    return round(pw/total, 4), round(pd/total, 4), round(pl/total, 4)


def fair_odds(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """公平赔率（无抽水理论赔率）"""
    fpw, fpd, fpl = fair_prob(w, d, l)
    return (
        round(1.0/fpw, 3) if fpw > 0 else 0.0,
        round(1.0/fpd, 3) if fpd > 0 else 0.0,
        round(1.0/fpl, 3) if fpl > 0 else 0.0,
    )


def calc_ev(fair_p: float, book_odds: float) -> float:
    """期望价值 EV = fair_p * odds - 1"""
    if fair_p <= 0 or book_odds <= 0:
        return 0.0
    return round(fair_p * book_odds - 1.0, 4)


# ================================================================
#  工具函数
# ================================================================

def _safe_float(s, default=0.0) -> float:
    if not s:
        return default
    try:
        v = float(str(s).strip().replace("↑", "").replace("↓", "").replace("→", ""))
        return v if 1.01 < v < 50.0 else default
    except (ValueError, TypeError):
        return default


def _calc_implied(w, d, l):
    if w <= 0 or d <= 0 or l <= 0:
        return 0, 0, 0
    total = 1/w + 1/d + 1/l
    return round(1/w/total, 4), round(1/d/total, 4), round(1/l/total, 4)


def _is_waf(html: str) -> bool:
    """检测WAF拦截页面"""
    return "HuaweiCloudWAF" in html or "CloudWAF" in html or "Access Verification" in html or len(html) < 5000


# ================================================================
#  足彩网 — 百家平均 (GET，最稳定)
# ================================================================

def fetch_avg(date_str: str, page_type: str = None) -> List[Dict]:
    """抓取百家平均欧赔

    Args:
        date_str: YYYYMMDD格式
        page_type: None=竞彩, 'bd'=北单

    Returns: [{match_id, number, league, kickoff, home, away,
              avg_open:{w,d,l}, avg_close:{w,d,l},
              movement, implied_prob, margin}]
    """
    params = {"date": date_str}
    if page_type:
        params["type"] = page_type

    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
            if r.status_code == 200 and not _is_waf(r.text):
                break
            if attempt < MAX_RETRY:
                wait = SLEEP_SEC * (attempt + 2)
                print(f"  ⚠️ 百家平均 WAF拦截，{wait:.0f}s后重试({attempt+1}/{MAX_RETRY})...")
                time.sleep(wait)
            else:
                print(f"  ❌ 百家平均 重试耗尽")
                return []
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(SLEEP_SEC * 2)
            else:
                print(f"  ❌ 百家平均: {e}")
                return []

    html = r.text
    matches = []

    # 方法1: tab-body表格（当前页面结构）
    body_table = re.search(r'<table[^>]*class="tab-body"[^>]*>(.*?)</table>', html, re.S)
    if body_table:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', body_table.group(1), re.S)
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
            if len(tds) < 11:
                continue
            clean = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]

            mid_m = re.search(r'id="match_ul_(\d+)"', row)
            match_id = mid_m.group(1) if mid_m else ""

            teams_text = re.sub(r'<[^>]+>', '', tds[4]).strip()
            vs_parts = re.split(r'\s*VS\s*', teams_text, flags=re.I)
            home = vs_parts[0].strip() if len(vs_parts) >= 1 else ""
            away = vs_parts[1].strip() if len(vs_parts) >= 2 else ""

            open_w = _safe_float(clean[5])
            open_d = _safe_float(clean[6])
            open_l = _safe_float(clean[7])
            close_w = _safe_float(clean[8])
            close_d = _safe_float(clean[9])
            close_l = _safe_float(clean[10])

            if close_w <= 0:
                continue

            mv_w = close_w - open_w if open_w > 0 else 0
            mv_d = close_d - open_d if open_d > 0 else 0
            mv_l = close_l - open_l if open_l > 0 else 0
            imp_w, imp_d, imp_l = _calc_implied(close_w, close_d, close_l)

            matches.append({
                "match_id": match_id,
                "number": clean[1],
                "league": clean[2],
                "kickoff": clean[3],
                "home": home,
                "away": away,
                "source": "avg",
                "odds_w": close_w, "odds_d": close_d, "odds_l": close_l,
                "avg_open": {"w": open_w, "d": open_d, "l": open_l},
                "avg_close": {"w": close_w, "d": close_d, "l": close_l},
                "movement": {
                    "w": round(mv_w, 2), "d": round(mv_d, 2), "l": round(mv_l, 2),
                    "direction": "主升" if mv_w > 0.1 else ("客升" if mv_l > 0.1 else "平稳"),
                },
                "implied_prob": {"w": imp_w, "d": imp_d, "l": imp_l},
                "margin": margin(close_w, close_d, close_l),
            })
        return matches

    # 方法2: 兼容旧页面（t1/t2 class + fenxi链接）
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        mid_list = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not mid_list:
            continue
        match_id = mid_list[0]

        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        odds_vals = []
        for td in td_contents:
            c = re.sub(r'<[^>]+>', '', td).strip()
            v = _safe_float(c)
            if v > 0:
                odds_vals.append(v)

        if len(odds_vals) < 6:
            continue

        open_w, open_d, open_l = odds_vals[0], odds_vals[1], odds_vals[2]
        close_w, close_d, close_l = odds_vals[3], odds_vals[4], odds_vals[5]

        # HHAD让球盘检测：平局赔率<2.0 是亚盘不是欧赔
        if close_d > 0 and close_d < 2.0:
            continue

        imp_w, imp_d, imp_l = _calc_implied(close_w, close_d, close_l)
        mv_w = close_w - open_w if open_w > 0 else 0
        mv_l = close_l - open_l if open_l > 0 else 0

        matches.append({
            "match_id": match_id,
            "number": "",
            "league": "",
            "kickoff": "",
            "home": teams[0],
            "away": teams[1],
            "source": "avg",
            "odds_w": close_w, "odds_d": close_d, "odds_l": close_l,
            "avg_open": {"w": open_w, "d": open_d, "l": open_l},
            "avg_close": {"w": close_w, "d": close_d, "l": close_l},
            "movement": {
                "w": round(mv_w, 2), "d": 0, "l": round(mv_l, 2),
                "direction": "主升" if mv_w > 0.1 else ("客升" if mv_l > 0.1 else "平稳"),
            },
            "implied_prob": {"w": imp_w, "d": imp_d, "l": imp_l},
            "margin": margin(close_w, close_d, close_l),
        })

    return matches


# ================================================================
#  足彩网 — 指定公司 (POST)
# ================================================================

def fetch_company(company_id: str, date_str: str,
                  page_type: str = None) -> List[Dict]:
    """POST抓取指定公司赔率

    company_id: '106'=Pinnacle, '136'=HKJC, '3'=SB
    注意: POST返回的可能是亚盘(HHAD)，会自动过滤
    """
    company_name = COMPANY_MAP.get(company_id, f"aid={company_id}")
    data = {
        "company": company_id,
        "companyType": "b",
        "date": date_str,
        "type": page_type if page_type else "jc",
        "issue": "",
        "fg": "1",
    }

    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.post(BASE_URL, data=data, headers={
                **HEADERS, "Content-Type": "application/x-www-form-urlencoded"
            }, timeout=20)
            if r.status_code == 200 and not _is_waf(r.text):
                break
            if attempt < MAX_RETRY:
                wait = SLEEP_SEC * (attempt + 2)
                print(f"  ⚠️ {company_name} WAF拦截，{wait:.0f}s后重试({attempt+1}/{MAX_RETRY})...")
                time.sleep(wait)
            else:
                print(f"  ❌ {company_name} 重试耗尽")
                return []
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(SLEEP_SEC * 2)
            else:
                print(f"  ❌ {company_name}: {e}")
                return []

    html = r.text
    matches = []

    # 提取JsonOdds变量（赔率变化历史，可补HTML为空的情况）
    json_odds = []
    m_odds = re.search(r"var JsonOdds\s*=\s*'(.*?)';", html, re.S)
    if m_odds:
        json_odds = _parse_json_odds(m_odds.group(1))

    # tab-body表格
    body_table = re.search(r'<table[^>]*class="tab-body"[^>]*>(.*?)</table>', html, re.S)
    if body_table:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', body_table.group(1), re.S)
        for i, row in enumerate(rows):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
            if len(tds) < 11:
                continue
            clean = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]

            mid_m = re.search(r'id="match_ul_(\d+)"', row)
            match_id = mid_m.group(1) if mid_m else ""
            teams_text = re.sub(r'<[^>]+>', '', tds[4]).strip()
            vs_parts = re.split(r'\s*VS\s*', teams_text, flags=re.I)
            home = vs_parts[0].strip() if len(vs_parts) >= 1 else ""
            away = vs_parts[1].strip() if len(vs_parts) >= 2 else ""

            close_w = _safe_float(clean[8])
            close_d = _safe_float(clean[9])
            close_l = _safe_float(clean[10])

            # HTML赔率为空时从JsonOdds补
            if close_w <= 0 and i < len(json_odds) and json_odds[i]:
                last = json_odds[i][-1]
                host = _safe_float(last.get("HOST", 0))
                hc = _safe_float(last.get("HANDICAP", 0))
                guest = _safe_float(last.get("GUEST", 0))
                # HOST>1 才是欧赔
                if host > 1.0 and hc > 1.0 and guest > 1.0:
                    close_w, close_d, close_l = host, hc, guest

            if close_w <= 0 or close_l <= 0:
                continue

            # HHAD让球盘过滤
            if close_d > 0 and close_d < 2.0:
                continue

            imp_w, imp_d, imp_l = _calc_implied(close_w, close_d, close_l)
            matches.append({
                "match_id": match_id,
                "number": clean[1],
                "league": clean[2],
                "kickoff": clean[3],
                "home": home, "away": away,
                "source": company_name.lower().replace(" ", ""),
                "odds_w": close_w, "odds_d": close_d, "odds_l": close_l,
                "implied_prob": {"w": imp_w, "d": imp_d, "l": imp_l},
                "margin": margin(close_w, close_d, close_l),
            })
        return matches

    # 兼容旧页面
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
    for i, row in enumerate(rows):
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        mid_list = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not mid_list:
            continue

        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        odds_vals = []
        for td in td_contents:
            c = re.sub(r'<[^>]+>', '', td).strip()
            v = _safe_float(c)
            if v > 0:
                odds_vals.append(v)

        close_w = close_d = close_l = 0
        if len(odds_vals) >= 6:
            close_w, close_d, close_l = odds_vals[3], odds_vals[4], odds_vals[5]

        # JsonOdds回退
        if close_w <= 0 and i < len(json_odds) and json_odds[i]:
            last = json_odds[i][-1]
            host = _safe_float(last.get("HOST", 0))
            hc = _safe_float(last.get("HANDICAP", 0))
            guest = _safe_float(last.get("GUEST", 0))
            if host > 1.0 and hc > 1.0 and guest > 1.0:
                close_w, close_d, close_l = host, hc, guest

        if close_w <= 0 or close_l <= 0:
            continue
        if close_d > 0 and close_d < 2.0:
            continue

        imp_w, imp_d, imp_l = _calc_implied(close_w, close_d, close_l)
        matches.append({
            "match_id": mid_list[0],
            "number": "", "league": "", "kickoff": "",
            "home": teams[0], "away": teams[1],
            "source": company_name.lower().replace(" ", ""),
            "odds_w": close_w, "odds_d": close_d, "odds_l": close_l,
            "implied_prob": {"w": imp_w, "d": imp_d, "l": imp_l},
            "margin": margin(close_w, close_d, close_l),
        })

    return matches


def _parse_json_odds(raw: str) -> List[List[Dict]]:
    """解析足彩网JsonOdds变量（非标准JSON）"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 暴力正则提取
    result = []
    match_strs = re.split(r'\],\s*\[', raw.strip('[]'))
    for ms in match_strs:
        records = []
        for rec_m in re.finditer(r'\{([^}]+)\}', ms):
            record = {}
            for kv_m in re.finditer(r'(\w+)\s*[=:]\s*["\']?([-\d.]+)["\']?', rec_m.group(1)):
                record[kv_m.group(1)] = kv_m.group(2)
            if record:
                records.append(record)
        result.append(records)
    return result


# ================================================================
#  多源聚合
# ================================================================
#  足彩网 — 亚盘让球盘 (POST companyType=y)
# ================================================================

def _parse_ah_value(text):
    """解析亚盘水位/盘口值，去掉箭头标记"""
    if not text:
        return 0.0, 'stable'
    text = str(text).strip()
    movement = 'stable'
    if '↑' in text:
        movement = 'up'
    elif '↓' in text:
        movement = 'down'
    elif '→' in text:
        movement = 'stable'
    text = text.replace('↑', '').replace('↓', '').replace('→', '').replace('＊', '*').strip()
    try:
        val = float(text)
        return val, movement
    except:
        return 0.0, 'stable'


def fetch_asian_handicap(date_str: str, page_type: str = None, company: str = '0'):
    """POST抓取亚盘让球盘数据 (companyType=y)

    亚盘格式: home_water / handicap / away_water (主队水位/盘口/客队水位)
    company: '0'=百家平均, '136'=HKJC
    date_str: YYYYMMDD格式
    Returns: {match_key: {open: {home_w, handicap, away_w}, close: {home_w, handicap, away_w}}}
    """
    company_names = {'0': '百家平均', '136': 'HKJC'}
    company_name = company_names.get(company, f'company={company}')

    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'y',
        'date': date_str,
        'fg': '1',
    }

    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.post(BASE_URL, data=data, headers={
                **HEADERS, "Content-Type": "application/x-www-form-urlencoded"
            }, timeout=20)

            if r.status_code == 418 or _is_waf(r.text):
                if attempt < MAX_RETRY:
                    wait = SLEEP_SEC * (attempt + 2)
                    print(f"  ⚠️ 亚盘{company_name} WAF拦截，{wait:.0f}s后重试({attempt+1}/{MAX_RETRY})...")
                    time.sleep(wait)
                    continue
                else:
                    print(f"  ❌ 亚盘{company_name} 重试耗尽")
                    return {}

            if r.status_code != 200:
                print(f"  ❌ 亚盘{company_name} 请求失败: status={r.status_code}")
                return {}

            break
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(SLEEP_SEC * 2)
            else:
                print(f"  ❌ 亚盘{company_name}: {e}")
                return {}

    html = r.text
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)

    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue

        # 提取TD内容
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        td_values = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            td_values.append(td_clean)

        # 亚盘数据：按列位置提取（竞彩/北单列结构相同）
        # TD0=checkbox, TD1=编号, TD2=联赛, TD3=时间, TD4=对阵
        # TD5=open_home_w, TD6=open_handicap, TD7=open_away_w
        # TD8=close_home_w, TD9=close_handicap, TD10=close_away_w
        # TD11+=链接
        open_home_w, open_handicap, open_away_w = 0.0, 0.0, 0.0
        close_home_w, close_handicap, close_away_w = 0.0, 0.0, 0.0

        if len(td_values) >= 11:
            open_home_w, _ = _parse_ah_value(td_values[5])
            open_handicap, _ = _parse_ah_value(td_values[6])
            open_away_w, _ = _parse_ah_value(td_values[7])
            close_home_w, _ = _parse_ah_value(td_values[8])
            close_handicap, _ = _parse_ah_value(td_values[9])
            close_away_w, _ = _parse_ah_value(td_values[10])

        # 基本校验
        if open_home_w == 0 and open_away_w == 0 and close_home_w == 0 and close_away_w == 0:
            continue

        key = f"{teams[0]}_{teams[1]}"
        result[key] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'home_w': open_home_w, 'handicap': open_handicap, 'away_w': open_away_w},
            'close': {'home_w': close_home_w, 'handicap': close_handicap, 'away_w': close_away_w},
        }

    print(f"  亚盘{company_name}: {len(result)} 场")
    return result


# ================================================================
#  足彩网 — 大小球盘 (POST companyType=d)
# ================================================================

def fetch_over_under(date_str: str, page_type: str = None, company: str = '0'):
    """POST抓取大小球数据 (companyType=d)

    大小球格式: over / line / under (大球赔率/盘口线/小球赔率)
    company: '0'=百家平均, '15'=利记, '6'=明升
    date_str: YYYYMMDD格式
    Returns: {match_key: {home, away, open: {over, line, under}, close: {over, line, under}}}
    """
    company_names = {'0': '百家平均', '15': '利记', '6': '明升'}
    company_name = company_names.get(company, f'company={company}')

    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'd',
        'date': date_str,
        'fg': '1',
    }

    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.post(BASE_URL, data=data, headers={
                **HEADERS, "Content-Type": "application/x-www-form-urlencoded"
            }, timeout=20)

            if r.status_code == 418 or _is_waf(r.text):
                if attempt < MAX_RETRY:
                    wait = SLEEP_SEC * (attempt + 2)
                    print(f"  ⚠️ 大小球{company_name} WAF拦截，{wait:.0f}s后重试({attempt+1}/{MAX_RETRY})...")
                    time.sleep(wait)
                    continue
                else:
                    print(f"  ❌ 大小球{company_name} 重试耗尽")
                    return {}

            if r.status_code != 200:
                print(f"  ❌ 大小球{company_name} 请求失败: status={r.status_code}")
                return {}

            break
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(SLEEP_SEC * 2)
            else:
                print(f"  ❌ 大小球{company_name}: {e}")
                return {}

    html = r.text
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)

    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue

        # 提取TD内容
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        td_values = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            td_values.append(td_clean)

        # 大小球数据：按列位置提取（与亚盘相同的列结构）
        # TD0=checkbox, TD1=编号, TD2=联赛, TD3=时间, TD4=对阵
        # TD5=open_over, TD6=open_line, TD7=open_under
        # TD8=close_over, TD9=close_line, TD10=close_under
        open_over, open_line, open_under = 0.0, 0.0, 0.0
        close_over, close_line, close_under = 0.0, 0.0, 0.0

        if len(td_values) >= 11:
            open_over, _ = _parse_ah_value(td_values[5])
            open_line, _ = _parse_ah_value(td_values[6])
            open_under, _ = _parse_ah_value(td_values[7])
            close_over, _ = _parse_ah_value(td_values[8])
            close_line, _ = _parse_ah_value(td_values[9])
            close_under, _ = _parse_ah_value(td_values[10])

        # 基本校验
        if open_over == 0 and open_under == 0 and close_over == 0 and close_under == 0:
            continue

        key = f"{teams[0]}_{teams[1]}"
        result[key] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'over': open_over, 'line': open_line, 'under': open_under},
            'close': {'over': close_over, 'line': close_line, 'under': close_under},
        }

    print(f"  大小球{company_name}: {len(result)} 场")
    return result
# ================================================================

def _fuzzy_match(home: str, away: str, merged: dict) -> Optional[str]:
    """模糊匹配队名找到已存在的match_key"""
    for k in merged:
        info = merged[k]["info"]
        if (home in info["home"] or info["home"] in home) and \
           (away in info["away"] or info["away"] in away):
            return k
    return None


def fetch_all(date_str: str = None, companies: List[str] = None,
              do_compare: bool = False) -> Dict:
    """聚合所有赔率源

    Args:
        date_str: YYYY-MM-DD，默认今天
        companies: 要抓的公司ID列表，默认['106','136','3']
        do_compare: 是否自动做BSD多源对比

    Returns: {date, fetch_time, matches, summary}
    """
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    if companies is None:
        companies = ["106"]

    prev_day = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y%m%d')
    curr_day = date_str.replace('-', '')

    print(f"\n{'='*50}")
    print(f"赔率抓取: {date_str}")
    print(f"{'='*50}")

    # 1. 百家平均 — 当天 + 前一天（覆盖凌晨比赛）
    avg_matches = {}
    for d, label in [(prev_day, "前一天"), (curr_day, "当天"), (next_day, "次日")]:
        print(f"  百家平均 {label}...")
        matches = fetch_avg(d)
        time.sleep(SLEEP_SEC)
        for m in matches:
            key = f"{m['home']}_{m['away']}"
            avg_matches[key] = m
        print(f"    -> {len(matches)} 场")

    # 北单
    for d, label in [(prev_day, "前一天"), (curr_day, "当天"), (next_day, "次日")]:
        bd = fetch_avg(d, page_type="bd")
        time.sleep(SLEEP_SEC)
        for m in bd:
            key = f"{m['home']}_{m['away']}"
            if key not in avg_matches:
                avg_matches[key] = m
        if bd:
            print(f"    北单 {label}: {len(bd)} 场")

    print(f"  百家平均汇总: {len(avg_matches)} 场")

    # 1.5 亚盘让球盘 (POST companyType=y) — 百家平均(竞彩+北单)
    ah_data = {}  # key -> {open: {}, close: {}, source: str}
    for d, label in [(prev_day, "前一天"), (curr_day, "当天"), (next_day, "次日")]:
        # 竞彩亚盘
        ah_avg = fetch_asian_handicap(d, company='0')
        time.sleep(SLEEP_SEC)
        for key, v in ah_avg.items():
            ah_data[key] = {**v, 'source': 'avg'}
        # 北单亚盘
        ah_bd = fetch_asian_handicap(d, page_type='bd', company='0')
        time.sleep(SLEEP_SEC)
        for key, v in ah_bd.items():
            if key not in ah_data:  # 竞彩优先，北单补缺
                ah_data[key] = {**v, 'source': 'avg'}
        if ah_bd:
            print(f"    北单亚盘 {label}: {len(ah_bd)} 场")
    print(f"  亚盘汇总: {len(ah_data)} 场")

    # 1.6 利记(company=15) + 明升(company=6) 亚盘 — 2026-06-20 改造：加北单(bd) 抓取
    liji_ah = {}
    ms_ah = {}
    for d, label in [(prev_day, "前一天"), (curr_day, "当天"), (next_day, "次日")]:
        # 竞彩亚盘 - 利记
        lj = fetch_asian_handicap(d, company='15')
        time.sleep(SLEEP_SEC)
        for key, v in lj.items():
            liji_ah[key] = v
        # 北单亚盘 - 利记（竞彩优先，北单补缺）
        lj_bd = fetch_asian_handicap(d, page_type='bd', company='15')
        time.sleep(SLEEP_SEC)
        for key, v in lj_bd.items():
            if key not in liji_ah:
                liji_ah[key] = v
        if lj_bd:
            print(f"    利记北单 {label}: {len(lj_bd)} 场")
        # 竞彩亚盘 - 明升
        ms = fetch_asian_handicap(d, company='6')
        time.sleep(SLEEP_SEC)
        for key, v in ms.items():
            ms_ah[key] = v
        # 北单亚盘 - 明升（竞彩优先，北单补缺）
        ms_bd = fetch_asian_handicap(d, page_type='bd', company='6')
        time.sleep(SLEEP_SEC)
        for key, v in ms_bd.items():
            if key not in ms_ah:
                ms_ah[key] = v
        if ms_bd:
            print(f"    明升北单 {label}: {len(ms_bd)} 场")
    print(f"  利记亚盘: {len(liji_ah)} 场 | 明升亚盘: {len(ms_ah)} 场")

    # 1.7 大小球 — 暂时禁用：足彩网companyType='d'返回的实为亚盘数据，非大小球
    # 大小球数据改为从api-football获取Pinnacle OU（见fetch_pinnacle_odds.py Step 5.9）
    ou_data = {}
    ou_liji = {}
    ou_ms = {}
    print(f"  大小球: 跳过足彩网抓取（companyType=d返回亚盘数据），改用api-football")

    # 2. 各公司（2026-06-20 改造：启用北单page_type=bd，与百家平均/亚盘一致）
    company_data = {}
    for cid in companies:
        cname = COMPANY_MAP.get(cid, cid)
        all_odds = {}
        for d in [prev_day, curr_day, next_day]:  # 2026-06-20 改造：48小时范围
            # 竞彩页
            matches = fetch_company(cid, d, page_type="jc")
            time.sleep(SLEEP_SEC)
            for m in matches:
                key = f"{m['home']}_{m['away']}"
                all_odds[key] = m
            # 北单页
            matches_bd = fetch_company(cid, d, page_type="bd")
            time.sleep(SLEEP_SEC)
            for m in matches_bd:
                key = f"{m['home']}_{m['away']}"
                all_odds[key] = m
        company_data[cid] = all_odds
        print(f"  {cname}: {len(all_odds)} 场")

    # 3. 按 match_key 聚合
    merged = {}
    for key, m in avg_matches.items():
        merged[key] = {
            "info": {
                "match_id": m.get("match_id", ""),
                "number": m.get("number", ""),
                "league": m.get("league", ""),
                "kickoff": m.get("kickoff", ""),
                "home": m["home"],
                "away": m["away"],
            },
            "odds": {
                "avg": {
                    "source": "avg",
                    "odds_w": m["odds_w"], "odds_d": m["odds_d"], "odds_l": m["odds_l"],
                    "margin": m.get("margin", 0),
                    "implied_prob": m.get("implied_prob", {}),
                    "movement": m.get("movement"),
                },
            },
        }

    # 3.5 合并亚盘数据到 merged
    ah_merged = 0
    for key, ah in ah_data.items():
        if key in merged:
            merged[key]["ah"] = {
                "open": ah.get("open", {}),
                "close": ah.get("close", {}),
                "source": ah.get("source", ""),
            }
            ah_merged += 1
        else:
            # 模糊匹配
            fk = _fuzzy_match(ah.get("home", ""), ah.get("away", ""), merged)
            if fk:
                merged[fk]["ah"] = {
                    "open": ah.get("open", {}),
                    "close": ah.get("close", {}),
                    "source": ah.get("source", ""),
                }
                ah_merged += 1
    if ah_merged:
        print(f"  亚盘匹配: {ah_merged}/{len(ah_data)} 场")

    # 1.6 保存亚盘数据为独立文件，供 fetch_pinnacle_odds.py 读取写入DB
    if ah_data:
        ah_output = {}
        for key, ah in ah_data.items():
            ah_output[key] = {
                "home": ah.get("home", ""),
                "away": ah.get("away", ""),
                "open": ah.get("open", {}),
                "close": ah.get("close", {}),
                "source": ah.get("source", ""),
                # 利记/明升亚盘(含初盘+即时盘)
                "liji": liji_ah.get(key, {}),
                "ms": ms_ah.get(key, {}),
                # 大小球(含初盘+即时盘) — 百家平均/利记/明升
                "ou": ou_data.get(key, {}),
                "ou_liji": ou_liji.get(key, {}),
                "ou_ms": ou_ms.get(key, {}),
            }
        ah_path = os.path.join(RAW_DIR, f"ah_{curr_day}.json")
        with open(ah_path, 'w', encoding='utf-8') as f:
            json.dump(ah_output, f, ensure_ascii=False, indent=2)
        print(f"  亚盘数据保存: {ah_path}")

    # 1.7 合并利记/明升亚盘到 merged（含open+close）
    liji_merged = 0
    ms_merged = 0
    for key, ah in liji_ah.items():
        target = merged.get(key)
        if not target:
            fk = _fuzzy_match(ah.get("home", ""), ah.get("away", ""), merged)
            target = merged.get(fk) if fk else None
        if target:
            target["liji_ah"] = {
                "open": ah.get("open", {}),
                "close": ah.get("close", {}),
            }
            liji_merged += 1

    for key, ah in ms_ah.items():
        target = merged.get(key)
        if not target:
            fk = _fuzzy_match(ah.get("home", ""), ah.get("away", ""), merged)
            target = merged.get(fk) if fk else None
        if target:
            target["ms_ah"] = {
                "open": ah.get("open", {}),
                "close": ah.get("close", {}),
            }
            ms_merged += 1

    if liji_merged or ms_merged:
        print(f"  利记亚盘匹配: {liji_merged} 场 | 明升亚盘匹配: {ms_merged} 场")

    for cid, odds_dict in company_data.items():
        cname = COMPANY_MAP.get(cid, cid).lower().replace(" ", "")
        for key, m in odds_dict.items():
            # 先精确匹配
            if key in merged:
                if cname not in merged[key]["odds"]:
                    merged[key]["odds"][cname] = {
                        "source": cname,
                        "odds_w": m["odds_w"], "odds_d": m["odds_d"], "odds_l": m["odds_l"],
                        "margin": m.get("margin", 0),
                        "implied_prob": m.get("implied_prob", {}),
                    }
            else:
                # 模糊匹配
                fk = _fuzzy_match(m["home"], m["away"], merged)
                if fk and cname not in merged[fk]["odds"]:
                    merged[fk]["odds"][cname] = {
                        "source": cname,
                        "odds_w": m["odds_w"], "odds_d": m["odds_d"], "odds_l": m["odds_l"],
                        "margin": m.get("margin", 0),
                        "implied_prob": m.get("implied_prob", {}),
                    }
                elif not fk:
                    # 新比赛（百家平均没覆盖的）
                    merged[key] = {
                        "info": {"match_id": m.get("match_id", ""), "number": "",
                                 "league": "", "kickoff": "",
                                 "home": m["home"], "away": m["away"]},
                        "odds": {cname: {
                            "source": cname,
                            "odds_w": m["odds_w"], "odds_d": m["odds_d"], "odds_l": m["odds_l"],
                            "margin": m.get("margin", 0),
                            "implied_prob": m.get("implied_prob", {}),
                        }},
                    }

    # 4. 统计
    sources = set()
    pin_count = 0
    for v in merged.values():
        sources.update(v["odds"].keys())
        if "pinnacle" in v["odds"]:
            pin_count += 1

    total = len(merged)
    summary = {
        "total": total,
        "sources": sorted(list(sources)),
        "pinnacle_coverage": round(pin_count/total, 2) if total else 0,
    }

    # 5. BSD多源对比（可选）
    if do_compare:
        merged = bsd_compare_all(merged)

    # 6. 保存
    output = {
        "date": date_str,
        "fetch_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "matches": merged,
        "summary": summary,
    }

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, f"{curr_day}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    # 也存cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"pinnacle_odds_{curr_day}.json")
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ {total} 场 | {len(sources)} 源 | Pin覆盖{summary['pinnacle_coverage']:.0%}")
    print(f"   → {out_path}")
    return output


# ================================================================
#  BSD 多源对比
# ================================================================

def bsd_compare_all(matches: Dict) -> Dict:
    """对所有比赛做BSD多源对比

    1. 各机构去抽水 → fair_prob
    2. 按 1/margin 加权融合 → consensus_prob
    3. 分歧检测：同方向赔率最大差值
    4. EV扫描：融合概率 vs 各机构赔率
    """
    for key, m in matches.items():
        odds_dict = m.get("odds", {})
        if len(odds_dict) < 1:
            continue

        # 加权融合
        weights = {}
        w_total = 0.0
        for src, o in odds_dict.items():
            mg = o.get("margin", 0)
            # margin必须>0且<1才合理（0%<抽水<100%）
            if 0 < mg < 1:
                wt = 1.0 / mg  # 抽水越低权重越高
            else:
                wt = 1.0  # 异常margin用默认权重
            weights[src] = wt
            w_total += wt

        if w_total <= 0:
            continue

        cw = cd = cl = 0.0
        for src, o in odds_dict.items():
            ow = o.get("odds_w", 0)
            od_ = o.get("odds_d", 0)
            ol = o.get("odds_l", 0)
            if ow <= 0 or od_ <= 0 or ol <= 0:
                continue
            fpw, fpd, fpl = fair_prob(ow, od_, ol)
            wt = weights[src] / w_total
            cw += fpw * wt
            cd += fpd * wt
            cl += fpl * wt

        total = cw + cd + cl
        if total <= 0:
            continue

        m["bsd"] = {
            "consensus_prob": {
                "w": round(cw/total, 4),
                "d": round(cd/total, 4),
                "l": round(cl/total, 4),
            },
            "consensus_odds": {
                "w": round(total/cw, 3) if cw > 0 else 0,
                "d": round(total/cd, 3) if cd > 0 else 0,
                "l": round(total/cl, 3) if cl > 0 else 0,
            },
        }

        # 分歧检测
        all_w = [o["odds_w"] for o in odds_dict.values() if o.get("odds_w", 0) > 1]
        all_d = [o["odds_d"] for o in odds_dict.values() if o.get("odds_d", 0) > 1]
        all_l = [o["odds_l"] for o in odds_dict.values() if o.get("odds_l", 0) > 1]
        if len(all_w) >= 2:
            m["bsd"]["spread"] = {
                "w": round(max(all_w) - min(all_w), 3),
                "d": round(max(all_d) - min(all_d), 3) if len(all_d) >= 2 else 0,
                "l": round(max(all_l) - min(all_l), 3) if len(all_l) >= 2 else 0,
            }

        # EV扫描
        cp = m["bsd"]["consensus_prob"]
        ev_results = {}
        for src, o in odds_dict.items():
            ev_w = calc_ev(cp["w"], o.get("odds_w", 0))
            ev_d = calc_ev(cp["d"], o.get("odds_d", 0))
            ev_l = calc_ev(cp["l"], o.get("odds_l", 0))
            best = max(ev_w, ev_d, ev_l)
            if best > 0:
                ev_results[src] = {
                    "w": ev_w, "d": ev_d, "l": ev_l,
                    "best": round(best, 4),
                    "best_dir": "主胜" if best == ev_w else ("平局" if best == ev_d else "客胜"),
                }
        if ev_results:
            m["bsd"]["ev"] = ev_results

    return matches


# ================================================================
#  缓存读取
# ================================================================

def load_cache(date_str: str) -> Optional[Dict]:
    """加载缓存"""
    date_tag = date_str.replace('-', '')
    for path in [
        os.path.join(RAW_DIR, f"{date_tag}.json"),
        os.path.join(CACHE_DIR, f"pinnacle_odds_{date_tag}.json"),
    ]:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    return None


def print_summary(data: Dict):
    """打印摘要"""
    s = data.get("summary", {})
    print(f"\n📅 {data['date']} 拉取: {data['fetch_time']}")
    print(f"   总: {s.get('total',0)}场 | 源: {s.get('sources',[])} | Pin覆盖: {s.get('pinnacle_coverage',0):.0%}")

    matches = data.get("matches", {})
    for i, (key, m) in enumerate(matches.items()):
        if i >= 10:
            print(f"   ... 共{len(matches)}场")
            break
        info = m["info"]
        odds = m.get("odds", {})
        avg_o = odds.get("avg", {})
        src_list = list(odds.keys())
        bsd = m.get("bsd", {})
        cp = bsd.get("consensus_prob", {})

        line = f"   {info.get('number',''):6s} {info.get('league',''):6s} {info['home']} vs {info['away']}"
        if avg_o:
            line += f"  {avg_o.get('odds_w',0):.2f}/{avg_o.get('odds_d',0):.2f}/{avg_o.get('odds_l',0):.2f}"
        if cp:
            line += f"  BSD:{cp['w']:.0%}/{cp['d']:.0%}/{cp['l']:.0%}"
        line += f"  [{','.join(src_list)}]"
        print(line)


# ================================================================
#  main
# ================================================================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="足彩网赔率抓取 + BSD分析")
    p.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天")
    p.add_argument("--source", default="all",
                   choices=["all", "avg", "pinnacle", "hkjc", "sb"],
                   help="[已废弃] 赔率源筛选，默认仅抓取Pinnacle(106)+百家平均")
    p.add_argument("--companies", help="公司ID列表，逗号分隔，默认106")
    p.add_argument("--compare", action="store_true", help="抓完后自动BSD对比")
    p.add_argument("--load", help="读取缓存 YYYYMMDD")
    p.add_argument("--sleep", type=float, default=3.0, help="请求间隔秒数")
    args = p.parse_args()

    SLEEP_SEC = args.sleep

    if args.load:
        data = load_cache(args.load)
        if data:
            print_summary(data)
        else:
            print("未找到缓存")
    elif args.source == "avg":
        d = (args.date or datetime.now().strftime('%Y-%m-%d')).replace('-', '')
        matches = fetch_avg(d)
        print(f"百家平均: {len(matches)} 场")
        for m in matches[:10]:
            print(f"  {m.get('number','')} {m.get('league','')} {m['home']} vs {m['away']}  "
                  f"{m['odds_w']:.2f}/{m['odds_d']:.2f}/{m['odds_l']:.2f}  "
                  f"margin={m.get('margin',0):.1%}")
    else:
        companies = None
        if args.companies:
            companies = args.companies.split(",")
        elif args.source == "pinnacle":
            companies = ["106"]
        elif args.source == "hkjc":
            companies = ["136"]
        elif args.source == "sb":
            companies = ["3"]

        fetch_all(args.date, companies=companies, do_compare=args.compare)

