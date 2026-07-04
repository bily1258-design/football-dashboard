#!/usr/bin/env python3
"""gen_oddsmagnet_json.py — 从500.com生成 oddsmagnet JSON 格式数据

直接从500.com抓取百家平均欧赔，输出到 data/raw/oddsmagnet/{date}.json
供 predict_from_odds.py 使用。

用法:
  python gen_oddsmagnet_json.py --date 2026-07-05
  python gen_oddsmagnet_json.py --date 2026-07-05 --db data/football.db   # 同时更新DB中的fid
"""

import os, sys, re, json, math, urllib.request, argparse
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

WHITELIST = {
    '中超', '中甲', 'K1联赛', 'K2联赛', '芬超', '芬甲', '冰岛超',
    '瑞典超', '挪甲', '爱甲', '美冠', '巴乙', '厄甲', '世界杯',
}

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch_page(url, encoding='gbk'):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    try:
        return raw.decode(encoding)
    except:
        return raw.decode('utf8', errors='replace')


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        **HEADERS,
        'Referer': url,
        'X-Requested-With': 'XMLHttpRequest',
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode('utf-8', errors='replace').strip()
    if raw.startswith('(') and raw.endswith(')'):
        raw = raw[1:-1]
    return json.loads(raw)


def get_matches_from_wanchang(date_str):
    """从 wanchang.php 获取比赛列表"""
    url = f'https://live.500.com/wanchang.php?e={date_str}'
    html = fetch_page(url)
    m = re.search(r'<table[^>]*id="table_match"[^>]*>(.*?)</table>', html, re.DOTALL)
    if not m:
        print(f"  ⚠ 未找到比赛表格")
        return []
    content = m.group(1)
    rows = re.findall(r'<tr[^>]*>.*?</tr>', content, re.DOTALL)
    matches = []
    for row in rows[1:]:
        fid_m = re.search(r'ouzhi-(\d+)\.shtml', row)
        if not fid_m:
            continue
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row)
        if len(cells) < 6:
            continue
        league = re.sub(r'<[^>]+>', '', cells[0]).strip()
        if league not in WHITELIST:
            continue
        match_time = re.sub(r'<[^>]+>', '', cells[2]).strip()
        home = re.sub(r'\[?\d+\]?', '', re.sub(r'<[^>]+>', '', cells[3])).strip()
        away = re.sub(r'\[?\d+\]?', '', re.sub(r'<[^>]+>', '', cells[5])).strip()
        matches.append({
            'fid': fid_m.group(1),
            'league': league,
            'time': match_time,
            'home': home,
            'away': away,
        })
    return matches


def resolve_kickoff(match_time, date_str):
    """把 07-05 18:30 转为完整 ISO 时间"""
    parts = match_time.replace('(', '').replace(')', '').split()
    if len(parts) >= 2:
        md, hm = parts[0], parts[1]
        # 如果是 MM-DD 格式
        if '-' in md:
            month, day = md.split('-')
        else:
            # 可能已经是完整日期: 2026-07-05
            return f"{match_time}:00" if ':' in match_time and match_time.count(':') == 1 else match_time
        if ':' in hm:
            return f"{date_str[:4]}-{month}-{day} {hm}:00"
    return match_time


def fetch_avg_odds(fid):
    """获取某fid的百家平均欧赔"""
    url = f'https://odds.500.com/fenxi/json/ouzhi.php?fid={fid}&type=europe&r=1'
    try:
        data = fetch_json(url)
        if isinstance(data, list) and len(data) > 0:
            latest = data[0]
            w, d, l, ret_rate, ts = latest
            margin = 1.0/w + 1.0/d + 1.0/l - 1.0
            prob_w = (ret_rate / 100.0) / w
            prob_d = (ret_rate / 100.0) / d
            prob_l = (ret_rate / 100.0) / l
            total_p = prob_w + prob_d + prob_l
            return {
                'odds_w': float(w),
                'odds_d': float(d),
                'odds_l': float(l),
                'margin': round(margin, 4),
                'return_rate': float(ret_rate),
                'timestamp': ts,
                'implied_prob': {
                    'w': round(prob_w / total_p, 4),
                    'd': round(prob_d / total_p, 4),
                    'l': round(prob_l / total_p, 4),
                }
            }
    except Exception as e:
        print(f"  ⚠ fid={fid} 抓取失败: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description='从500.com生成oddsmagnet JSON')
    parser.add_argument('--date', type=str, required=True, help='日期 (YYYY-MM-DD)')
    args = parser.parse_args()

    date_str = args.date
    date_key = date_str.replace('-', '')

    print(f"=== 从500.com生成 oddsmagnet JSON: {date_str} ===")

    # 1. 从 wanchang.php 获取比赛列表
    print(f"\n[1/3] 抓取 wanchang.php?e={date_str} ...")
    matches = get_matches_from_wanchang(date_str)
    print(f"  找到 {len(matches)} 场白名单比赛")

    if not matches:
        print("  ⚠ 没有找到比赛，退出")
        return

    # 2. 逐个抓取百家平均赔率
    print(f"\n[2/3] 抓取百家平均赔率 ...")
    om_matches = {}
    success = 0
    for i, m in enumerate(matches):
        fid = m['fid']
        print(f"  [{i+1}/{len(matches)}] fid={fid} {m['home']} vs {m['away']} ...", end=' ')
        odds = fetch_avg_odds(fid)
        if odds:
            key = f"{m['home']}_{m['away']}".replace(' ', '_')
            kickoff = resolve_kickoff(m['time'], date_str)
            om_matches[key] = {
                'info': {
                    'fid_500': int(fid),
                    'league': m['league'],
                    'kickoff': kickoff,
                    'home': m['home'],
                    'away': m['away'],
                },
                'odds': {
                    'avg': odds,
                },
                'lookup': {
                    'match_key': f"{m['home']} vs {m['away']}",
                }
            }
            success += 1
            print(f"✓ W={odds['odds_w']} D={odds['odds_d']} L={odds['odds_l']}")
        else:
            print(f"✗ 无赔率")

    output = {'date': date_str, 'matches': om_matches}

    # 3. 保存JSON
    print(f"\n[3/3] 保存到 data/raw/oddsmagnet/{date_key}.json ...")
    out_dir = os.path.join(REPO_DIR, 'data', 'raw', 'oddsmagnet')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{date_key}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 已保存: {out_path} ({success}/{len(matches)} 场有赔率)")


if __name__ == '__main__':
    main()
