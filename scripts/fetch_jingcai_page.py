#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 titan007 竞彩足球销售页 + 各场欧赔(竞彩官方/平博/HKJC)。

背景: bfdata_ut.js 是实时赛程流, 只含"当日窗口", 竞彩编号字段 f[57] 仅对当日场次非空。
      竞彩销售页 cp.titan007.com/buy/JingCai.aspx 覆盖多个销售日(通常 周四~周日),
      实测 2026-09-18 = 61 场(周三1/周四9/周五14/周六26/周日11)。
      该页 spf 单元格走 ajax 为空, 赔率改从 1x2d.titan007.com/{sid}.js 取。

产物: docs/data/jingcai_onsale.json
  {updated, count, by_day:{周五:14,...}, matches:[{no,day,seq,league,kickoff,deadline,
    home,away,sid,matchid,handicap,cansale,odds_official:[w,d,l],odds_pinnacle,odds_hkjc,
    odds_avg}]}

用法: python3 scripts/fetch_jingcai_page.py [--no-odds]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PAGE = 'https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2'
UA = ('Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'data', 'jingcai_onsale.json')


def _get(url, referer='https://cp.titan007.com/buy/JingCai.aspx', timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': referer})
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch_page():
    return _get(PAGE).decode('utf-8', errors='replace')


def parse_page(html):
    out = []
    for m in re.finditer(r'<tr id="row_(\d+)"(.*?)</tr>', html, re.DOTALL):
        attrs, body = m.group(1), m.group(2)
        day = (re.search(r'name="(周[一二三四五六日])"', body) or re.search(r'name="(周[一二三四五六日])"', m.group(0)))
        day = day.group(1) if day else ''
        game = re.search(r'gamename="([^"]*)"', m.group(0))
        poly = re.search(r'polygoal="([^"]*)"', m.group(0))
        can = re.search(r'cansale="([^"]*)"', m.group(0))
        seq = re.search(r'<td[^>]*>(\d{3})</td>', body)
        home = re.search(r'id="HomeTeam_(\d+)"[^>]*>([^<]*)<', body)
        away = re.search(r'id="GuestTeam_(\d+)"[^>]*>([^<]*)<', body)
        kick = re.search(r'title="开赛时间：([^"]*)"', body)
        if not kick:
            kick = re.search(r'开赛时间[^0-9]*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', body)
        dl = re.findall(r'截止时间[^0-9]*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', body)
        sid = home.group(1) if home else (away.group(1) if away else '')
        if not sid:
            continue
        out.append({
            'no': (day + seq.group(1)) if (day and seq) else sid,
            'day': day,
            'seq': seq.group(1) if seq else '',
            'matchid': attrs,
            'sid': sid,
            'league': game.group(1) if game else '',
            'kickoff': (kick.group(1).strip() if kick else ''),
            'deadline': dl[0] if dl else '',
            'home': (home.group(2).strip() if home else ''),
            'away': (away.group(2).strip() if away else ''),
            'handicap': poly.group(1) if poly else '',
            'cansale': (can.group(1) == 'true') if can else False,
            'odds_official': None, 'odds_pinnacle': None,
            'odds_hkjc': None, 'odds_avg': None,
        })
    out.sort(key=lambda r: (r['day'], r['seq']))
    return out


# 1x2d.titan007.com/{sid}.js: game=Array("公司id|赔率id|公司名|初盘W|D|L|...|即时W|D|L|...|时间|简称|...")
_COMPANY = {'official': ('lottery', '竞彩官'), 'pinnacle': ('pinnacle', '平博'),
            'hkjc': ('jockey', '马会')}


def _odds_entry(sid):
    try:
        txt = _get('https://1x2d.titan007.com/%s.js' % sid,
                   referer='https://op1.titan007.com/oddslist/%s.htm' % sid).decode('utf-8', errors='replace')
    except Exception:
        return sid, {}
    got = {}
    ent = re.findall(r'"([^"]*\|[^"]*\|[^"]*)"', txt)
    for e in ent:
        p = e.split('|')
        if len(p) < 13:
            continue
        name = (p[2] + '|' + (p[21] if len(p) > 21 else '')).lower()
        try:
            val = [float(p[10]), float(p[11]), float(p[12])]  # 即时盘 W/D/L
        except (ValueError, IndexError):
            continue
        for key, (en, cn) in _COMPANY.items():
            if en in name and key not in got:
                got[key] = val
    return sid, got


def attach_odds(matches):
    sids = [m['sid'] for m in matches if m['sid']]
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sid, got in ex.map(_odds_entry, sids):
            for m in matches:
                if m['sid'] == sid:
                    for k, v in got.items():
                        m['odds_' + k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-odds', action='store_true', help='只抓编号/赛程, 不抓欧赔')
    args = ap.parse_args()

    html = fetch_page()
    matches = parse_page(html)
    if not args.no_odds:
        attach_odds(matches)

    by_day = {}
    for m in matches:
        by_day[m['day']] = by_day.get(m['day'], 0) + 1
    data = {
        'updated': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': PAGE,
        'count': len(matches),
        'by_day': by_day,
        'matches': matches,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    live = [m for m in matches if m.get('cansale')]
    with_odds = [m for m in matches if m.get('odds_official')]
    print('竞彩在售页: %d 场 %s' % (len(matches), by_day))
    print('其中 cansale=%d, 拿到竞彩官方赔率=%d' % (len(live), len(with_odds)))
    for m in matches[:3]:
        print('  ', m['no'], m['kickoff'], m['home'], 'vs', m['away'], '官方', m['odds_official'], '平博', m['odds_pinnacle'], 'HKJC', m['odds_hkjc'])
    print('->', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
