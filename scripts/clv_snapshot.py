#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临场关盘价快照 (CLV 前提)

只做一件事: 对即将开赛的场次 (默认 开赛前 20~90 分钟) 再抓一次
HKJC / Pinnacle 1X2 赔率, 追加到 docs/data/clv_close.json。
不读模型、不写 results.json、不影响推荐/账本。

用法: python3 clv_snapshot.py [--window 20,90] [--dry]
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from titan007_utils import fetch_1x2d_odds  # noqa: E402

CID_PINNACLE = '177'
CID_HKJC = '432'
OUT = os.path.join(ROOT, 'docs', 'data', 'clv_close.json')
KEEP_DAYS = 60


def load_fixtures():
    """从本地产物读未来场次 (fid, kickoff, teams)"""
    seen = {}
    for f in sorted(glob.glob(os.path.join(ROOT, 'data', 'matches_2026*.json')))[-7:]:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        for m in (d['matches'] if isinstance(d, dict) else d):
            fid = str(m.get('fid') or '')
            mt = m.get('match_time') or ''
            if not fid or not mt:
                continue
            try:
                ko = datetime.strptime(mt.strip()[:16], '%Y-%m-%d %H:%M')
            except ValueError:
                continue
            seen[fid] = dict(fid=fid, kickoff=mt.strip()[:16], ko=ko,
                             home=m.get('home_team', ''), away=m.get('away_team', ''),
                             event=m.get('event', ''), saved_hkjc=(m.get('odds_hkjc_win'),
                                                                     m.get('odds_hkjc_draw'),
                                                                     m.get('odds_hkjc_loss')))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--window', default='20,90', help='距开赛分钟窗口 lo,hi')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    lo, hi = [int(x) for x in a.window.split(',')]
    now = datetime.now()
    fx = load_fixtures()
    due = [x for x in fx.values() if lo <= (x['ko'] - now).total_seconds() / 60 <= hi]
    print(f'[CLV] 未来场次 {len(fx)} | 窗口内待抓 {len(due)} | now={now:%Y-%m-%d %H:%M}')
    if not due:
        print('[CLV] 无可抓场次, 退出')
        return 0

    db = {}
    if os.path.exists(OUT):
        try:
            db = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            db = {}

    ok = fail = 0
    for x in sorted(due, key=lambda y: y['ko']):
        try:
            odds, _mt = fetch_1x2d_odds(x['fid'])
        except Exception as e:
            print(f"  ! {x['fid']} {x['home']}vs{x['away']} 抓取异常: {e}")
            fail += 1
            continue
        if not odds:
            print(f"  - {x['fid']} {x['home']}vs{x['away']} 无赔率")
            fail += 1
            continue
        h = odds.get(CID_HKJC) or {}
        p = odds.get(CID_PINNACLE) or {}

        def trip(src):
            """1x2d 结构: {name,init_w/d/l,init_rr,curr_w/d/l,curr_rr}"""
            if not src:
                return [None, None, None]
            out = []
            for k in ('curr_w', 'curr_d', 'curr_l'):
                try:
                    out.append(float(src.get(k)) or None)
                except (TypeError, ValueError):
                    out.append(None)
            return out

        def trip_open(src):
            if not src:
                return [None, None, None]
            out = []
            for k in ('init_w', 'init_d', 'init_l'):
                try:
                    out.append(float(src.get(k)) or None)
                except (TypeError, ValueError):
                    out.append(None)
            return out

        rec = dict(fid=x['fid'], kickoff=x['kickoff'], teams=f"{x['home']} vs {x['away']}",
                   event=x['event'], snapshot_at=now.strftime('%Y-%m-%d %H:%M'),
                   minutes_to_ko=round((x['ko'] - now).total_seconds() / 60),
                   hkjc_close=trip(h), pin_close=trip(p),
                   hkjc_open=trip_open(h), pin_open=trip_open(p),
                   hkjc_saved=list(x['saved_hkjc']), raw_hkjc=h or None, raw_pin=p or None)
        if not any(rec['hkjc_close']) and not any(rec['pin_close']):
            print(f"  - {x['fid']} 快照无有效赔率")
            fail += 1
            continue
        db.setdefault(x['fid'], []).append(rec)
        ok += 1
        print(f"  + {x['fid']} {x['home']}vs{x['away']} 开赛{x['kickoff']} "
              f"HKJC {rec['hkjc_close']} Pin {rec['pin_close']}")

    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    for fid in list(db):
        db[fid] = [r for r in db[fid] if r.get('kickoff', '') >= cutoff]
        if not db[fid]:
            del db[fid]

    print(f'[CLV] 成功 {ok} 失败 {fail} | 累计场次 {len(db)}')
    if a.dry:
        print('[CLV] --dry, 不写盘')
        return 0
    tmp = OUT + '.tmp'
    json.dump(db, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f'[CLV] 已写 {OUT} ({os.path.getsize(OUT)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
