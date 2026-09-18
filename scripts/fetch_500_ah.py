#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""500.com 亚盘(亚指)抓取 —— 源: www.500.com 静态 XML (无 WAF)

数据面:
  1) https://www.500.com/static/public/jczq/xml/odds/odds.xml          (今日, 竞彩场次)
     https://www.500.com/static/public/jczq/xml/hisdata/YYYY/MMDD/odds.xml (历史, 会被裁剪)
  2) https://trade.500.com/jczq/   行属性 data-infomatchid / data-fixtureid / 编号 / 队名 / 时间

XML 结构:
  <match id="165875" processdate="2026-09-18" processname="5001">   # processname = 周几+编号(周五001 -> 5001)
    <europe avg=".." wl=".." am=".." lb=".." bet365=".." hg=".."/>
    <asian  am="0.980,一球,0.800" lb="" bet365="0.950,一球,0.850" hg="1.010,一球,0.810"/>
  <match> 的 id == trade.500.com/jczq/ 行的 data-infomatchid

用法:
  python scripts/fetch_500_ah.py                # 今日, 只报数(不落盘)
  python scripts/fetch_500_ah.py --date 2026-09-17
  python scripts/fetch_500_ah.py --out docs/data/ah_500.json
"""
import argparse, json, os, re, sys, urllib.request, gzip, io
from datetime import datetime

UA = ('Mozilla/5.0 (Linux; Android 13; Mi 11 Lite) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CN_DOW = {'1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日'}


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Referer': 'https://trade.500.com/bjdcsf/',
        'Accept-Language': 'zh-CN,zh;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    for enc in ('utf-8', 'gbk'):
        if enc == 'gbk':
            return raw.decode('gbk', errors='replace')
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def fetch_odds_xml(date=None):
    """返回 {infomatchid: {code, date, asian:{hg,am,lb,bet365}, europe:{hg,avg}}}"""
    if date:
        y, m, d = date.split('-')
        url = f'https://www.500.com/static/public/jczq/xml/hisdata/{y}/{m}{d}/odds.xml'
    else:
        url = 'https://www.500.com/static/public/jczq/xml/odds/odds.xml'
    txt = get(url)
    out = {}
    for blk in re.findall(r'<match\b[^>]*>.*?</match>', txt, re.S):
        mid = re.search(r'id="(\d+)"', blk)
        pname = re.search(r'processname="(\d+)"', blk)
        pdate = re.search(r'processdate="([\d-]+)"', blk)
        if not (mid and pname):
            continue
        p = pname.group(1)
        code = CN_DOW.get(p[0], '?') + p[1:].zfill(3)   # 5001 -> 周五001
        def attrs(tag):
            m = re.search(rf'<{tag} ([^>]*?)/>', blk)
            return dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))) if m else {}
        out[mid.group(1)] = {'code': code, 'date': pdate.group(1) if pdate else '',
                             'asian': attrs('asian'), 'europe': attrs('europe')}
    return out


def parse_asian(v):
    """'0.980,一球,0.800' -> (1.0 系水位: home_water, handicap_text, away_water)"""
    if not v:
        return None
    p = v.split(',')
    if len(p) != 3:
        return None
    return {'home_water': p[0], 'handicap_text': p[1], 'away_water': p[2]}


def fetch_jczq_page():
    """竞彩列表: 返回 [{code, info_id, fid, home, away, date, time, league}]"""
    txt = get('https://trade.500.com/jczq/')
    rows = []
    for tr in re.findall(r'<tr class="bet-tb-tr".*?</tr>', txt, re.S):
        a = dict(re.findall(r'data-([a-z]+)="([^"]*)"', tr))
        code_m = re.search(r'周[一二三四五六日]\d{3}', tr)
        rows.append({
            'code': code_m.group(0) if code_m else '',
            'info_id': a.get('infomatchid', ''), 'fid': a.get('fixtureid', ''),
            'home': a.get('homesxname', ''), 'away': a.get('awaysxname', ''),
            'date': a.get('matchdate', ''), 'time': a.get('matchtime', ''),
            'hcp': a.get('rangqiu', ''), 'league': a.get('simpleleague', ''),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='历史日期 YYYY-MM-DD; 省略=今日实时')
    ap.add_argument('--out', help='落盘路径(JSON)')
    ap.add_argument('--results', default=os.path.join(ROOT, 'docs/data/results.json'))
    a = ap.parse_args()

    xml = fetch_odds_xml(a.date)
    rows = fetch_jczq_page()
    print(f'500.com 亚盘 XML: {len(xml)} 场 | 竞彩列表: {len(rows)} 场')

    page = {r['info_id']: r for r in rows if r['info_id']}
    merged, no_asian = [], 0
    for mid, v in xml.items():
        r = page.get(mid, {})
        hg = parse_asian(v['asian'].get('hg', ''))
        am = parse_asian(v['asian'].get('am', ''))
        if not (hg or am):
            no_asian += 1
            continue
        merged.append({**v, 'fid': r.get('fid', ''), 'home': r.get('home', ''),
                       'away': r.get('away', ''), 'time': r.get('time', ''),
                       'league': r.get('league', ''), 'hcp': r.get('hcp', ''),
                       'hg': hg, 'am': am})
    print(f'含亚盘: {len(merged)} 场 (无亚盘 {no_asian})')
    for m in merged[:5]:
        print(f"  {m['code']} {m['home']} vs {m['away']} | 皇冠 {m['hg']['handicap_text']} "
              f"{m['hg']['home_water']}/{m['hg']['away_water']} | 澳门 "
              f"{(m['am'] or {}).get('handicap_text','-')}")

    # 与我方 results.json 对照
    try:
        rs = json.load(open(a.results, encoding='utf-8'))['matches']
    except Exception as e:
        print('results.json 读取失败:', e); rs = []
    by_code = {m['code']: m for m in merged}
    ours = [m for m in rs if m.get('jingcai_no')]
    hit_code = [m for m in ours if m['jingcai_no'] in by_code]
    print(f'\n我方带竞彩编号: {len(ours)} 场 | 编号命中 500.com 亚盘: {len(hit_code)} 场')
    for m in hit_code[:6]:
        x = by_code[m['jingcai_no']]
        print(f"  {m['jingcai_no']} {m.get('home_team')} vs {m.get('away_team')} "
              f"-> 皇冠 {x['hg']['handicap_text']} {x['hg']['home_water']}/{x['hg']['away_water']}")
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump({'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                   'source': 'www.500.com static odds.xml (asian: am/lb/bet365/hg)',
                   'count': len(merged), 'matches': merged},
                  open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已落盘:', a.out)


if __name__ == '__main__':
    main()
