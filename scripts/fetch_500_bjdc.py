#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""500.com 北单双玩法: 让球胜平负(playid=3) + 胜负过关(playid=0)

用途: 取每场北单比赛的『让球 + 固定赔率』, 供亚盘初盘/即时盘两行使用。
两页共用同一 fid 空间, 可直接按 fid 对照/join。

  https://trade.500.com/bjdc/     让球胜平负: 让球(rangqiuNum) + 3way SP(主/平/客)
  https://trade.500.com/bjdcsf/   胜负过关  : 让球(rq)         + 2way SP(主胜/客胜)

输出: {fid: {league, home, away, date, time, rq_hcp, rq_sp[], sf_hcp, sf_sp[]}}
"""
import difflib, gzip, json, os, re, sys, urllib.request
from datetime import datetime, timedelta

UA = ('Mozilla/5.0 (Linux; Android 13; Mi 11 Lite) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36')


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Referer': 'https://trade.500.com/'})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', errors='replace')


def _js_val(blob, key):
    m = re.search(rf"{key}:\s*'([^']*)'", blob)
    return m.group(1) if m else ''


def fetch_rangqiu():
    """让球胜平负 -> {fid: {...}}"""
    t = get('https://trade.500.com/bjdc/')
    out = {}
    for tr in re.findall(r'<tr class="vs_lines".*?</tr>', t, re.S):
        fm = re.search(r'fid="(\d+)"', tr)
        vm = re.search(r'value="\{([^}]*)\}"', tr)
        if not (fm and vm):
            continue
        v = vm.group(1)
        hcp = _js_val(v, 'rangqiuNum')
        sps = re.findall(r'class="sp_w35 eng pjoz">([\d.]+)<', tr)
        out[fm.group(1)] = {
            'league': _js_val(v, 'leagueName'), 'home': _js_val(v, 'homeTeam'),
            'away': _js_val(v, 'guestTeam'), 'date': _js_val(v, 'scheduleDate'),
            'time': _js_val(v, 'endTime').split(' ')[-1], 'hcp': hcp, 'sp': sps[:3]}
    return out


def fetch_sf():
    """胜负过关 -> {fid: {...}}"""
    t = get('https://trade.500.com/bjdcsf/')
    out = {}
    for tr in re.findall(r'<tr[^>]*fid="\d+".*?</tr>', t, re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tr.split('>')[0]))
        if not a.get('fid'):
            continue
        sps = re.findall(r'data-type="sf" value="\d+" data-sp="([\d.]+)"', tr)
        out[a['fid']] = {'home': a.get('homesxname', ''), 'away': a.get('awaysxname', ''),
                         'date': a.get('gdate', ''), 'rq': a.get('rq', ''), 'sp': sps}
    return out


QUARTER = {0: '平手', 0.25: '平手/半球', 0.5: '半球', 0.75: '半球/一球',
           1.0: '一球', 1.25: '一球/球半', 1.5: '球半', 1.75: '球半/两球',
           2.0: '两球', 2.25: '两球/两球半', 2.5: '两球半', 3.0: '三球'}


def hcp_text(v):
    """北单让球数 -> 展示文本 (正数=主队受让, 负数=主队让球), 与存量 ah_handicap_text 口径一致"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ''
    if abs(v) < 1e-9:
        return '平手'
    name = QUARTER.get(abs(v), f'{abs(v):g}球')
    return ('受让' + name) if v > 0 else name


WATER_MIN, WATER_MAX = 0.20, 14.0      # SP-1 水位合理区间; 区间外=未开售占位(实测有 1.03/31.00)


def water(sp):
    """固定赔率 -> 水位(SP-1); 越界/非法返回 None"""
    try:
        w = float(sp) - 1.0
    except (TypeError, ValueError):
        return None
    return w if WATER_MIN <= w <= WATER_MAX else None


def build_fields(rq_row, sf_row):
    """(让球胜平负, 胜负过关) -> {ahbd_open_*, ahbd_cur_*}  (A 方案: 初盘行=让球胜平负, 即时盘行=胜负过关)"""
    f = {}
    if rq_row and len(rq_row['sp']) >= 3:
        h, a = water(rq_row['sp'][0]), water(rq_row['sp'][2])
        if h is not None and a is not None:
            v = rq_row['hcp']
            f.update(ahbd_open_home=round(h, 2), ahbd_open_away=round(a, 2),
                     ahbd_open_handicap=float(v or 0), ahbd_open_handicap_text=hcp_text(v),
                     ahbd_open_push=round(float(rq_row['sp'][1]) - 1.0, 2))
    if sf_row and len(sf_row['sp']) >= 2:
        h, a = water(sf_row['sp'][0]), water(sf_row['sp'][1])
        if h is not None and a is not None:
            v = sf_row['rq']
            f.update(ahbd_cur_home=round(h, 2), ahbd_cur_away=round(a, 2),
                     ahbd_cur_handicap=float(v or 0), ahbd_cur_handicap_text=hcp_text(v))
    return f


AHD_KEYS = ('ahbd_open_home', 'ahbd_open_away', 'ahbd_open_handicap',
            'ahbd_open_handicap_text', 'ahbd_open_push',
            'ahbd_cur_home', 'ahbd_cur_away', 'ahbd_cur_handicap', 'ahbd_cur_handicap_text')

# ---- 队名对齐 (500.com 简体短名 vs 我方繁体全名, fid 空间不同, 只能模糊匹配) ----
try:
    from opencc import OpenCC
    _CC, _CC2 = OpenCC('s2t'), OpenCC('t2s')
except Exception:                                     # opencc 缺失时退化为原文比较
    _CC = _CC2 = None


def _norm(s):
    s = _CC.convert(s or '') if _CC else (s or '')
    return re.sub(r'[\s\(\)（）·.\-]|FC|fc|队|隊|U23|女足', '', s)


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def _shift(d, delta):
    try:
        return (datetime.strptime(d, '%Y-%m-%d') + timedelta(days=delta)).strftime('%Y-%m-%d')
    except ValueError:
        return d


JOIN_MIN, JOIN_DAYS = 1.3, 1        # 双队名相似度之和下限 / 日期容差(天)


def join_rows(rows, matches):
    """500.com 行 -> 我方 match; 按 (队名相似度, 日期±1天) 一对一配对"""
    by_day = {}
    for m in matches:
        by_day.setdefault((m.get('date') or '')[:10], []).append(m)
    cand = []
    for fid, r in rows.items():
        d = (r.get('dt') or r.get('date') or '')[:10]
        pool = []
        for k in range(-JOIN_DAYS, JOIN_DAYS + 1):
            pool += by_day.get(_shift(d, k), [])
        for m in pool:
            sc = _sim(r.get('h') or r.get('home'), m.get('home_team')) + \
                 _sim(r.get('a') or r.get('away'), m.get('away_team'))
            if sc >= JOIN_MIN:
                cand.append((sc, fid, id(m), m, r))
    cand.sort(key=lambda x: -x[0])
    pairs, used_row, used_m = [], set(), set()
    for sc, fid, mid, m, r in cand:
        if fid in used_row or mid in used_m:
            continue
        used_row.add(fid)
        used_m.add(mid)
        pairs.append((sc, m, r))
    return pairs


def write_into(path, dry=False):
    """把两玩法的让球/水位写进 results.json 的 ahbd_* 字段 (不碰 ah_*, 不参与规则)
    A 方案: ahbd_open_* <- 让球胜平负(bjdc) ; ahbd_cur_* <- 胜负过关(bjdcsf)"""
    data = json.load(open(path, encoding='utf-8'))
    ms = data.get('matches') if isinstance(data, dict) else data
    rq, sf = fetch_rangqiu(), fetch_sf()
    jr = {id(m): r for _, m, r in join_rows(rq, ms)}
    js = {id(m): r for _, m, r in join_rows(sf, ms)}
    n_open = n_cur = 0
    for m in ms:
        f = build_fields(jr.get(id(m)), js.get(id(m)))
        for k in AHD_KEYS:
            m.pop(k, None)
        if f:
            m.update(f)
            n_open += 1 if 'ahbd_open_home' in f else 0
            n_cur += 1 if 'ahbd_cur_home' in f else 0
    if not dry:
        json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'500.com: 让球胜平负 {len(rq)} 行 / 胜负过关 {len(sf)} 行 | 配对到我方 {len(jr)} / {len(js)}')
    print(f'{"[dry] " if dry else ""}写入: 初盘行(让球胜平负) {n_open} 场 | 即时盘行(胜负过关) {n_cur} 场')
    n = 0
    for m in ms:
        if m.get('ahbd_cur_home') is not None or m.get('ahbd_open_home') is not None:
            if n < 6:
                print('  样例', m.get('date'), m.get('home_team'), 'vs', m.get('away_team'),
                      '| 亚初', m.get('ahbd_open_home'), m.get('ahbd_open_handicap_text'), m.get('ahbd_open_away'),
                      '| 亚即', m.get('ahbd_cur_home'), m.get('ahbd_cur_handicap_text'), m.get('ahbd_cur_away'))
            n += 1
    return n_open, n_cur


def main():
    if '--write' in sys.argv or '--dry' in sys.argv:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'docs', 'data', 'results.json')
        if '--results' in sys.argv:
            p = sys.argv[sys.argv.index('--results') + 1]
        write_into(p, dry='--write' not in sys.argv)
        return
    rq, sf = fetch_rangqiu(), fetch_sf()
    print(f'让球胜平负: {len(rq)} 场 | 胜负过关: {len(sf)} 场 | 共同 fid: {len(set(rq) & set(sf))}')
    both = sorted(set(rq) & set(sf))
    for f in both[:8]:
        r, s = rq[f], sf[f]
        print(f"  fid={f} {r['league']} {r['home']} vs {r['away']}")
        print(f"     让球胜平负: 让 {r['hcp']:>4}  SP {r['sp']}")
        print(f"     胜负过关  : 让 {s['rq']:>4}  SP {s['sp']}")
    if '--out' in sys.argv:
        p = sys.argv[sys.argv.index('--out') + 1]
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   'source': 'trade.500.com bjdc(playid=3) + bjdcsf(playid=0)',
                   'rangqiu': rq, 'shengfu': sf},
                  open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已落盘:', p)


if __name__ == '__main__':
    main()
