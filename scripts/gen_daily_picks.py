#!/usr/bin/env python3
"""心水推荐生成器 — 一条命令出全部推荐
用法: python3 gen_daily_picks.py [--start '2026-08-01 14:15'] [--end '2026-08-02 12:00'] [--min-odds 1.8]
信号逻辑(2026-08-01 复盘定案):
  三向同向(欧赔/泊松/LGBM) +3 | 亚盘赢盘同向 +2 | prediction同向 +1
  含平 -2 | 深盘|盘口|>=2 -3
  首选 = 分>=6 且 方向赔率>=2.0 | 高价值 = 分>=5 或 EV价值 | 避雷 = 深盘低赔
"""
import json, sys, datetime, argparse

def load_matches():
    res = json.load(open('/data/data/com.termux/files/home/football-dashboard/docs/data/results.json'))
    return res.get('matches', res if isinstance(res, list) else [])

def parse_t(s):
    return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    ap.add_argument('--end', default=None)
    ap.add_argument('--min-odds', type=float, default=1.8)
    args = ap.parse_args()

    now = datetime.datetime.now()
    start = parse_t(args.start)
    end = parse_t(args.end) if args.end else (now + datetime.timedelta(hours=24)).replace(hour=12, minute=0)

    ms = load_matches()
    win, skipped = [], 0
    for r in ms:
        try:
            d = parse_t(r['match_time'][:16])
        except Exception:
            continue
        if start <= d <= end:
            win.append(r)
        elif d < start and r.get('score'):
            skipped += 1

    print(f"窗口: {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} | 未开场 {len(win)} 场 (已开赛跳过 {skipped})\n")

    rated = []
    for r in win:
        try:
            ow, od, ol = float(r['odds_win']), float(r['odds_draw']), float(r['odds_loss'])
            ahf = float(r['ah_handicap'])
            ac = float(r.get('ah_away_covers_prob', 0) or 0)
            hc = float(r.get('ah_home_covers_prob', 0) or 0)
            mw, md, ml = float(r['model_win']), float(r['model_draw']), float(r['model_loss'])
            lw, ld, ll = float(r['lgbm_win']), float(r['lgbm_draw']), float(r['lgbm_loss'])
            pred = r.get('prediction_cn', '')
        except Exception:
            continue
        if ow <= 1 or od <= 1 or ol <= 1:
            continue
        tot = 1/ow + 1/od + 1/ol
        ew, ed, el = (1/ow)/tot, (1/od)/tot, (1/ol)/tot
        e_dir = ['home','draw','away'][max(range(3), key=lambda i: [ew,ed,el][i])]
        m_dir = ['home','draw','away'][max(range(3), key=lambda i: [mw,md,ml][i])]
        l_dir = ['home','draw','away'][max(range(3), key=lambda i: [lw,ld,ll][i])]
        a_dir = 'away' if ac > hc else 'home'

        score = 0; tags = []
        if e_dir == m_dir == l_dir and e_dir != 'draw':
            score += 3; tags.append(f"三向{e_dir}")
        if a_dir == e_dir and e_dir != 'draw':
            score += 2; tags.append(f"亚P{max(ac,hc):.0%}")
        if pred in ('主胜','客胜') and pred == {'home':'主胜','away':'客胜'}.get(e_dir):
            score += 1; tags.append("pred同向")
        if e_dir == 'draw' or m_dir == 'draw':
            score -= 2; tags.append("含平")
        if abs(ahf) >= 2:
            score -= 3; tags.append(f"深盘{ahf:g}")

        # 逆势信号: 欧赔分歧, 但模型+LGBM+亚盘同向 → 推荐模型方向 (EV来源)
        rev = False
        if e_dir != m_dir and m_dir == l_dir and m_dir != 'draw' and a_dir == m_dir:
            rev = True
            score += 2; tags.append(f"逆欧赔·模型/亚盘同向{m_dir}")

        dir_ = e_dir if not rev else m_dir
        odds = {'home': ow, 'away': ol}.get(dir_, 0)
        ev = 0
        for v in r.get('value_bets', []):
            if v.get('outcome') == dir_ and v.get('ev', 0) > ev:
                ev = v['ev']
        rated.append({
            'r': r, 'score': score, 'dir': dir_, 'odds': odds, 'tags': tags,
            'ev': ev, 'ac': ac, 'hc': hc, 'ahf': ahf, 'htxt': r.get('ah_handicap_text', ''),
            'ew': ew, 'ed': ed, 'el': el, 'mw': mw, 'md': ml,
        })

    rated.sort(key=lambda x: -x['score'])

    # 避雷: 深盘低赔(方向赔率<=1.5 且 |盘口|>=1.5)
    dodge = [x for x in rated if abs(x['ahf']) >= 1.5 and x['odds'] <= 1.5 and x['score'] > 0]
    print("⚠️ 避雷 (深盘低赔):")
    for x in dodge[:8]:
        r = x['r']
        print(f"  {r['match_time'][5:16]} {r['event']} {r['home_team']} vs {r['away_team']} | {x['htxt']} 方向赔率{x['odds']}")
    if not dodge:
        print("  (无)")

    # 首选: 分>=6 且 赔率>=2.0 且非平 (排除低赔热门)
    top = [x for x in rated if x['score'] >= 6 and x['odds'] >= 2.0 and x['dir'] != 'draw']
    top.sort(key=lambda x: -x['odds'])
    print(f"\n⭐⭐⭐ 首选 ({len(top)}):")
    for x in top:
        r = x['r']
        cn = {'home':'主胜','away':'客胜'}[x['dir']]
        print(f"  @{x['odds']} {r['match_time'][5:16]} {r['event']} {r['home_team']} vs {r['away_team']} | {x['htxt']} | {x['dir']}·{'/'.join(x['tags'])} 欧赔{x['ew']:.0%}/{x['ed']:.0%}/{x['el']:.0%}")

    # 高价值: 分>=5 或 EV>=8%, 排除已在首选的; 赔率>=2.0
    top_keys = {id(x['r']) for x in top}
    hi = [x for x in rated if id(x['r']) not in top_keys and (x['score'] >= 5 or x['ev'] >= 0.08) and x['odds'] >= 2.0 and x['dir'] != 'draw']
    hi.sort(key=lambda x: -x['ev'] if x['ev'] else -x['score'])
    print(f"\n⭐⭐ 高价值 ({len(hi)}):")
    for x in hi:
        r = x['r']
        cn = {'home':'主胜','away':'客胜'}[x['dir']]
        evs = f" EV+{x['ev']:.0%}" if x['ev'] >= 0.05 else ""
        print(f"  @{x['odds']} {r['match_time'][5:16]} {r['event']} {r['home_team']} vs {r['away_team']} | {x['htxt']} | {x['dir']}·{'/'.join(x['tags'])}{evs}")

    # 串关建议
    if top:
        combo = 1.0; names = []
        for x in top[:3]:
            combo *= x['odds']
            names.append(f"{x['r']['home_team'].replace('(中)','')[:4]}客" if x['dir']=='away' else f"{x['r']['home_team'].replace('(中)','')[:4]}主")
        print(f"\n💰 串关建议: {' + '.join(names)} ≈ {combo:.1f}倍")
    print()

if __name__ == '__main__':
    main()
