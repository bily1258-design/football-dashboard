#!/usr/bin/env python3
"""fetch_zqdc.py — 从500.com zqdc页面抓取北单/竞彩比赛数据

用法:
  python3 scripts/fetch_zqdc.py
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --period 26074

输出: data/matches_{YYYYMMDD}.json
"""
import re, json, os, argparse, urllib.request
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
PERIOD_MAP = {'2026-07-03': '26072', '2026-07-08': '26073', '2026-07-10': '26074'}

def find_period(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d')
    doy = d.timetuple().tm_yday
    # 从已知映射找最近一期
    for ps, pn in sorted(PERIOD_MAP.items()):
        p_doy = datetime.strptime(ps, '%Y-%m-%d').timetuple().tm_yday
        if doy >= p_doy:
            return pn
    return '26072'

def fetch(period):
    url = f'https://live.500.com/zqdc.php?e={period}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('gbk', errors='replace')

def parse(html, date_str):
    target_md = date_str[5:10]
    # odds list 里有zqdc赔率
    odds_m = re.search(r'var liveOddsList\s*=\s*(\{.*?\});', html, re.DOTALL)
    odds_data = json.loads(odds_m.group(1)) if odds_m else {}

    tr_pat = re.compile(
        r'<tr[^>]*id="a(\d+)"[^>]*status="(\d+)"[^>]*gy="([^"]*)"[^>]*yy="([^"]*)"[^>]*>.*?</tr>', re.DOTALL)
    matches = []
    for fid, status, gy, yy in tr_pat.findall(html):
        row_m = re.search(rf'<tr[^>]*id="a{fid}"[^>]*>.*?</tr>', html, re.DOTALL)
        row = row_m.group(0) if row_m else ''
        t_m = re.search(r'(\d{2}-\d{2}\s+\d{2}:\d{2})', row)
        t = t_m.group(1) if t_m else '??'
        if not t.startswith(target_md):
            continue
        hm = re.search(r'id="hm_\d+"[^>]*>([^<]+)<', row)
        aw = re.search(r'id="aw_\d+"[^>]*>([^<]+)<', row)
        ev_m = re.search(r'class="gy"[^>]*>([^<]+)<', row)
        home = hm.group(1).strip() if hm else ''
        away = aw.group(1).strip() if aw else ''
        event = ev_m.group(1).strip() if ev_m else (gy or '')

        # 竞彩赔率
        def odds_val(fid, suffix):
            m = re.search(rf'id="od_{fid}_{suffix}"[^>]*>([^<]*)<', row)
            return float(m.group(1)) if m else 0.0

        ow = odds_val(fid, 'w')
        od = odds_val(fid, 'd')
        ol = odds_val(fid, 'l')

        # zqdc北单赔率
        zq = odds_data.get(fid, {})
        zw = float(zq.get('a', 0))
        zd = float(zq.get('b', 0))
        zl = float(zq.get('c', 0))

        # 判断是竞彩还是北单: 页面里有"北单"标识还是"竞彩"
        page_type = 'beidan'  # zqdc页面默认北单

        matches.append({
            'fid': fid,
            'date': date_str,
            'match_time': t,
            'event': event,
            'home_team': home,
            'away_team': away,
            'score': '',
            'status': status,
            'source': page_type,
            'odds_win': ow,
            'odds_draw': od,
            'odds_loss': ol,
            'odds_zqdc_win': zw,
            'odds_zqdc_draw': zd,
            'odds_zqdc_loss': zl,
        })
    return matches

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--period')
    args = parser.parse_args()
    period = args.period or find_period(args.date)
    print(f"[INFO] 获取期号 {period} 的 {args.date} 数据...")
    html = fetch(period)
    ms = parse(html, args.date)
    if not ms:
        print(f"[WARN] 未找到 {args.date} 的比赛")
        return
    out = {'date': args.date, 'period': period, 'fetched_at': datetime.now().isoformat(),
           'total': len(ms), 'matches': ms}
    os.makedirs(DATA_DIR, exist_ok=True)
    fpath = os.path.join(DATA_DIR, f'matches_{args.date.replace("-","")}.json')
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(ms)} 场 → {fpath}")

if __name__ == '__main__':
    main()
