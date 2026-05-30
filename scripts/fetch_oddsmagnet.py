#!/usr/bin/env python3
"""fetch_oddsmagnet.py — 从足彩网(zgzcw.com)抓取百家赔率（OddsMagnet辅助数据源）

输出：data/raw/oddsmagnet/{YYYYMMDD}.json
结构：{date, fetch_time, matches:[], summary:{}}

数据源：
1. 列表页 GET/POST https://plzx.zgzcw.com/bjzs — 百家平均欧赔
2. POST + company=106 — 真Pinnacle
3. POST + company=3   — SB(明升)
4. POST + company=136 — HKJC(香港马会)

兼容：纯requests，无需浏览器
"""

import os, re, json, time, requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(REPO_DIR, "data", "raw", "oddsmagnet")

BASE_URL = "https://plzx.zgzcw.com/bjzs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://plzx.zgzcw.com/bjzs",
}


def _safe_float(s, default=0.0):
    """安全转浮点"""
    if not s:
        return default
    try:
        v = float(str(s).strip())
        return v if 1.0 < v < 50.0 else default
    except (ValueError, TypeError):
        return default


def _parse_odds_value(text):
    """从HTML片段提取赔率值"""
    text = re.sub(r'<[^>]+>', '', str(text)).strip()
    return _safe_float(text)


def _calc_implied_prob(w, d, l):
    """计算隐含概率"""
    if w <= 0 or d <= 0 or l <= 0:
        return 0, 0, 0
    total = 1/w + 1/d + 1/l
    margin = total - 1
    return round(1/w/total, 4), round(1/d/total, 4), round(1/l/total, 4)


# ─── 列表页：百家平均欧赔 ───
def fetch_list(date_str: str = None, page_type: str = None) -> List[Dict]:
    """抓取百家指数列表页

    page_type: None=默认, 'jc'=竞彩, 'bd'=北单
    """
    params = {}
    if date_str:
        params['date'] = date_str.replace('-', '')
    if page_type:
        params['type'] = page_type

    try:
        r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  ❌ 列表页 HTTP {r.status_code}")
            return []
        html = r.text
    except Exception as e:
        print(f"  ❌ 列表页请求失败: {e}")
        return []

    matches = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 9:
            continue
        try:
            league = re.sub(r'<[^>]+>', '', cells[0]).strip()
            ko = re.sub(r'<[^>]+>', '', cells[1]).strip()
            teams = re.sub(r'<[^>]+>', '', cells[2]).strip()
            vs = re.split(r'\s*vs\s*', teams, flags=re.I)
            if len(vs) < 2:
                continue

            # match_id (从链接提取)
            mid_m = re.search(r'/(\d+)/', cells[2])
            match_id = mid_m.group(1) if mid_m else ''

            # 平均欧赔（开盘/即时）
            open_w = _parse_odds_value(cells[3])
            open_d = _parse_odds_value(cells[4])
            open_l = _parse_odds_value(cells[5])
            close_w = _parse_odds_value(cells[6])
            close_d = _parse_odds_value(cells[7])
            close_l = _parse_odds_value(cells[8])

            if close_w <= 0:
                continue

            # 赔率变动
            mv_w = close_w - open_w if open_w > 0 else 0
            mv_d = close_d - open_d if open_d > 0 else 0
            mv_l = close_l - open_l if open_l > 0 else 0

            imp_w, imp_d, imp_l = _calc_implied_prob(close_w, close_d, close_l)

            matches.append({
                'match_id': match_id,
                'league': league,
                'kickoff': ko,
                'home': vs[0].strip(),
                'away': vs[1].strip(),
                'avg_open': {'w': open_w, 'd': open_d, 'l': open_l},
                'avg_close': {'w': close_w, 'd': close_d, 'l': close_l},
                'avg_movement': {
                    'w': round(mv_w, 2), 'd': round(mv_d, 2), 'l': round(mv_l, 2),
                    'direction': '主升' if mv_w > 0.1 else ('客升' if mv_l > 0.1 else '平稳'),
                },
                'implied_prob': {'w': imp_w, 'd': imp_d, 'l': imp_l},
                'margin': round(1/imp_w + 1/imp_d + 1/imp_l - 1, 4) if imp_w else 0,
            })
        except Exception:
            continue

    return matches


# ─── 指定公司赔率 ───
def fetch_company_odds(date_str: str = None, company: str = '106',
                       page_type: str = None) -> List[Dict]:
    """抓取指定公司的赔率

    company: '106'=Pinnacle, '3'=SB, '136'=HKJC, '56'=Betfair
    """
    data = {'company': company}
    if date_str:
        data['date'] = date_str.replace('-', '')
    if page_type:
        data['type'] = page_type

    try:
        r = requests.post(BASE_URL, data=data, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  ❌ 公司{company} HTTP {r.status_code}")
            return []
        html = r.text
    except Exception as e:
        print(f"  ❌ 公司{company}请求失败: {e}")
        return []

    results = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 9:
            continue
        try:
            teams = re.sub(r'<[^>]+>', '', cells[2]).strip()
            vs = re.split(r'\s*vs\s*', teams, flags=re.I)
            if len(vs) < 2:
                continue

            mid_m = re.search(r'/(\d+)/', cells[2])
            match_id = mid_m.group(1) if mid_m else ''

            open_w = _parse_odds_value(cells[3])
            open_d = _parse_odds_value(cells[4])
            open_l = _parse_odds_value(cells[5])
            close_w = _parse_odds_value(cells[6])
            close_d = _parse_odds_value(cells[7])
            close_l = _parse_odds_value(cells[8])

            if close_w <= 0:
                continue

            results.append({
                'match_id': match_id,
                'home': vs[0].strip(),
                'away': vs[1].strip(),
                'company': company,
                'open': {'w': open_w, 'd': open_d, 'l': open_l},
                'close': {'w': close_w, 'd': close_d, 'l': close_l},
            })
        except Exception:
            continue

    return results


# ─── Pinnacle 赔率（主赔率源）───
def fetch_pinnacle(date_str: str = None, page_type: str = None) -> Tuple[List[Dict], List[Dict]]:
    """抓取百家平均 + Pinnacle赔率"""
    print("  抓取百家平均...")
    avg_matches = fetch_list(date_str, page_type)
    print(f"  百家平均: {len(avg_matches)}场")

    print("  抓取Pinnacle...")
    pin_matches = fetch_company_odds(date_str, '106', page_type)
    print(f"  Pinnacle: {len(pin_matches)}场")

    # 合并Pinnacle到平均赔率
    pin_map = {(m['home'], m['away']): m for m in pin_matches}
    for m in avg_matches:
        key = (m['home'], m['away'])
        if key in pin_map:
            pm = pin_map[key]
            m['pinnacle_open'] = pm['open']
            m['pinnacle_close'] = pm['close']
        else:
            m['pinnacle_open'] = {'w': 0, 'd': 0, 'l': 0}
            m['pinnacle_close'] = {'w': 0, 'd': 0, 'l': 0}

    return avg_matches, pin_matches


# ─── HKJC 赔率（交叉验证）───
def fetch_hkjc(date_str: str = None, page_type: str = None) -> List[Dict]:
    print("  抓取HKJC...")
    hkjc = fetch_company_odds(date_str, '136', page_type)
    print(f"  HKJC: {len(hkjc)}场")
    return hkjc


def fetch_all(date_str: str = None) -> Dict:
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    print(f"📥 OddsMagnet 赔率抓取: {date_str}")

    # 竞彩赔率
    avg_jc, pin_jc = fetch_pinnacle(date_str, 'jc')
    hkjc_jc = fetch_hkjc(date_str, 'jc')

    # 将HKJC合并到avg
    hkjc_map = {(m['home'], m['away']): m for m in hkjc_jc}
    for m in avg_jc:
        key = (m['home'], m['away'])
        if key in hkjc_map:
            hm = hkjc_map[key]
            m['hkjc_open'] = hm['open']
            m['hkjc_close'] = hm['close']
        else:
            m['hkjc_open'] = {'w': 0, 'd': 0, 'l': 0}
            m['hkjc_close'] = {'w': 0, 'd': 0, 'l': 0}

    output = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'matches': avg_jc,
        'summary': {
            'avg_count': len(avg_jc),
            'pinnacle_count': len(pin_jc),
            'hkjc_count': len(hkjc_jc),
            'pinnacle_coverage': round(len(pin_jc) / max(len(avg_jc), 1) * 100, 1),
        }
    }

    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, f"{date_str.replace('-','')}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ → {out_path}")
    return output


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', type=str, default=None)
    args = p.parse_args()
    fetch_all(args.date)
