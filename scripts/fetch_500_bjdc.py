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


def _period(t):
    """页面 responseJson 里的期号 (如 26096 = 当期)"""
    m = re.search(r'period\s*:\s*"(\d+)"', t)
    return m.group(1) if m else ''


# 北单期号: 500 每行的「场次编号」是【期号内】唯一的 —— 编号相同 + 期号相同 = 同一场。
# 但编号会跨期重复(实测 编77: 26096=忠南牙山/天安城, 26095=瓦埃勒/布隆德比),
# 且一期横跨多个比赛日, 所以「期号」不能按日期推断, 只能由命中行(期号+编号+队名三方一致)带出。
PERIOD_DEPTH = 2      # 抓 当前期 + 前一期 (我方清单常含上一期刚完赛的场次)


def fetch_plays(expect=''):
    """(让球胜平负行, 胜负过关行) —— 指定期号(空 = 当期)"""
    return fetch_rangqiu(expect), fetch_sf(expect)


def fetch_pool(depth=PERIOD_DEPTH):
    """多期池: [(期号, {fid: 让行}, {fid: 过行}), ...], 当期在前; 前一期拉不到就停"""
    rq0, sf0 = fetch_plays()
    cur = next((str(r.get('period')) for r in list(rq0.values()) + list(sf0.values())
                if r.get('period')), '')
    out = [(cur, rq0, sf0)]
    p = cur
    for _ in range(max(0, depth - 1)):
        if not p.isdigit():
            break
        p = str(int(p) - 1)
        try:
            rq, sf = fetch_plays(p)
        except Exception:
            break
        if not rq and not sf:
            break
        out.append((p, rq, sf))
    return out


def merge_pool(pool):
    """多期池 -> ({fid: 让行}, {fid: 过行}); 每行自带 period"""
    rq, sf = {}, {}
    for _, a, b in pool:
        rq.update(a)
        sf.update(b)
    return rq, sf


def fetch_rangqiu(expect=''):
    """让球胜平负 -> {fid: {...}}   num = 期号内场次编号(chnum)"""
    t = get('https://trade.500.com/bjdc/' + (f'?expect={expect}' if expect else ''))
    per = _period(t)
    out = {}
    for tr in re.findall(r'<tr class="vs_lines[^"]*".*?</tr>', t, re.S):
        fm = re.search(r'fid="(\d+)"', tr)
        vm = re.search(r'value="\{([^}]*)\}"', tr)
        if not (fm and vm):
            continue
        v = vm.group(1)
        hcp = _js_val(v, 'rangqiuNum')
        sps = re.findall(r'class="sp_w35 eng pjoz">([\d.]+)<', tr)
        cm = re.search(r'class="chnum">(\d+)<', tr)
        out[fm.group(1)] = {
            'league': _js_val(v, 'leagueName'), 'home': _js_val(v, 'homeTeam'),
            'away': _js_val(v, 'guestTeam'), 'date': _js_val(v, 'scheduleDate'),
            'time': _js_val(v, 'endTime').split(' ')[-1], 'hcp': hcp, 'sp': sps[:3],
            'num': cm.group(1) if cm else _js_val(v, 'index'), 'period': per}
    return out


def fetch_sf(expect=''):
    """胜负过关 -> {fid: {...}}   num = 期号内场次编号(ordernum), period = 期号(pdate)"""
    t = get('https://trade.500.com/bjdcsf/' + (f'?expect={expect}' if expect else ''))
    out = {}
    for tr in re.findall(r'<tr[^>]*fid="\d+".*?</tr>', t, re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tr.split('>')[0]))
        if not a.get('fid'):
            continue
        sps = re.findall(r'data-type="sf" value="\d+" data-sp="([\d.]+)"', tr)
        out[a['fid']] = {'home': a.get('homesxname', ''), 'away': a.get('awaysxname', ''),
                         'league': a.get('lg', '').replace('足球-', ''),
                         'time': a.get('pendtime', '').split(' ')[-1], 'num': a.get('ordernum', ''),
                         'period': a.get('pdate', ''),
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


# 译名别名: 500.com 用简体短名/亚运代称, 我方用繁体全名; 只收已逐条核对是同一场的对
# 键/值都是 _norm 之后的形式 (s2t + 去空格标点 + 去 FC/队/隊/U23/女足)
NAME_ALIAS = {
    '阿爾傑什': '阿格斯',            # FC Argeș (罗甲)
    '法爾肯貝裏': '法爾肯堡',        # Falkenberg (瑞典甲)
    '厄斯特松德': '奧斯特桑斯',      # Östersund
    '卡普芬貝格': '卡芬堡',          # Kapfenberg (奥乙)
    '阿姆施泰滕': '阿姆斯特頓',      # Amstetten
    '佈雷流浪者': '佈雷',            # Bray Wanderers (爱甲)
    '條約聯': '特瑞特聯',            # Treaty United
    '葡萄牙體育': '里斯本',          # Sporting CP (葡超)
    '布瑞恩斯': '法蘭波壘斯',        # Francs Borains (比乙)
    '沙特亞運男足': '沙特阿拉伯', '科威特亞運男足': '科威特',
    '卡塔爾亞運男足': '卡塔爾', '烏茲別克亞運男足': '烏茲別克斯坦',
    '布魯日NXT': '布魯日', '布魯日B': '布魯日',
    '根特預備': '根特', '根特B': '根特',
    '安德萊赫特預備': '安德萊赫特', '安德萊赫特B': '安德萊赫特',
}


def _norm(s):
    s = _CC.convert(s or '') if _CC else (s or '')
    s = re.sub(r'[\s\(\)（）·.\-]|FC|fc|队|隊|U23|女足', '', s)
    return NAME_ALIAS.get(s, s)


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
NUM_SCORE = 2.0                     # 场次编号命中(精确配对) 分数
NUM_NAME_MIN = 0.6                  # 编号命中仍要求的最低队名相似度和
NUM_DAYS = 2                        # 编号命中允许的日期差(天): 我方日 vs 500 销售日
                                    # (只认编号会串场: 同一编号在相邻期号里是另一场,
                                    #  例 编号47 本期=史泰比亞/切塞納, 上一期=AB格萊薩克瑟/桑德捷斯基)


def _beidan_no_num(m):
    """我方 beidan_no -> 期号内场次编号 (容忍 '17' / '#北单17' / '北单17' 等写法)"""
    mm = re.search(r'(\d+)', str(m.get('beidan_no') or ''))
    return (mm.group(1).lstrip('0') or '0') if mm else ''


def _period_of(m):
    """我方 match 已核定的北单期号(由上次命中行带出); 从未核定 = ''"""
    return str(m.get('beidan_period') or '').strip()


def _day_diff(d1, d2):
    """两个日期字符串相差天数; 无法解析返回 None"""
    try:
        return (datetime.strptime((d2 or '')[:10], '%Y-%m-%d') -
                datetime.strptime((d1 or '')[:10], '%Y-%m-%d')).days
    except ValueError:
        return None


def join_rows(rows, matches):
    """500.com 行 -> 我方 match; 一对一择优

    ① 期号 + 场次编号精确配对(主键): 一期内编号唯一 —— 编号相同 + 期号相同 = 同一场。
       行自带期号(26096/26095); 我方期号只认已核定的 beidan_period(上次命中行带出),
       绝不按日期推断 —— 一期横跨多个比赛日, 编号又跨期重复
       (实测 编77: 26096=忠南牙山/天安城, 26095=瓦埃勒/布隆德比; 我方 09-18 编77 是后者),
       按日期推期号会把 09-18 的欧洲场配到 09-19 的日韩场上。
       三重校验: 期号(两侧都有则须相同) + 日期 ≤ NUM_DAYS 天 + 队名相似度和 ≥ NUM_NAME_MIN。
    ② 编号对不上/缺失时, 退回 (双队名相似度 ≥ JOIN_MIN, 日期 ±JOIN_DAYS 天) 模糊匹配。
    """
    by_day, by_num = {}, {}
    for m in matches:
        by_day.setdefault((m.get('date') or '')[:10], []).append(m)
        n = _beidan_no_num(m)
        if n:
            by_num.setdefault(n, []).append(m)
    cand = {}
    for fid, r in rows.items():
        d = (r.get('dt') or r.get('date') or '')[:10]
        n = str(r.get('num') or '').strip().lstrip('0')
        rp = str(r.get('period') or '').strip()
        ns_pred = lambda m: (_sim(r.get('h') or r.get('home'), m.get('home_team')) +
                             _sim(r.get('a') or r.get('away'), m.get('away_team')))
        if n:
            for m in by_num.get(n, []):
                dd = _day_diff(d, m.get('date'))
                ns = ns_pred(m)
                mp = _period_of(m)
                if mp and rp and mp != rp:              # 期号不符 = 别期的同号场(编77 双胞胎)
                    continue
                if dd is None or abs(dd) > NUM_DAYS:    # 期号+编号之外还要日期对得上
                    continue
                if ns < NUM_NAME_MIN:                   # 再加队名门槛(挡跨期串场/译名对不上)
                    continue
                sc = NUM_SCORE + min(ns, 2.0) * 0.01 - abs(dd) * 0.002
                key = (fid, id(m))
                if key not in cand or sc > cand[key][0]:
                    cand[key] = (sc, m)
        pool = []
        for k in range(-JOIN_DAYS, JOIN_DAYS + 1):
            pool += by_day.get(_shift(d, k), [])
        for m in pool:
            sc = ns_pred(m)
            if sc >= JOIN_MIN and sc > cand.get((fid, id(m)), (0, None))[0]:
                cand[(fid, id(m))] = (sc, m)
    pairs, used_row, used_m = [], set(), set()
    for (fid, mid), (sc, m) in sorted(cand.items(), key=lambda kv: -kv[1][0]):
        if fid in used_row or mid in used_m:
            continue
        used_row.add(fid)
        used_m.add(mid)
        pairs.append((sc, fid, m, rows[fid]))
    return pairs


def write_into(path, dry=False):
    """把两玩法的让球/水位写进 results.json 的 ahbd_* 字段 (不碰 ah_*, 不参与规则)
    A 方案: ahbd_open_* <- 让球胜平负(bjdc) ; ahbd_cur_* <- 胜负过关(bjdcsf)
    另落 beidan_period(北单期号) —— 由命中的 500 行带出(期号+编号+队名三方一致), 不按日期推断。"""
    data = json.load(open(path, encoding='utf-8'))
    ms = data.get('matches') if isinstance(data, dict) else data
    pool = fetch_pool()
    rq, sf = merge_pool(pool)
    jr = {id(m): r for _, _, m, r in join_rows(rq, ms)}
    js = {id(m): r for _, _, m, r in join_rows(sf, ms)}
    n_open = n_cur = n_per = 0
    n_carry = 0
    for m in ms:
        r = jr.get(id(m)) or js.get(id(m)) or {}
        p = str(r.get('period') or '').strip()
        prev_p = str(m.get('beidan_period') or '').strip()   # 上一轮已核定的期号(可能为空)
        if p:
            m['beidan_period'] = p
            n_per += 1
        f = build_fields(jr.get(id(m)), js.get(id(m)))
        old = {k: m.get(k) for k in AHD_KEYS}
        for k in AHD_KEYS:
            m.pop(k, None)
        if f:
            m.update(f)
        # 赛后才跑(500 把「已结束」行的 SP 撤掉, 抓不到)时保留赛前抓到的值:
        # 同一场(期号一致, 或本来就没有期号)不该因为跑得晚就把让/过整行抹掉
        if not p or not prev_p or prev_p == p:
            for k, v in old.items():
                if k not in m and v is not None:
                    m[k] = v
                    n_carry += 1
        n_open += 1 if m.get('ahbd_open_home') is not None else 0
        n_cur += 1 if m.get('ahbd_cur_home') is not None else 0
    if not dry:
        json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    per = sorted({str(r.get('period')) for r in list(rq.values()) + list(sf.values()) if r.get('period')})
    print(f'500.com: 让球胜平负 {len(rq)} 行 / 胜负过关 {len(sf)} 行 | 配对到我方 {len(jr)} / {len(js)}')
    print(f'{"[dry] " if dry else ""}写入: 初盘行(让球胜平负) {n_open} 场 | 即时盘行(胜负过关) {n_cur} 场')
    print(f'期号: {"、".join(per) or "—"} | 带期号场次 {n_per}')
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
    rq, sf = merge_pool(fetch_pool())
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
