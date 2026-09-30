#!/usr/bin/env python3
"""回填/增量抓取 titan007 详情页的「首发阵容 + 阵型 + 主教练」。
数据源: https://live.titan007.com/detail/{fid}cn.htm  (与 fetch_daily_xg.py 同一个页面, 只多解析阵容段)

入库:
  match_formations(fid PK, ...)  每场一行: 主客阵型/主教练/首发人数/是否有阵容
  match_lineups(fid, side, player_id, ...)  每名球员一行: 首发/替补 + 号码 + 姓名 + 位置块

用法:
  python fetch_lineups.py --days 30 --limit 400      # 回填近30天(可断点续跑, 已抓过的自动跳过)
  python fetch_lineups.py --fid 3014093              # 单场调试
  python fetch_lineups.py --stats                    # 只看覆盖情况
"""
import re, sys, os, sqlite3, time, argparse, urllib.request

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
           'Accept-Language': 'zh-CN,zh;q=0.9'}
PL = re.compile(r"""team/player/(\d+)/(\d+)\.html['"][^>]*>([^<]+)</a>""")
NUM = re.compile(r"class=\"num\">\s*(\d+)")
POS = re.compile(r'class="(home|guest)"')

DDL = """
CREATE TABLE IF NOT EXISTS match_formations (
    fid INTEGER PRIMARY KEY, date TEXT, home_team TEXT, away_team TEXT,
    home_formation TEXT, away_formation TEXT, home_coach TEXT, away_coach TEXT,
    home_starters INTEGER, away_starters INTEGER, has_lineup INTEGER,
    src_updated TEXT
);
CREATE TABLE IF NOT EXISTS match_lineups (
    fid INTEGER, date TEXT, side TEXT, team TEXT, team_id INTEGER,
    player_id INTEGER, player_name TEXT, shirt INTEGER, is_starter INTEGER,
    formation TEXT, coach TEXT, src_updated TEXT,
    PRIMARY KEY (fid, side, player_id)
);
CREATE INDEX IF NOT EXISTS idx_lineups_team ON match_lineups(team, is_starter);
CREATE INDEX IF NOT EXISTS idx_lineups_date ON match_lineups(date);
"""


def safe_fetch(url, delay=1.0, timeout=20):
    time.sleep(delay)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', errors='replace')
    except Exception:
        return None


def _team_info(seg, cls):
    """从 homeN/guestN 头块取 (队名, 阵型, 主教练, 球队id)"""
    m = re.search(r'class="%s">(.*?)</div>' % cls, seg, re.S)
    if not m:
        return None, None, None, None
    b = m.group(1)
    t = re.search(r'team/Summary/(\d+)\.html"[^>]*>([^<]+)</a>', b)
    f = re.search(r'</a>\s*([0-9](?:-[0-9]){1,3})', b)
    c = re.search(r"title='([^']+)'>\(主教练", b)
    return (t.group(2) if t else None, f.group(1) if f else None,
            c.group(1) if c else None, int(t.group(1)) if t else None)


def parse_lineup(html):
    """返回 dict(home, away, players=[(side,pid,name,shirt,is_starter)]) 或 None"""
    i = html.find('首发阵容')
    if i < 0:
        return None
    seg = html[i:]
    home = _team_info(seg, 'homeN')
    away = _team_info(seg, 'guestN')
    if not home[0] or not away[0]:
        return None
    pos = [(m.start(), m.group(1)) for m in POS.finditer(seg)]
    # 交替配对: home 块 → 到下一个 guest 为止; guest 块 → 到下一个 home 为止
    blocks = []          # (side, html块, block序号)
    k = 0
    t = 0
    while t < len(pos):
        side, start = pos[t][1], pos[t][0]
        nxt = pos[t + 1][0] if t + 1 < len(pos) else len(seg)
        blk = seg[start:nxt]
        if PL.search(blk):
            blocks.append((side, blk, k))
            k += 1
        t += 1
    if not blocks:
        return None
    blocks = blocks[:4]          # 只取 首发(主/客) + 替补(主/客)
    players = []
    for side, blk, k in blocks:
        nums = [(m.start(), int(m.group(1))) for m in NUM.finditer(blk)]
        used = set()
        for pm in PL.finditer(blk):
            shirt = None
            for np, nv in reversed(nums):
                if np < pm.start() and np not in used and (pm.start() - np) < 260:
                    shirt = nv
                    used.add(np)
                    break
            players.append((side, int(pm.group(2)), pm.group(3).strip(),
                            shirt, 1 if k < 2 else 0))
    return dict(home=home, away=away, players=players)


def parse_meta(html):
    """队名(繁体)从详情页标题兜底"""
    m = re.search(r'<title>([^<]*)</title>', html or '')
    return m.group(1).strip() if m else None


def store(conn, fid, date, r, html, started):
    hp = [p for p in r['players'] if p[0] == 'home' and p[4] == 1]
    ap = [p for p in r['players'] if p[0] == 'guest' and p[4] == 1]
    n = len(hp) + len(ap)
    conn.execute("""INSERT OR REPLACE INTO match_formations
        (fid,date,home_team,away_team,home_formation,away_formation,home_coach,away_coach,
         home_starters,away_starters,has_lineup,src_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (fid, date, r['home'][0], r['away'][0], r['home'][1], r['away'][1],
                  r['home'][2], r['away'][2], len(hp), len(ap), 1 if n >= 22 else 0,
                  datetime_now()))
    conn.execute("DELETE FROM match_lineups WHERE fid=?", (fid,))
    rows = []
    for side, pid, name, shirt, starter in r['players']:
        info = r['home'] if side == 'home' else r['away']
        rows.append((fid, date, side, info[0], info[3], pid, name, shirt, starter,
                     info[1], info[2], datetime_now()))
    conn.executemany("""INSERT OR REPLACE INTO match_lineups
        (fid,date,side,team,team_id,player_id,player_name,shirt,is_starter,formation,coach,src_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return n


def sane(r):
    """首发人数合理性: 两侧都应是 11 人(部分场次页面单边缺块 → 判为解析异常)"""
    h = sum(1 for p in r['players'] if p[0] == 'home' and p[4] == 1)
    a = sum(1 for p in r['players'] if p[0] == 'guest' and p[4] == 1)
    return h >= 10 and a >= 10


def datetime_now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def candidates(conn, days, limit):
    where = "(pp.match_id LIKE '29%' OR pp.match_id LIKE '30%')"
    if days:
        where += " AND pp.date >= date('now','-%d day')" % days
    rows = conn.execute(f"""
        SELECT pp.match_id, pp.date, pp.home_team, pp.away_team
        FROM poisson_predictions pp
        LEFT JOIN match_formations mf ON mf.fid = CAST(pp.match_id AS INTEGER)
        WHERE {where} AND mf.fid IS NULL
        ORDER BY pp.date DESC LIMIT ?""", (limit,)).fetchall()
    return rows


def stats(conn):
    try:
        tot = conn.execute("SELECT COUNT(*) FROM match_formations").fetchone()[0]
        ok = conn.execute("SELECT COUNT(*) FROM match_formations WHERE has_lineup=1").fetchone()[0]
        lp = conn.execute("SELECT COUNT(*) FROM match_lineups").fetchone()[0]
        teams = conn.execute("SELECT COUNT(DISTINCT team) FROM match_lineups").fetchone()[0]
        print(f"match_formations: {tot} 场 (有完整阵容 {ok} 场, {ok*100//max(tot,1)}%) | 球员行 {lp} | 覆盖球队 {teams}")
        for d, c in conn.execute("""SELECT substr(date,1,7), COUNT(*) FROM match_formations
                                    GROUP BY 1 ORDER BY 1 DESC LIMIT 6"""):
            print(f"   {d}: {c} 场")
    except sqlite3.OperationalError as e:
        print('表尚未创建:', e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--limit', type=int, default=400)
    ap.add_argument('--sleep', type=float, default=1.0)
    ap.add_argument('--fid', type=int)
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--only-partial', action='store_true',
                    help='只重抓「无阵容/半残(单边<10人)」的场次, 用于修复解析(不改已成功的场次)')
    a = ap.parse_args()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA busy_timeout=30000')
    conn.executescript(DDL)
    conn.commit()
    if a.stats:
        stats(conn)
        return
    if a.fid:
        html = safe_fetch(f'https://live.titan007.com/detail/{a.fid}cn.htm', delay=0, timeout=25)
        r = parse_lineup(html) if html else None
        if not r:
            print('解析失败 (页面无阵容段或未公布)')
            return
        n = store(conn, a.fid, time.strftime('%Y-%m-%d'), r, html, 0)
        conn.commit()
        print(f"fid={a.fid} {r['home'][0]} {r['home'][1]} vs {r['away'][0]} {r['away'][1]} | 球员 {n} 人")
        return

    if a.only_partial:
        rows = conn.execute("""SELECT fid, date, home_team, away_team FROM match_formations
            WHERE has_lineup=0 OR home_starters<10 OR away_starters<10
            ORDER BY date DESC LIMIT ?""", (a.limit,)).fetchall()
    else:
        rows = candidates(conn, a.days, a.limit)
    print(f"待抓 {len(rows)} 场 (近{a.days}天, 上限{a.limit})")
    t0 = time.time()
    ok = miss = 0
    for i, (mid, date, ht, at) in enumerate(rows, 1):
        fid = int(mid)
        html = safe_fetch(f'https://live.titan007.com/detail/{fid}cn.htm', delay=a.sleep)
        r = parse_lineup(html) if html else None
        if r and not sane(r):
            conn.execute("""INSERT OR REPLACE INTO match_formations
                (fid,date,home_team,away_team,has_lineup,src_updated) VALUES (?,?,?,?,0,?)""",
                         (fid, date, ht, at, datetime_now()))
            r = None          # 单边人数异常 → 按失败处理, 留给 --only-partial 重抓
        if r:
            n = store(conn, fid, date, r, html, 0)
            ok += 1
        else:
            conn.execute("""INSERT OR REPLACE INTO match_formations
                (fid,date,home_team,away_team,has_lineup,src_updated) VALUES (?,?,?,?,0,?)""",
                         (fid, date, ht, at, datetime_now()))
            miss += 1
        if i % 25 == 0 or i == len(rows):
            conn.commit()
            el = time.time() - t0
            eta = el / i * (len(rows) - i)
            print(f"  {i}/{len(rows)}  成功{ok} 无阵容{miss}  用时{el/60:.1f}分 预计剩余{eta/60:.1f}分", flush=True)
    conn.commit()
    stats(conn)
    conn.close()


if __name__ == '__main__':
    main()
