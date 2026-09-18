#!/usr/bin/env python3
"""竞彩未来销售日补充抓取 —— 把周六/周日的竞彩场次补进 data/matches_*.json

背景（2026-09-18 定位）:
    竞彩在售页 cp.titan007.com/buy/JingCai.aspx 一次列出 ~3 个销售日（周五/周六/周日 …），
    但主抓取源 livestatic.titan007.com/vbsxml/bfdata_ut.js 是**实时赛程流，只含当日窗口**，
    未来一/两天的场次在源里根本不存在（实测 546 行仅 15 行带竞彩编号 f[57]，
    热刺vs维拉、乌迪内斯、法兰克福、波尔图等一场都没有）——不是映射漏，是源不给。
    所以脚本 docs/data/jingcai_onsale.json（fetch_jingcai_page.py 抓取）是本补充源的赛程口径，
    赔率仍走 1x2d.titan007.com/{sid}.js（复用 fetch_zqdc.fetch_odds，平博 cid=177 / HKJC cid=432）。

产出:
    data/matches_jingcai_YYYYMMDD.json  （每场按真实开赛日归档，source='jingcai'）
    → 该文件名匹配 ai_analysis.load_raw_matches() 的 glob('matches_*.json')，会被自动并入分析；
      刻意避开 fetch_zqdc 的 data/matches_YYYYMMDD.json 命名，防止次日定时任务覆盖。

用法:
    python3 scripts/fetch_jingcai_future.py                # 默认：抓 kickoff >= 明天 的场次
    python3 scripts/fetch_jingcai_future.py --from-date 2026-09-19
    python3 scripts/fetch_jingcai_future.py --dry          # 只列不抓
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_zqdc import fetch_odds, _s2t  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONSALE = os.path.join(REPO, 'docs', 'data', 'jingcai_onsale.json')
DATA_DIR = os.path.join(REPO, 'data')


def load_onsale():
    with open(ONSALE, 'r', encoding='utf-8') as f:
        return json.load(f)


def build(m, today=None):
    """把 onsale 条目转成 fetch_odds 需要的抓取入参"""
    kickoff = (m.get('kickoff') or '').strip()
    date = kickoff[:10] if len(kickoff) >= 10 else ''
    return {
        'sid': m['sid'],
        'league': m.get('league', ''),
        'home_team': _s2t.convert(m.get('home', '') or ''),
        'away_team': _s2t.convert(m.get('away', '') or ''),
        'display_time': kickoff,
        'match_time': kickoff,
        'date': date,
        'score': '',
        'source': 'jingcai',
        'beidan_no': '',
        'jingcai_no': m.get('no', ''),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-date', default='', help='只抓开赛日>=该日期的场次(默认明天)')
    ap.add_argument('--dry', action='store_true', help='只列场次，不抓赔率')
    ap.add_argument('--delay', type=float, default=0.3)
    ap.add_argument('--workers', type=int, default=3)
    args = ap.parse_args()

    from_date = args.from_date or (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    d = load_onsale()
    # from_date 允许带时间（如 '2026-09-19 10:00'），便于把当日凌晨场排除在外
    sel = [m for m in d.get('matches', [])
           if m.get('sid') and (m.get('kickoff') or '') >= from_date]
    sel.sort(key=lambda m: m['kickoff'])
    print(f'在售页共 {len(d.get("matches", []))} 场，开赛 >= {from_date} 的 {len(sel)} 场')

    if args.dry:
        for m in sel:
            print('  ', m['no'], m['kickoff'], m['league'], f"{m['home']} vs {m['away']}", m['sid'])
        return

    inputs = [build(m) for m in sel]
    enriched = fetch_odds(inputs, delay=args.delay, workers=args.workers)

    # 按真实开赛日归档（跨日的凌晨场归到开赛当天）
    by_date = {}
    for r in enriched:
        by_date.setdefault(r.get('date') or 'unknown', []).append(r)

    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for date, rows in sorted(by_date.items()):
        rows.sort(key=lambda x: x.get('match_time', ''))
        if date == 'unknown':
            continue
        fp = os.path.join(DATA_DIR, f'matches_jingcai_{date.replace("-", "")}.json')
        payload = {'updated': stamp, 'source': 'jingcai_onsale', 'count': len(rows), 'matches': rows}
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        n_pin = sum(1 for r in rows if r.get('odds_pinnacle_win'))
        n_hk = sum(1 for r in rows if r.get('odds_hkjc_win'))
        print(f'  ✓ {date}: {len(rows)} 场 → {os.path.relpath(fp, REPO)} (平博 {n_pin} / HKJC {n_hk})')


if __name__ == '__main__':
    main()
