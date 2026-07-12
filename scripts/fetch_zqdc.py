#!/usr/bin/env python3
"""fetch_zqdc.py — 从500.com zqdc页面抓取北单比赛数据

抓取zqdc期号页面，提取比赛信息+北单赔率(liveOddsList["3"])。
支持自动在已知期号中查找。

用法:
  python3 scripts/fetch_zqdc.py                              # 今天
  python3 scripts/fetch_zqdc.py --date 2026-07-12            # 指定日期
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --period 26074  # 指定期号

输出: data/matches_{YYYYMMDD}.json
"""
import re, json, os, argparse, urllib.request
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
KNOWN_PERIODS = ['26072', '26073', '26074']

def fetch(period):
    url = f'https://live.500.com/zqdc.php?e={period}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('gbk', errors='replace')

def parse(html, date_str):
    target_md = date_str[5:]  # MM-DD

    # 1) 解析 liveOddsList — 北单赔率 {fid: {"3": [w,d,l], ...}}
    odds_data = {}
    m = re.search(r'var liveOddsList\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            odds_data = json.loads(m.group(1))
        except Exception as e:
            print(f"[WARN] liveOddsList解析失败: {e}")

    # 2) 解析比赛行 <tr id="aXXXX" status="..." gy="联赛,主队,客队" ...>
    matches = []
    # 匹配所有tr行
    for tr_m in re.finditer(r'<tr\s+id="a(\d+)"[^>]*status="(\d+)"[^>]*gy="([^"]*)"[^>]*yy="([^"]*)"[^>]*>.*?</tr>', html, re.DOTALL):
        fid, status, gy_str, yy_str = tr_m.group(1), tr_m.group(2), tr_m.group(3), tr_m.group(4)
        row = tr_m.group(0)

        # 时间
        t_m = re.search(r'<td[^>]*align="center"[^>]*>(\d{2}-\d{2}\s+\d{2}:\d{2})</td>', row)
        if not t_m:
            continue
        t = t_m.group(1)
        if not t.startswith(target_md):
            continue

        # gy = "联赛,主队,客队"
        parts = gy_str.split(',')
        event = parts[0] if len(parts) >= 1 else ''
        home = parts[1] if len(parts) >= 2 else ''
        away = parts[2] if len(parts) >= 3 else ''

        # 评分
        score_m = re.search(r'<td[^>]*align="center"[^>]*class="[^"]*red[^"]*"[^>]*>(\d+)\s*-\s*(\d+)</td>', row)
        score = f"{score_m.group(1)}-{score_m.group(2)}" if score_m else ''

        # 3) 北单赔率 liveOddsList[fid]["3"]
        o3 = odds_data.get(fid, {}).get("3", [])
        zw = float(o3[0]) if len(o3) >= 1 and o3[0] else 0.0
        zd = float(o3[1]) if len(o3) >= 2 and o3[1] else 0.0
        zl = float(o3[2]) if len(o3) >= 3 and o3[2] else 0.0

        matches.append({
            'fid': fid,
            'date': date_str,
            'match_time': t,
            'event': event,
            'home_team': home,
            'away_team': away,
            'score': score,
            'status': status,
            'source': 'beidan',
            'odds_win': zw,
            'odds_draw': zd,
            'odds_loss': zl,
            'odds_zqdc_win': zw,
            'odds_zqdc_draw': zd,
            'odds_zqdc_loss': zl,
        })

    return sorted(matches, key=lambda x: x['match_time'])

def find_period(date_str, known_periods):
    """逐个期号查找，返回(期号, 比赛列表)"""
    for p in known_periods:
        html = fetch(p)
        ms = parse(html, date_str)
        if ms:
            return p, ms
    return known_periods[-1], []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--period')
    args = parser.parse_args()

    if args.period:
        print(f"[INFO] 获取期号 {args.period} 的 {args.date} 数据...")
        html = fetch(args.period)
        ms = parse(html, args.date)
        period = args.period
    else:
        period, ms = find_period(args.date, KNOWN_PERIODS)
        print(f"[INFO] 期号 {period} → {len(ms)} 场")

    if not ms:
        print(f"[WARN] {args.date} 无比赛数据")
        return

    out = {
        'date': args.date,
        'period': period,
        'fetched_at': datetime.now().isoformat(),
        'total': len(ms),
        'matches': ms,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    fpath = os.path.join(DATA_DIR, f'matches_{args.date.replace("-", "")}.json')
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(ms)} 场 → {fpath}")

if __name__ == '__main__':
    main()
