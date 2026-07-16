#!/usr/bin/env python3
"""
fetch_stats.py — 从500.com获取历史交锋(H2H)和近期战绩
"""
import re, json, time, sys
import urllib.request

HEADERS = {'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36'}
BASE = 'https://odds.500.com'

def decode_gbk(raw):
    for enc in ('gbk', 'gb18030', 'utf-8'):
        try: return raw.decode(enc)
        except: pass
    return raw.decode('utf-8', errors='replace')

def fetch_url(url):
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=15).read()
    return decode_gbk(raw)

def get_hash(fid):
    html = fetch_url(f'{BASE}/fenxi/stat-{fid}.shtml')
    m = re.search(r'<input[^>]*id="hash"[^>]*value="(\d+)"', html)
    return m.group(1) if m else None


# ─── 通用：从 pub_table 提取行 ─────────────────

def extract_table_rows(html):
    """从HTML中找到第一个 <table class="pub_table">, 返回所有行raw"""
    m = re.search(r'<table[^>]*class="[^"]*pub_table[^"]*"[^>]*>(.*?)</table>', html, re.DOTALL)
    if not m:
        return []
    table_html = m.group(1)
    # 删除 colgroup
    table_html = re.sub(r'<colgroup[^>]*>.*?</colgroup>', '', table_html, flags=re.DOTALL)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
    return rows


def parse_cells(tr_html):
    """从 <tr> 中提取每个 <td> 或 <th> 的纯文本"""
    cells = []
    for tag in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr_html, re.DOTALL):
        # 去标签、压缩空格
        text = re.sub(r'<[^>]+>', ' ', tag)
        text = re.sub(r'\s+', ' ', text).strip()
        cells.append(text)
    return cells


# ─── 比分解析 ──────────────────────────────────

def parse_score(text):
    """从 '2: 1', '1 :3', 'VS' 等提取 (home_score, away_score) 或 None"""
    m = re.search(r'(\d+)\s*[:：]\s*(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def split_home_away_score(cell):
    """拆分第三格: '[25] 蒙特利尔CF  1 : 6  多伦多FC [28]' → (home, hs, as, away)"""
    # 去掉 [rank]
    cell = re.sub(r'\[\d+\]', '', cell).strip()
    # 找比分: X:Y
    m = re.search(r'(\d+)\s*[:：]\s*(\d+)', cell)
    if not m:
        # 可能是 'VS' 还没踢
        parts = cell.split('VS')
        if len(parts) == 2:
            return parts[0].strip(), None, None, parts[1].strip()
        return cell, None, None, None

    score_start = m.start()
    score_end = m.end()
    home_part = cell[:score_start].strip()
    away_part = cell[score_end:].strip()
    hs = int(m.group(1))
    as_ = int(m.group(2))
    return home_part, hs, as_, away_part


# ─── H2H解析 ──────────────────────────────────

def parse_h2h(html):
    """解析历史交锋 pub_table"""
    rows = extract_table_rows(html)
    results = []
    for tr in rows:
        cells = parse_cells(tr)
        # 跳过表头
        if not cells or cells[0] == '赛事' or '赛事' in cells[0]:
            continue
        # row 1+ : 联赛 | 日期 | 主比分客 | 半场 | 赛果 | 欧指 | 盘口 | 盘路 | 大小 | 备注
        if len(cells) < 5:
            continue
        league = cells[0]
        date = cells[1]
        combined = cells[2]
        half_score = cells[3] if len(cells) > 3 else ''
        result = cells[4] if len(cells) > 4 else ''
        
        home, hs, as_, away = split_home_away_score(combined)
        if hs is None:
            continue  # 未进行的比赛

        results.append({
            'league': league,
            'date': date,
            'home': home,
            'away': away,
            'home_score': hs,
            'away_score': as_,
            'half_score': half_score[:10],
            'result': result,
        })
    return results


# ─── 近期战绩解析 ────────────────────────────

def parse_form(html):
    """解析近期战绩 pub_table"""
    rows = extract_table_rows(html)
    results = []
    for tr in rows:
        cells = parse_cells(tr)
        if not cells or cells[0] == '赛事' or '赛事' in cells[0]:
            continue
        # row 1+ : 联赛 | 日期 | 主比分客 | 盘口 | 半场 | 赛果 | 盘路 | 大小
        if len(cells) < 6:
            continue
        league = cells[0]
        date = cells[1]
        combined = cells[2]
        handicap = cells[3] if len(cells) > 3 else ''
        half_score = cells[4] if len(cells) > 4 else ''
        result = cells[5] if len(cells) > 5 else ''
        panlu = cells[6] if len(cells) > 6 else ''
        daxiao = cells[7] if len(cells) > 7 else ''

        home, hs, as_, away = split_home_away_score(combined)
        if hs is None:
            continue  # 未比赛

        results.append({
            'league': league,
            'date': date,
            'home': home,
            'away': away,
            'home_score': hs,
            'away_score': as_,
            'handicap': handicap,
            'half_score': half_score[:10],
            'result': result,
            'panlu': panlu,
            'daxiao': daxiao,
        })
    return results


# ─── 核心函数 ────────────────────────────────

def fetch_match_stats(fid, max_retries=2):
    """获取单场比赛的 H2H + 近期战绩"""
    fid_s = str(int(float(fid)))
    for attempt in range(max_retries + 1):
        try:
            hash_val = get_hash(fid_s)
            if not hash_val:
                time.sleep(0.5)
                continue

            params_base = {'id': fid_s, 'hash': hash_val, 'callback': 'ajax', 'r': '1'}

            # H2H
            params = {**params_base, 'limit': '10'}
            h2h_html = fetch_url(f'{BASE}/fenxi1/inc/shuju_jiaozhan.php?' + '&'.join(f'{k}={v}' for k, v in params.items()))
            h2h = parse_h2h(h2h_html)

            # 近期战绩
            def get_form(hoa):
                p = {**params_base, 'limit': '6', 'hoa': str(hoa), 'match': '0', 'bhbc': '0'}
                html = fetch_url(f'{BASE}/fenxi1/inc/shuju_zhanji.php?' + '&'.join(f'{k}={v}' for k, v in p.items()))
                return parse_form(html)

            home_form = get_form(0)
            away_form = get_form(1)

            return {'h2h': h2h, 'home_form': home_form, 'away_form': away_form}

        except Exception as e:
            if attempt < max_retries:
                time.sleep(0.5)
                continue
            return None


# ─── 特征提取 ────────────────────────────────

def extract_form_features(form_data, team_is_home=True):
    """从近期战绩提取数值特征"""
    if not form_data:
        return {'win_rate': 0.5, 'draw_rate': 0.25, 'loss_rate': 0.25,
                'goals_avg': 1.0, 'goals_conceded_avg': 1.0,
                'win_streak': 0, 'panlu_win_rate': 0.5, 'over_rate': 0.5}
    n = len(form_data)
    wins = 0; draws = 0; losses = 0
    goals_f = 0; goals_a = 0
    panlu_wins = 0; panlu_losses = 0
    overs = 0; unders = 0

    for m in form_data:
        hs = m.get('home_score', 0) or 0
        as_ = m.get('away_score', 0) or 0
        result = m.get('result', '')

        if team_is_home:
            if hs > as_: wins += 1
            elif hs == as_: draws += 1
            else: losses += 1
        else:
            if as_ > hs: wins += 1
            elif hs == as_: draws += 1
            else: losses += 1
        goals_f += hs if team_is_home else as_
        goals_a += as_ if team_is_home else hs

        panlu = m.get('panlu', '')
        if panlu == '赢': panlu_wins += 1
        elif panlu == '输': panlu_losses += 1
        daxiao = m.get('daxiao', '')
        if daxiao == '大': overs += 1
        elif daxiao == '小': unders += 1

    last_result = form_data[0].get('result', '') if form_data else ''
    win_streak = 1 if last_result in ('胜', '赢') else (-1 if last_result in ('负', '输') else 0)

    return {
        'win_rate': round(wins / max(n, 1), 4),
        'draw_rate': round(draws / max(n, 1), 4),
        'loss_rate': round(losses / max(n, 1), 4),
        'goals_avg': round(goals_f / max(n, 1), 2),
        'goals_conceded_avg': round(goals_a / max(n, 1), 2),
        'win_streak': win_streak,
        'panlu_win_rate': round(panlu_wins / max(panlu_wins + panlu_losses, 1), 4),
        'over_rate': round(overs / max(overs + unders, 1), 4),
    }


def extract_h2h_features(h2h_data):
    """从历史交锋提取特征"""
    if not h2h_data:
        return {'home_win_rate': 0.35, 'draw_rate': 0.3, 'away_win_rate': 0.35,
                'home_goals_avg': 1.0, 'away_goals_avg': 1.0,
                'total_matches': 0, 'recent_trend': 0, 'h2h_advantage': 0}
    home_wins = 0; draws = 0; away_wins = 0; n = 0
    home_goals = 0; away_goals = 0
    for m in h2h_data:
        hs = m.get('home_score')
        as_ = m.get('away_score')
        if hs is None or as_ is None: continue
        home_goals += hs; away_goals += as_
        n += 1
        if hs > as_: home_wins += 1
        elif hs == as_: draws += 1
        else: away_wins += 1
    recent_trend = 0
    if h2h_data:
        last = h2h_data[0]
        hs, as_ = last.get('home_score'), last.get('away_score')
        if hs is not None and as_ is not None:
            if hs > as_: recent_trend = 1
            elif hs < as_: recent_trend = -1
    return {
        'home_win_rate': round(home_wins / max(n, 1), 4),
        'draw_rate': round(draws / max(n, 1), 4),
        'away_win_rate': round(away_wins / max(n, 1), 4),
        'home_goals_avg': round(home_goals / max(n, 1), 2),
        'away_goals_avg': round(away_goals / max(n, 1), 2),
        'total_matches': n,
        'recent_trend': recent_trend,
        'h2h_advantage': round((home_wins - away_wins) / max(n, 1), 4),
    }


# ─── 自测 ────────────────────────────────────

if __name__ == '__main__':
    fids = sys.argv[1:] if len(sys.argv) > 1 else ['1358412']
    for fid in fids:
        print(f"\n{'='*60}")
        print(f"📊 获取比赛 fid={fid}")
        print('='*60)
        stats = fetch_match_stats(fid)
        if not stats:
            print("❌ 获取失败")
            continue

        print(f"\n📋 历史交锋 (H2H): {len(stats['h2h'])} 场")
        for h in stats['h2h'][:5]:
            print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']})")

        h2h_feat = extract_h2h_features(stats['h2h'])
        print(f"\n  H2H特征: 主胜率={h2h_feat['home_win_rate']:.0%} 平率={h2h_feat['draw_rate']:.0%} 客胜率={h2h_feat['away_win_rate']:.0%}")
        print(f"  主均进={h2h_feat['home_goals_avg']} 客均进={h2h_feat['away_goals_avg']}")

        print(f"\n📈 主队近期战绩: {len(stats['home_form'])} 场")
        for h in stats['home_form'][:5]:
            print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']}) [{h.get('panlu','')}/{h.get('daxiao','')}]")
        hf = extract_form_features(stats['home_form'], team_is_home=True)
        print(f"  胜率={hf['win_rate']:.0%} 均进={hf['goals_avg']} 均失={hf['goals_conceded_avg']} 盘路赢率={hf['panlu_win_rate']:.0%}")

        if stats['away_form']:
            print(f"\n📈 客队近期战绩: {len(stats['away_form'])} 场")
            for h in stats['away_form'][:5]:
                print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']}) [{h.get('panlu','')}/{h.get('daxiao','')}]")
            af = extract_form_features(stats['away_form'], team_is_home=False)
            print(f"  胜率={af['win_rate']:.0%} 均进={af['goals_avg']} 均失={af['goals_conceded_avg']} 盘路赢率={af['panlu_win_rate']:.0%}")
