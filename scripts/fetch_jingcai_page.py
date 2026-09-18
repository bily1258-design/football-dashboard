#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 titan007 竞彩足球销售页 (cp.titan007.com/buy/JingCai.aspx)

用途: 拿到「当前在售」的全部竞彩场次及其编号(周X###)、matchid、sid、
开赛/截止时间、让球、胜平负赔率 —— 覆盖多个销售日(通常 周四~周日),
而 bfdata_ut.js 实时流只含当日窗口, 因此该页是竞彩编号/未来赛程的补充源。

输出: docs/data/jingcai_page.json
    {updated, source, count, by_day:{周X:n}, matches:[{no, day, seq, matchid,
     sid, league, kickoff, deadline, home, away, handicap, cansale, spf:[h,d,a]}]}

用法: python3 scripts/fetch_jingcai_page.py [--date YYYY-M-D]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ('Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36')
URL = 'https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2'


def fetch_html(date=None):
    url = URL + (f'&date={date}' if date else '')
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://cp.titan007.com/',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode('utf-8', errors='replace')


def strip(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t)).strip()


def parse(html):
    out = []
    for m in re.finditer(r'<tr id="row_(\d+)"(.*?)</tr>', html, re.DOTALL):
        matchid, body = m.group(1), m.group(2)
        day = re.search(r'name="(周[一二三四五六日])"', body)
        league = re.search(r'gamename="([^"]*)"', body)
        hcap = re.search(r'polygoal="([^"]+)"', body)
        cansale = re.search(r'cansale="([^"]+)"', body)
        seq = re.search(r'>(\d{3})</td>', body)
        kick = re.search(r'开赛时间：([\d\-: ]+)', body)
        dead = re.search(r'截止时间：([\d\-: ]+)', body)
        home = re.search(r'id="HomeTeam_\d+">([^<]+)</a>', body)
        away = re.search(r'id="GuestTeam_\d+">([^<]+)</a>', body)
        sid = re.search(r'panlu/(\d+)\.htm', body)
        # 胜平负赔率: 三个 class="op" 单元格, 或内嵌 spfItem 表
        spf = []
        for td in re.findall(r'<td class="op"[^>]*>(.*?)</td>', body, re.DOTALL):
            v = strip(td)
            spf.append(v)
        if not any(spf):
            spf = [strip(x) for x in re.findall(r"<td[^>]*class='spf[^']*'[^>]*>(.*?)</td>",
                                               body, re.DOTALL)][:3]
        rec = {
            'no': (day.group(1) if day else '') + (seq.group(1) if seq else ''),
            'day': day.group(1) if day else '',
            'seq': seq.group(1) if seq else '',
            'matchid': matchid,
            'sid': sid.group(1) if sid else '',
            'league': league.group(1) if league else '',
            'kickoff': (kick.group(1) if kick else '').strip(),
            'deadline': (dead.group(1) if dead else '').strip(),
            'home': home.group(1).strip() if home else '',
            'away': away.group(1).strip() if away else '',
            'handicap': hcap.group(1) if hcap else '',
            'cansale': (cansale.group(1) == 'true') if cansale else False,
            'spf': spf,
        }
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='页面日期, 如 2026-9-18 (缺省=今天)')
    ap.add_argument('--out', default=os.path.join(BASE, 'docs/data/jingcai_page.json'))
    a = ap.parse_args()

    html = fetch_html(a.date)
    matches = parse(html)
    if len(html) < 20000 or not matches:
        print(f'[ERROR] 竞彩页解析为空 (html={len(html)} bytes, rows={len(matches)})')
        return 1

    by_day = {}
    for r in matches:
        by_day[r['day']] = by_day.get(r['day'], 0) + 1
    data = {
        'updated': datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2',
        'count': len(matches),
        'by_day': by_day,
        'matches': matches,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # 覆盖对照: 与 results.json 的 jingcai_no 比
    cov_missing = None
    rj = os.path.join(BASE, 'docs/data/results.json')
    if os.path.exists(rj):
        try:
            with open(rj, encoding='utf-8') as f:
                res = json.load(f)
            have = {str(m.get('jingcai_no', '')) for m in res.get('matches', [])}
            miss = [r['no'] for r in matches if r['no'] not in have]
            cov_missing = miss
        except Exception as e:
            print(f'[WARN] results.json 对照失败: {e}')

    print(f'[OK] 竞彩在售 {len(matches)} 场 -> {a.out}')
    print('     按销售日: ' + ', '.join(f'{k}{v}' for k, v in sorted(by_day.items())))
    if cov_missing is not None:
        print(f'     库内缺编号: {len(cov_missing)} 场 -> {" ".join(cov_missing[:40])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
