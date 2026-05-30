#!/usr/bin/env python3
"""fetch_bsd.py — 从500.com抓取赛果（BSD主数据源）

输出：data/raw/bsd/{YYYYMMDD}.json
结构：{date, fetch_time, jingcai:[], wanchang:[], beidan:[], summary:{}}

兼容：纯requests，无需浏览器，Termux/GA均可用
"""

import os, re, json, requests
from datetime import datetime
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(REPO_DIR, "data", "raw", "bsd")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def _fetch(url: str, encoding: str = 'utf-8') -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  ❌ {url} → HTTP {r.status_code}")
            return None
        try:
            return r.content.decode(encoding)
        except UnicodeDecodeError:
            return r.content.decode('gbk', errors='replace')
    except Exception as e:
        print(f"  ❌ {url} → {e}")
        return None


def _clean(html_frag: str) -> str:
    return re.sub(r'<[^>]+>', '', html_frag).strip()


# ─── 竞彩开奖 ───
def fetch_jingcai(date_str: str) -> List[Dict]:
    url = f"https://zx.500.com/jczq/kaijiang.php?play=0&beg={date_str}&end={date_str}"
    html = _fetch(url)
    if not html:
        return []
    results = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 6:
            continue
        try:
            mid_m = re.search(r'周[一二三四五六日]\d+', cells[0])
            league = _clean(cells[0])
            ko_m = re.search(r'(\d{2}-\d{2}\s*\d{2}:\d{2})', cells[1])
            teams = _clean(cells[2])
            vs = re.split(r'\s*vs\s*', teams, flags=re.I)
            hcap_m = re.search(r'[-+]?\d+', _clean(cells[3]))
            score_m = re.search(r'(\d+)\s*[-:]\s*(\d+)', _clean(cells[4]))
            if not score_m or len(vs) < 2:
                continue
            hs, as_ = int(score_m.group(1)), int(score_m.group(2))
            # 赔率（如果有的话）
            odds_w = _clean(cells[6]) if len(cells) > 6 else ''
            odds_d = _clean(cells[7]) if len(cells) > 7 else ''
            odds_l = _clean(cells[8]) if len(cells) > 8 else ''
            results.append({
                'source': 'jingcai_kaijiang',
                'match_id': mid_m.group() if mid_m else '',
                'league': league,
                'kickoff': ko_m.group(1).strip() if ko_m else '',
                'home': vs[0].strip(), 'away': vs[1].strip(),
                'handicap': int(hcap_m.group()) if hcap_m else 0,
                'score': f"{hs}-{as_}", 'home_score': hs, 'away_score': as_,
                'result_hcap': '胜' if hs > as_ else ('负' if hs < as_ else '平'),
                'bonus': _clean(cells[5]) if len(cells) > 5 else '',
                'odds_w': odds_w, 'odds_d': odds_d, 'odds_l': odds_l,
            })
        except Exception:
            continue
    print(f"  竞彩开奖: {len(results)}场")
    return results


# ─── 完场热门 ───
def fetch_wanchang() -> List[Dict]:
    url = "https://live.500.com/wanchang.php"
    html = _fetch(url)
    if not html:
        return []
    results = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 6:
            continue
        try:
            score_m = re.search(r'(\d+)\s*[-:]\s*(\d+)', _clean(cells[4]))
            if not score_m:
                continue
            hs, as_ = int(score_m.group(1)), int(score_m.group(2))
            half_m = re.search(r'(\d+)\s*[-:]\s*(\d+)', _clean(cells[2]))
            results.append({
                'source': 'wanchang',
                'league': _clean(cells[0]),
                'kickoff': _clean(cells[1]),
                'home': _clean(cells[3]), 'away': _clean(cells[5]),
                'score': f"{hs}-{as_}", 'home_score': hs, 'away_score': as_,
                'outcome': '主胜' if hs > as_ else ('客胜' if hs < as_ else '平局'),
                'half_score': f"{half_m.group(1)}-{half_m.group(2)}" if half_m else '',
            })
        except Exception:
            continue
    print(f"  完场: {len(results)}场")
    return results


# ─── 北单 ───
def fetch_beidan() -> List[Dict]:
    url = "https://live.500.com/zqdc.php"
    html = _fetch(url)
    if not html:
        return []
    results = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 5:
            continue
        try:
            teams = _clean(cells[3])
            vs = re.split(r'\s*vs\s*', teams, flags=re.I)
            if len(vs) < 2:
                continue
            score_m = re.search(r'(\d+)\s*[-:]\s*(\d+)', _clean(cells[5]) if len(cells) > 5 else '')
            hs = int(score_m.group(1)) if score_m else None
            as_ = int(score_m.group(2)) if score_m else None
            results.append({
                'source': 'beidan',
                'match_id': _clean(cells[0]),
                'league': _clean(cells[1]),
                'kickoff': _clean(cells[2]),
                'home': vs[0].strip(), 'away': vs[1].strip(),
                'score': f"{hs}-{as_}" if hs is not None else '',
                'home_score': hs, 'away_score': as_,
                'outcome': ('主胜' if hs > as_ else ('客胜' if hs < as_ else '平局')) if hs is not None else '',
                'sp': _clean(cells[4]) if len(cells) > 4 else '',
            })
        except Exception:
            continue
    print(f"  北单: {len(results)}场")
    return results


def fetch_all(date_str: str = None) -> Dict:
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    print(f"📥 BSD 赛果抓取: {date_str}")

    jingcai = fetch_jingcai(date_str)
    wanchang = fetch_wanchang()
    beidan = fetch_beidan()

    output = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'jingcai': jingcai,
        'wanchang': wanchang,
        'beidan': beidan,
        'summary': {
            'jingcai': len(jingcai),
            'wanchang': len(wanchang),
            'beidan': len(beidan),
            'beidan_with_result': sum(1 for r in beidan if r.get('home_score') is not None),
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
