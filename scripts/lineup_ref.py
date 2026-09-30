#!/usr/bin/env python3
"""清单「📋战术阵容参考」段: 只读展示, 不参与任何选号规则。

数据来源(全部读库, 不发请求):
  match_formations / match_lineups  ← scripts/fetch_lineups.py 回填的 titan007 详情页数据
  xg_features                       ← 近3/10场 射门/控球/角球/xG (已有)
  match_analysis.h2h                ← 历史交手 (已有)

口径说明:
  ① 阵容=抓取时刻页面上的预计/官方首发, U21 及低级别联赛常缺;
  ② 「阵容延续」= 该队本场首发与上一场首发的重合人数(11人制, 1..11), 用来量化「可预判性」;
  ③ 「近5场稳定度」= 相邻两场首发重合人数/11 的均值(越高说明轮换/伤停越少);
  ④ 全部只展示, 不改 ①②③ 段任何标记, 不影响 LGBM/泊松/EV/避雷。
"""
import os
import re
import json
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'football.db')
PREFIX = '▫️'          # 行首标记: 保证清单解析器(LINE_RE 要求行首是 MM-DD HH:MM)不会误认成本场投注行


def _s2t():
    try:
        from opencc import OpenCC
        c = OpenCC('t2s')
        return c.convert
    except Exception:
        return lambda s: s


_t2s = _s2t()


def _norm(x):
    return re.sub(r'[\s\-·。．.]', '', (x or '')).lower()


def collect(fids, max_rows=40):
    """返回 list[str] (已含段头)。fids: 需要展示的场次 fid 列表(去重后按传入顺序)。"""
    fids = [f for f in dict.fromkeys(fids) if f]
    if not fids:
        return []
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    q = ','.join('?' * len(fids))
    fm = {r['fid']: dict(r) for r in con.execute(
        f"SELECT * FROM match_formations WHERE fid IN ({q})", fids)}
    if not fm:
        con.close()
        return []
    lu = {}
    for r in con.execute(
            f"SELECT * FROM match_lineups WHERE fid IN ({q}) ORDER BY side,is_starter DESC,shirt", fids):
        _d = dict(r)
        _d['side'] = 'home' if _d['side'] == 'home' else 'away'  # 入库侧名 guest → 展示侧名 away
        lu.setdefault(r['fid'], []).append(_d)
    xg = {r['sid']: dict(r) for r in con.execute(
        f"SELECT * FROM xg_features WHERE sid IN ({q})", fids)}
    h2h = {}
    for r in con.execute(f"SELECT sid,h2h FROM match_analysis WHERE sid IN ({q})", fids):
        try:
            h2h[r['sid']] = json.loads(r['h2h']) if r['h2h'] else None
        except Exception:
            h2h[r['sid']] = None

    # 队名(抓取页为简体) → 历史场次索引: 供稳定度/延续计算
    hist = {}
    for r in con.execute("SELECT l.team AS team, m.fid AS fid, m.date AS date, l.side AS side,"
                         " l.player_id AS pid FROM match_lineups l JOIN match_formations m ON m.fid=l.fid"
                         " WHERE l.is_starter=1 ORDER BY m.date"):
        hist.setdefault(_norm(_t2s(r['team'])), []).append(
            (r['date'] or '', r['fid'], 'home' if r['side'] == 'home' else 'away', r['pid']))
    con.close()

    def xi(fid, side):
        return set(p['player_id'] for p in lu.get(fid, [])
                   if p['side'] == side and p['is_starter'] == 1)

    def team_series(team_cn):
        """该队全部有阵容的场次(按日期升序): [(date, fid, side, set(首发pid))]"""
        rows = hist.get(_norm(team_cn), [])
        by = {}
        for d, f, s, pid in rows:
            by.setdefault((d, f, s), set()).add(pid)
        return [(d, f, s, pids) for (d, f, s), pids in sorted(by.items())]

    def stability(team_cn, cur_fid=None, cur_side=None):
        """返回 (稳定度%, 延续人数, 上场日期) — 稳定度=近5次相邻重合/11 均值"""
        ser = team_series(team_cn)
        seq = [(d, f, s, p) for d, f, s, p in ser if f != cur_fid]
        if cur_fid in [f for _, f, _, _ in ser] or cur_side is None:
            pass
        cur = xi(cur_fid, cur_side) if (cur_fid and cur_side) else set()
        prev = seq[-1] if seq else None
        cont = len(cur & prev[3]) if (cur and prev) else None
        pairs = []
        win = seq[-6:] if len(seq) >= 6 else seq
        for a, b in zip(win, win[1:]):
            u = len(a[3] | b[3])
            pairs.append(len(a[3] & b[3]) / u * 11 if u else 0)
        st = sum(pairs[-5:]) / len(pairs[-5:]) if pairs else None
        return st, cont, (prev[0] if prev else None)

    def fmt_team(key, side):
        r = xg.get(key)
        if not r:
            return None
        def g(*k):
            v = [r.get(x) for x in k]
            return v
        s3, c3 = g('home_shots_3', 'away_shots_3') if side == 'home' else g('away_shots_3', 'home_shots_3')
        p3, cp3 = g('home_possession_3', 'away_possession_3') if side == 'home' else g('away_possession_3', 'home_possession_3')
        co3, cco3 = g('home_corners_3', 'away_corners_3') if side == 'home' else g('away_corners_3', 'home_corners_3')
        x3, cx3 = g('xg_home_3', 'xg_away_3') if side == 'home' else g('xg_away_3', 'xg_home_3')
        x10, cx10 = g('xg_home_10', 'xg_away_10') if side == 'home' else g('xg_away_10', 'xg_home_10')
        def n(v):
            return '—' if v is None else (f'{v:.1f}' if isinstance(v, float) else str(v))
        def seg(label, a, b, suf=''):
            if a is None and b is None:
                return None
            return f"{label} {n(a)}{suf}/{n(b)}{suf}"
        parts = [x for x in (seg('射门', s3, c3), seg('控球', p3, cp3, '%'),
                             seg('角球', co3, cco3), seg('xG', x3, cx3)) if x]
        tail = (f" | 近10场 xG {n(x10)}/{n(cx10)}"
                if (x10 is not None or cx10 is not None) else '')
        if not parts:
            return None
        return "近3场 " + " · ".join(parts) + tail

    out, shown = [], 0
    body = []
    _fids_int = []
    for _f in fids:
        try:
            _fids_int.append(int(_f))
        except (TypeError, ValueError):
            continue
    fids = _fids_int
    for fid in fids:
        if shown >= max_rows:
            break
        f = fm.get(fid)
        if not f:
            continue
        home, away = f['home_team'] or '', f['away_team'] or ''
        home_s = _t2s(home)
        away_s = _t2s(away)
        hx, ax = xi(fid, 'home'), xi(fid, 'away')
        hs, hc, hd = stability(home_s, fid, 'home')
        as_, ac, ad = stability(away_s, fid, 'away')
        bits = []
        bits.append(f"阵型 主{f['home_formation'] or '—'} / 客{f['away_formation'] or '—'}")
        if hx or ax:
            bits.append(f"首发 主{len(hx)}/客{len(ax)}人")
        b2 = []
        if hs is not None:
            seg = f"近5场稳定度 主{hs:.0f}% / 客{as_:.0f}%" if as_ is not None else f"近5场稳定度 主{hs:.0f}%"
            b2.append(seg)
        if hc is not None or ac is not None:
            c1 = f"{hc}/11" if hc is not None else '—'
            c2 = f"{ac}/11" if ac is not None else '—'
            b2.append(f"本轮延续上场首发 主{c1} · 客{c2}")
        coaches = []
        if f['home_coach']:
            coaches.append(f"{home_s}({f['home_coach']})")
        if f['away_coach']:
            coaches.append(f"{away_s}({f['away_coach']})")
        r = xg.get(fid)
        xline = fmt_team(fid, 'home') if r else None
        h = h2h.get(fid)
        hline = None
        if h:
            hline = (f"交手 {h.get('total')}场 主{h.get('home_wins')}胜{h.get('draws')}平{h.get('away_wins')}负"
                     f" · 场均总球 {h.get('avg_total_goals')}")
        body.append((f['date'] or '', fid, home, away, bits, b2, coaches, xline, hline))
        shown += 1

    if not body:
        return []
    body.sort(key=lambda x: (x[0], x[1]))
    out.append(PREFIX + "战术阵容参考 (只读展示, 不参与①入选; 阵容=赛前页面预计值, U21/低级别常缺)")
    out.append("=" * 92)
    for _d, fid, home, away, bits, b2, coaches, xline, hline in body:
        out.append(f"{PREFIX}{_t2s(home)} vs {_t2s(away)} | " + " | ".join(bits))
        if b2:
            out.append(f"   {' | '.join(b2)}")
        if coaches:
            out.append(f"   主教练: {' / '.join(coaches)}")
        if xline:
            out.append(f"   {xline}")
        if hline:
            out.append(f"   {hline}")
    return out


if __name__ == '__main__':
    import sys
    fids = [int(x) for x in sys.argv[1:] if x.isdigit()]
    for l in collect(fids):
        print(l)
