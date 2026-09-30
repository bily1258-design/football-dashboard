#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 档入模候选特征: 阵容稳定度 / 阵型延续 / 攻防差 (只读, 不写任何库与模型)

设计铁律 —— 每个特征只能使用「本场之前」的数据:
  训练端可得、推理端可得, 两侧同源, 无泄漏。

⚠️ 本场首发延续度(本场首发 ∩ 上一场首发) 故意不入模:
   推理时点(赛前数小时)通常拿不到本场首发 → 若训练端喂该值会造成 train/serve skew,
   改用「最近一场延续度 last_cont」作代理, 该值在赛前一定可得。

特征 (11 维, 缺失一律 -1):
  stab_home/away        近5场 首发延续度均值 (两队相邻场次 首发交集/11)
  last_cont_home/away   最近一场 首发延续度
  form_switch_home/away 近5场 阵型切换率 (相邻场阵型不同数/可比对数, 0-1; 越高越不稳)
  lu_cover_home/away    近5场 中「赛后有完整首发」占比 (0-1; 反映数据可得性)
  shot_diff_3           近3场 场均射门差 (主 - 客)
  poss_diff_3           近3场 场均控球差
  corner_diff_3         近3场 场均角球差

用法:
    from lineup_features import LineupFeatureBuilder, FEATURE_NAMES
    b = LineupFeatureBuilder(conn)
    f = b.features(fid, home_team, away_team)   # dict[str, float]
"""

DB_TEAM_MIN = 10          # 单边首发 <10 人视为未出阵容(残缺), 不参与延续度
MIN_FULL = 11             # 完整首发阈值


def _norm(s):
    return ''.join(str(s or '').split())


FEATURE_NAMES = [
    'stab_home', 'stab_away',
    'last_cont_home', 'last_cont_away',
    'form_switch_home', 'form_switch_away',
    'lu_cover_home', 'lu_cover_away',
    'shot_diff_3', 'poss_diff_3', 'corner_diff_3',
]

MISSING = -1.0

# 分组: 用于离线对照时拆开看「哪一组真有增益」(2026-09-30)
LINEUP_NAMES = FEATURE_NAMES[:8]      # 阵容/阵型侧(纯历史推导, 赛前一定可得)
ATTACK_NAMES = FEATURE_NAMES[8:]      # 攻防差侧(来自 xg_features 赛前滚动均值)


class LineupFeatureBuilder(object):
    def __init__(self, conn):
        self.conn = conn
        self.hist = {}        # team_norm -> [ {date, fid, players:set, n, formation}, ... ] 按 date 升序
        self.by_fid = {}      # (fid, side) -> team_norm
        self._load()

    # ---------- 装载 ----------
    def _load(self):
        cur = self.conn.execute(
            "SELECT fid, date, side, team, player_id, formation "
            "FROM match_lineups WHERE is_starter=1 ORDER BY date, fid"
        )
        tmp = {}
        for fid, date, side, team, pid, form in cur.fetchall():
            key = (fid, side)
            rec = tmp.get(key)
            if rec is None:
                rec = {'fid': fid, 'date': date or '', 'team': _norm(team),
                       'players': set(), 'formation': form or ''}
                tmp[key] = rec
            if pid:
                rec['players'].add(pid)
        for (fid, side), rec in tmp.items():
            n = len(rec['players'])
            if n < DB_TEAM_MIN:
                continue                     # 残缺阵容: 不建历史(避免把半场当整场)
            rec['n'] = n
            self.hist.setdefault(rec['team'], []).append(rec)
            self.by_fid[(fid, side)] = rec['team']
        for team in self.hist:
            self.hist[team].sort(key=lambda r: (r['date'], r['fid']))

    # ---------- 单队历史特征 ----------
    @staticmethod
    def _cont(a, b):
        """相邻两场 首发延续度 = 交集/11"""
        return len(a['players'] & b['players']) / 11.0

    def _team_feats(self, team, fid, date, out, suffix):
        lst = self.hist.get(_norm(team))
        if not lst:
            out['stab_' + suffix] = MISSING
            out['last_cont_' + suffix] = MISSING
            out['form_switch_' + suffix] = MISSING
            out['lu_cover_' + suffix] = MISSING
            return
        d0 = (date or '')[:10]
        # 严格早于本场: 排除本场自己, 并排除该队「本场之后」的场次(否则就是未来信息泄漏)
        prior = [r for r in lst if r['fid'] != fid and (r['date'] or '')[:10] < d0]
        if not prior:
            out['stab_' + suffix] = MISSING
            out['last_cont_' + suffix] = MISSING
            out['form_switch_' + suffix] = MISSING
            out['lu_cover_' + suffix] = MISSING
            return
        last5 = prior[-5:]
        # 延续度: 需相邻两场
        conts = [self._cont(last5[i - 1], last5[i]) for i in range(1, len(last5))]
        out['last_cont_' + suffix] = (conts[-1] if conts else MISSING)
        out['stab_' + suffix] = (sum(conts) / len(conts) if conts else MISSING)
        # 阵型切换率
        forms = [r['formation'] for r in last5 if r['formation']]
        pairs = [(forms[i - 1], forms[i]) for i in range(1, len(forms))]
        out['form_switch_' + suffix] = (
            sum(1 for a, b in pairs if a != b) / float(len(pairs)) if pairs else MISSING)
        # 数据可得性
        out['lu_cover_' + suffix] = sum(1 for r in last5 if r['n'] >= MIN_FULL) / float(len(last5))
        return

    # ---------- 对外 ----------
    def features(self, fid, home_team, away_team, date='', shots=None):
        out = {}
        # 用 fid+side 的规范名优先(与 match_lineups 同源, 避免简繁/空格差异)
        h = self.by_fid.get((fid, 'home')) or _norm(home_team)
        a = self.by_fid.get((fid, 'guest')) or self.by_fid.get((fid, 'away')) or _norm(away_team)
        self._team_feats(h, fid, date, out, 'home')
        self._team_feats(a, fid, date, out, 'away')
        for k, v in zip(('shot_diff_3', 'poss_diff_3', 'corner_diff_3'),
                        self._diff3(fid, shots)):
            out[k] = v
        return {k: out.get(k, MISSING) for k in FEATURE_NAMES}

    def _diff3(self, fid, shots=None):
        """攻防差取自 xg_features(该场自身的赛前滚动均值, 天然不含本场结果)"""
        if shots is not None:
            return shots
        try:
            r = self.conn.execute(
                'SELECT home_shots_3, away_shots_3, home_possession_3, away_possession_3, '
                'home_corners_3, away_corners_3 FROM xg_features WHERE sid = ?', (fid,)
            ).fetchone()
        except Exception:
            r = None
        if not r:
            return (MISSING, MISSING, MISSING)
        def d(x, y):
            if x is None or y is None:
                return MISSING
            return float(x) - float(y)
        return (d(r[0], r[1]), d(r[2], r[3]), d(r[4], r[5]))

    def has_match(self, fid):
        return (fid, 'home') in self.by_fid or (fid, 'guest') in self.by_fid


def main():
    import argparse
    import sqlite3
    import os
    ap = argparse.ArgumentParser(description='阵容/阵型/攻防特征 (只读)')
    ap.add_argument('--fid', type=int, help='打印指定 fid 的 11 维特征')
    ap.add_argument('--stats', action='store_true', help='打印覆盖率统计')
    ap.add_argument('--db', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 '..', 'data', 'football.db'))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    b = LineupFeatureBuilder(conn)
    print('阵容历史球队数: %d | 有阵容场次(单边>=%d人): %d'
          % (len(b.hist), DB_TEAM_MIN, len(set(f for (f, s) in b.by_fid))))
    if args.fid:
        row = conn.execute('SELECT home_team, away_team, date FROM match_formations WHERE fid=?',
                           (args.fid,)).fetchone()
        ht = row[0] if row else ''
        at = row[1] if row else ''
        f = b.features(args.fid, ht, at)
        print('%s vs %s' % (ht, at))
        for k in FEATURE_NAMES:
            print('  %-18s %.3f' % (k, f[k]))
    if args.stats:
        n = miss = 0
        for (fid, side) in list(b.by_fid):
            if side != 'home':
                continue
            row = conn.execute('SELECT home_team, away_team FROM match_formations WHERE fid=?',
                               (fid,)).fetchone()
            if not row:
                continue
            f = b.features(fid, row[0], row[1])
            n += 1
            if f['stab_home'] != MISSING and f['stab_away'] != MISSING:
                miss += 1
        print('可算双边稳定度的场次: %d / %d' % (miss, n))


if __name__ == '__main__':
    main()
