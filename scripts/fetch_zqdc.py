#!/usr/bin/env python3
"""fetch_zqdc.py — 从500.com抓取zqdc(北单/竞彩)+平博赔率数据

抓取zqdc期号页面，提取比赛信息+北单赔率(liveOddsList["3"])，
再通过odds.500.com API获取平博(1055)的1X2开盘/即时赔率。

用法:
  python3 scripts/fetch_zqdc.py                                     # 今天
  python3 scripts/fetch_zqdc.py --date 2026-07-12                   # 指定日期
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --period 26074    # 指定期号
  python3 scripts/fetch_zqdc.py --date 2026-07-12 --no-pinnacle     # 跳过平博

输出: data/matches_{YYYYMMDD}.json
"""
import re, json, os, argparse, urllib.request, time
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
KNOWN_PERIODS = ['26072', '26073', '26074', '26075']

# ========== 期号自动发现 ==========

def fetch_available_periods():
    """从500.com live_expect_list 动态获取可用期号列表"""
    starters = ['26075', '26074', '26073']
    for p in starters:
        try:
            html = fetch(p)
            m = re.search(r"window\.live_expect_list\s*=\s*\[([^\]]+)\]", html)
            if m:
                periods = [x.strip().strip('"\'') for x in m.group(1).split(',')]
                periods = [x for x in periods if x.isdigit()]
                if periods:
                    print(f"[INFO] 发现期号: {periods[0]} ~ {periods[-1]} ({len(periods)}期)")
                    return periods
        except Exception:
            continue
    print("[WARN] 无法自动发现期号, 回退硬编码列表")
    return list(KNOWN_PERIODS)

# 500.com赔率API请求头
API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'X-Requested-With': 'XMLHttpRequest',
}

# ========== 基础抓取 ==========

def fetch(period):
    url = f'https://live.500.com/zqdc.php?e={period}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('gbk', errors='replace')

def fetch_json(url, retries=2, referer=None):
    """请求500.com JSON接口"""
    for attempt in range(retries + 1):
        try:
            headers = dict(API_HEADERS)
            if referer:
                headers['Referer'] = referer
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='replace').strip()
                if raw.startswith('(') and raw.endswith(')'):
                    raw = raw[1:-1]
                return json.loads(raw) if raw else None
        except Exception:
            if attempt == retries:
                return None
            time.sleep(1)
    return None

def fetch_1x2_odds(fid, cid):
    """获取指定公司的1X2开盘和最新赔率，返回{open: {w,d,l}, latest: {w,d,l}}或None"""
    url = f'https://odds.500.com/fenxi/json/ouzhi.php?fid={fid}&cid={cid}&type=europe'
    referer = f'https://odds.500.com/fenxi/ouzhi-{fid}.shtml'
    data = fetch_json(url, referer=referer)
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    try:
        opening = data[-1]  # API返回倒序(最新在前)，最后一条=开盘
        latest = data[0]    # 第一条=最新
        return {
            'open': {'w': float(opening[0]), 'd': float(opening[1]), 'l': float(opening[2])},
            'latest': {'w': float(latest[0]), 'd': float(latest[1]), 'l': float(latest[2])},
        }
    except (IndexError, ValueError, TypeError):
        return None

# ========== 解析zqdc页面 ==========

def parse(html, date_str):
    target_md = date_str[5:]  # MM-DD

    # 1) 解析比赛行 <tr id="aXXXX" ...>
    matches = []
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

        # 比分（仅当比赛已结束时提取）
        is_finished = bool(re.search(r'<span class="red">完</span>', row))
        if is_finished:
            bd_m = re.search(r'<div class="pk">.*?<a[^>]*class="clt1"[^>]*>(\d+)</a><span>-</span><a[^>]*class="clt3"[^>]*>(\d+)</a>', row)
            if bd_m:
                score = f"{bd_m.group(1)}-{bd_m.group(2)}"
            else:
                score_m = re.search(r'<td[^>]*align="center"[^>]*class="[^"]*red[^"]*"[^>]*>(\d+)\s*-\s*(\d+)</td>', row)
                score = f"{score_m.group(1)}-{score_m.group(2)}" if score_m else ''
        else:
            score = ''


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
        })

    return sorted(matches, key=lambda x: x['match_time'])

# ========== 追加平博赔率 ==========

def enhance_with_pinnacle(matches, delay=0.3):
    """为每场比赛获取平博(cid=1055)的收盘赔率"""
    pinnacle_ok = 0
    total = len(matches)

    for i, m in enumerate(matches):
        fid = m['fid']
        print(f"  [{i+1}/{total}] fid={fid} {m['home_team']} vs {m['away_team']}...", end=' ', flush=True)

        # 平博
        p = fetch_1x2_odds(fid, 1055)
        if p:
            m['odds_pinnacle_open_win'] = p['open']['w']
            m['odds_pinnacle_open_draw'] = p['open']['d']
            m['odds_pinnacle_open_loss'] = p['open']['l']
            m['odds_pinnacle_win'] = p['latest']['w']
            m['odds_pinnacle_draw'] = p['latest']['d']
            m['odds_pinnacle_loss'] = p['latest']['l']
            pinnacle_ok += 1
            print(f"平博开盘{p['open']['w']}/{p['open']['d']}/{p['open']['l']} 最新{p['latest']['w']}/{p['latest']['d']}/{p['latest']['l']}")
        else:
            print("平博-")

        if delay > 0 and i < total - 1:
            time.sleep(delay)

    return pinnacle_ok

def enhance_with_hkjc(matches, delay=0.3):
    """为每场比赛获取香港马会(cid=122)的收盘赔率"""
    hkjc_ok = 0
    total = len(matches)
    for i, m in enumerate(matches):
        fid = m['fid']
        print(f"  [{i+1}/{total}] fid={fid} {m['home_team']} vs {m['away_team']}...", end=' ', flush=True)
        h = fetch_1x2_odds(fid, 122)
        if h:
            m['odds_hkjc_open_win'] = h['open']['w']
            m['odds_hkjc_open_draw'] = h['open']['d']
            m['odds_hkjc_open_loss'] = h['open']['l']
            m['odds_hkjc_win'] = h['latest']['w']
            m['odds_hkjc_draw'] = h['latest']['d']
            m['odds_hkjc_loss'] = h['latest']['l']
            hkjc_ok += 1
            print(f"HKJC开盘{h['open']['w']}/{h['open']['d']}/{h['open']['l']} 最新{h['latest']['w']}/{h['latest']['d']}/{h['latest']['l']}")
        else:
            print("HKJC-")
        if delay > 0 and i < total - 1:
            time.sleep(delay)
    return hkjc_ok


# ========== 期号查找 ==========

def find_period(date_str):
    """自动发现可用期号，逐个查找含指定日期的比赛，返回(期号, 比赛列表)"""
    periods = fetch_available_periods()
    for p in periods:
        try:
            html = fetch(p)
            ms = parse(html, date_str)
            if ms:
                return p, ms, periods
        except Exception:
            continue
    # 所有期号都无匹配 → 用最后一个期号返回空
    return periods[-1] if periods else known_periods[-1], [], periods

# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--period')
    parser.add_argument('--no-pinnacle', action='store_true', help='跳过平博抓取')
    parser.add_argument('--no-hkjc', action='store_true', help='跳过香港马会抓取')
    parser.add_argument('--backfill', action='store_true', help='比分回填：只更新比分，保留已有赔率')
    args = parser.parse_args()

    # ===== 比分回填模式 =====
    if args.backfill:
        fpath = os.path.join(DATA_DIR, f'matches_{args.date.replace("-", "")}.json')
        if not os.path.exists(fpath):
            print(f"[WARN] {fpath} 不存在，跳过回填")
            return
        # 1) 读取已有数据（含赔率）
        with open(fpath) as f:
            existing = json.load(f)
        existing_matches = {m['fid']: m for m in existing['matches']}
        # 2) 抓取期号页面获取最新比分
        if args.period:
            html = fetch(args.period)
        else:
            period, ms, _ = find_period(args.date)
            html = fetch(period)
        fresh = parse(html, args.date)
        updated = 0
        for m in fresh:
            fid = m['fid']
            if fid in existing_matches:
                old_scr = existing_matches[fid].get('score', '')
                new_scr = m.get('score', '')
                if new_scr and new_scr != old_scr:
                    existing_matches[fid]['score'] = new_scr
                    updated += 1
        if updated:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f"[BACKFILL] {fpath} → {updated} 场比分已更新")
        else:
            print(f"[BACKFILL] {fpath} → 无变化")
        return

    if args.period:
        print(f"[INFO] 获取期号 {args.period} 的 {args.date} 数据...")
        html = fetch(args.period)
        ms = parse(html, args.date)
        period = args.period
    else:
        period, ms, available_periods = find_period(args.date)
        print(f"[INFO] 期号 {period} → {len(ms)} 场")

    if not ms:
        print(f"[WARN] {args.date} 无比赛数据")
        return

    # 追加平博赔率
    if not args.no_pinnacle:
        print("[INFO] 抓取平博赔率(收盘)...")
        p_ok = enhance_with_pinnacle(ms)
        print(f"[INFO] 平博 {p_ok}/{len(ms)}")

    # 追加香港马会赔率
    if not args.no_hkjc:
        print("[INFO] 抓取香港马会赔率(收盘)...")
        h_ok = enhance_with_hkjc(ms)
        print(f"[INFO] HKJC {h_ok}/{len(ms)}")

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
