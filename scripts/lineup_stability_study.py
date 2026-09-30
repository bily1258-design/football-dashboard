#!/usr/bin/env python3
"""只读研究：首发阵容稳定性 ↔ 胜率 对照（不参与选号、不改任何规则）。

口径:
  延续度 continuity = 本场首发 ∩ 上一场首发 / 11      (按球队, 按日期排序; 无上一场则不参与)
  稳定度 stab       = 该队(本场之前)最近 5 场 continuity 的均值; 不足 1 场则不计
  结果              = 该队本场 胜/平/负, 取自 docs/data/results.json 的 score
  阵型延续 same_form = 本场阵型串 == 上一场阵型串

用法:
  python3 scripts/lineup_stability_study.py            # 全量
  python3 scripts/lineup_stability_study.py --min-n 10 # 只打印样本>=10 的档
备注: 阵容数据来自 match_formations/match_lineups（赛前页面抓取），比分来自 results.json。
"""
import os, sys, json, sqlite3, argparse, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'football.db')
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')


def load_scores():
    """fid(str) -> (home_goals, away_goals)"""
    d = json.load(open(RESULTS, encoding='utf-8'))
    ms = d if isinstance(d, list) else (d.get('matches') or d.get('results') or [])
    out = {}
    for m in ms:
        sc = m.get('score')
        if not sc or not isinstance(sc, str) or '-' not in sc:
            continue
        try:
            h, a = [int(x.strip()) for x in sc.replace('–', '-').split('-')[:2]]
        except Exception:
            continue
        out[str(m.get('fid'))] = (h, a)
    return out


def load_lineups():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    fx = {}
    for r in con.execute("SELECT fid,date,home_team,away_team,home_formation,away_formation,"
                         "home_starters,away_starters FROM match_formations "
                         "WHERE has_lineup=1 AND home_starters>=11 AND away_starters>=11"):
        fx[str(r['fid'])] = dict(r)
    starters = collections.defaultdict(set)   # (fid, side) -> {player_id}
    for r in con.execute("SELECT fid,side,player_id FROM match_lineups WHERE is_starter=1"):
        starters[(str(r['fid']), r['side'])].add(r['player_id'])
    con.close()
    return fx, starters


def team_matches(fx, starters):
    """team_id -> [(date, fid, side, formation, starters_set)] 按日期升序"""
    tm = collections.defaultdict(list)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tid = {}
    for r in con.execute("SELECT fid,side,team_id FROM match_lineups GROUP BY fid,side"):
        tid[(str(r['fid']), r['side'])] = r['team_id']
    con.close()
    for fid, f in fx.items():
        for side in ('home', 'away'):
            key = (fid, side)
            if key not in starters or not starters[key]:
                continue
            formation = f['home_formation'] if side == 'home' else f['away_formation']
            t = tid.get(key) or (f['home_team'] if side == 'home' else f['away_team'])
            tm[t].append((f['date'], fid, side, formation, starters[key]))
    for t in tm:
        tm[t].sort(key=lambda x: (x[0], x[1]))
    return tm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=0)
    a = ap.parse_args()

    fx, starters = load_lineups()
    scores = load_scores()
    tm = team_matches(fx, starters)

    rows = []          # (stab, same_form, n_prev, result, is_home, league?)
    no_prev = 0
    for t, ms in tm.items():
        conts = []
        prev_set = None
        prev_form = None
        for (date, fid, side, formation, sset) in ms:
            if prev_set is not None:
                inter = len(sset & prev_set)
                cont = inter / 11.0
                same_form = 1 if (prev_form and formation and formation == prev_form) else 0
                stab = sum(conts[-5:]) / len(conts[-5:]) if conts else None
                if stab is not None and fid in scores:
                    hg, ag = scores[fid]
                    gf, ga = (hg, ag) if side == 'home' else (ag, hg)
                    res = 'W' if gf > ga else ('D' if gf == ga else 'L')
                    rows.append((stab, same_form, len(conts[-5:]), res, 1 if side == 'home' else 0))
                conts.append(cont)
            else:
                no_prev += 1
            prev_set = sset
            prev_form = formation

    print(f"球队数 {len(tm)} | 有阵容场次 {len(fx)} | 有比分的阵容场次 {sum(1 for f in fx if f in scores)}")
    print(f"可用样本 {len(rows)} 行(球队-场) | 因缺上一场阵容丢弃 {no_prev} 行")
    if not rows:
        print("样本为空：等待回填。")
        return

    def stats(sel, label):
        n = len(sel)
        if not n:
            return None
        w = sum(1 for r in sel if r[3] == 'W')
        d = sum(1 for r in sel if r[3] == 'D')
        pts = (3 * w + d) / n
        return (label, n, w / n * 100, (w + d) / n * 100, pts)

    buckets = [('<0.60', lambda s: s < 0.60), ('0.60-0.80', lambda s: 0.60 <= s < 0.80),
               ('0.80-0.95', lambda s: 0.80 <= s < 0.95), ('>=0.95', lambda s: s >= 0.95)]
    print("\n== 稳定度(近5场首发延续率均值) vs 胜率 (全部) ==")
    print(f"{'档位':<10}{'样本':>6}{'胜率%':>8}{'不败%':>8}{'场均分':>8}")
    for name, f in buckets:
        sel = [r for r in rows if f(r[0])]
        s = stats(sel, name)
        if s and s[1] >= a.min_n:
            print(f"{s[0]:<10}{s[1]:>6}{s[2]:>8.1f}{s[3]:>8.1f}{s[4]:>8.2f}")

    print("\n== 同上, 仅主队 ==")
    for name, f in buckets:
        sel = [r for r in rows if f(r[0]) and r[4] == 1]
        s = stats(sel, name)
        if s and s[1] >= a.min_n:
            print(f"{s[0]:<10}{s[1]:>6}{s[2]:>8.1f}{s[3]:>8.1f}{s[4]:>8.2f}")

    print("\n== 同上, 仅客队 ==")
    for name, f in buckets:
        sel = [r for r in rows if f(r[0]) and r[4] == 0]
        s = stats(sel, name)
        if s and s[1] >= a.min_n:
            print(f"{s[0]:<10}{s[1]:>6}{s[2]:>8.1f}{s[3]:>8.1f}{s[4]:>8.2f}")

    print("\n== 阵型延续(本场阵型==上场阵型) vs 胜率 ==")
    for name, f in [('同阵型', lambda x: x == 1), ('改阵型', lambda x: x == 0)]:
        sel = [r for r in rows if f(r[1])]
        s = stats(sel, name)
        if s and s[1] >= a.min_n:
            print(f"{s[0]:<10}{s[1]:>6}{s[2]:>8.1f}{s[3]:>8.1f}{s[4]:>8.2f}")

    print("\n== 延续度中位数 / 分布 ==")
    cs = sorted(r[0] for r in rows)
    print(f"n={len(cs)} 中位 {cs[len(cs)//2]:.3f} 10分位 {cs[len(cs)//10]:.3f} 90分位 {cs[len(cs)*9//10]:.3f}")


if __name__ == '__main__':
    main()
