#!/usr/bin/env python3
"""将 actual_outcome 中的比分提取到 reference_score，然后计算λ

2026-11-17 性能优化（逻辑零变化）:
  原实现「全表重置 → 每队一条 UPDATE ... WHERE home_team=?」共 1.6 万条语句,
  而 poisson_predictions 上 home_team/away_team 无索引 → 每条都全表扫 16877 行,
  实测 4.9 分钟。改为先在内存算好每队的 λ, 再按 rowid 批量 executemany 一次写入,
  语义完全等价（缺统计的队仍落 1.0/1.5 默认值）。
"""
import sqlite3, os, re, sys, time

_T0 = time.time()
def _t(msg):
    print(f'  ⏱️  {msg}: {time.time()-_T0:.1f}s')

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

conn = sqlite3.connect(DB_PATH, timeout=60)  # 2026-10-14: 并发写时曾报 database is locked
conn.execute('PRAGMA busy_timeout=60000')
cur = conn.cursor()

# 1. 从 actual_outcome 提取比分到 reference_score
# actual_outcome 格式: "home (2-0)", "away (1-3)", "draw (0-0)"
rows = cur.execute("""
    SELECT rowid, actual_outcome FROM poisson_predictions 
    WHERE actual_outcome IS NOT NULL AND actual_outcome != ''
      AND (reference_score IS NULL OR reference_score = '')
""").fetchall()

print(f'需同步比分: {len(rows)} 场')

updates = []
for rowid, outcome in rows:
    m = re.search(r'\((\d+)\s*-\s*(\d+)\)', outcome)
    if m:
        updates.append((f'{m.group(1)}-{m.group(2)}', rowid))
if updates:
    cur.executemany('UPDATE poisson_predictions SET reference_score=? WHERE rowid=?', updates)
conn.commit()
print(f'已同步: {len(updates)} 场 到 reference_score')
_t('比分同步')

# 2. 统计
total = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE reference_score IS NOT NULL AND reference_score != ""').fetchone()[0]
print(f'\n现在 reference_score 有值: {total} 场')

# 3. 重新计算每队的λ（基于全部比分数据 + 联赛收缩）
# 2026-08-07: 生涯平均会被极端比分污染(如慶南1场5-0 → λ=5.0)，
# 改为向联赛均值收缩: λ_final = (n*λ_sample + K*λ_league) / (n+K), K=5
print('\n=== 重新计算 λ（含联赛收缩） ===')
rows = cur.execute("""SELECT rowid, home_team, away_team, league, reference_score FROM poisson_predictions 
    WHERE reference_score IS NOT NULL AND reference_score != ''""").fetchall()

home_stats = {}
away_stats = {}
team_league = {}  # team -> 出现最多的联赛
league_goals = {}  # league -> 单队场均进球列表(主客都算)
league_cnt = {}

for rowid, h, a, lg, score in rows:
    m = re.search(r'(\d+)\s*-\s*(\d+)', score)
    if not m: continue
    hg, ag = int(m.group(1)), int(m.group(2))
    home_stats.setdefault(h, {'g':[],'c':[]})['g'].append(hg)
    home_stats[h]['c'].append(ag)
    away_stats.setdefault(a, {'g':[],'c':[]})['g'].append(ag)
    away_stats[a]['c'].append(hg)
    # 记录联赛(取出现最多的)
    for t in (h, a):
        team_league.setdefault(t, {})
        team_league[t][lg or ''] = team_league[t].get(lg or '', 0) + 1
    # 联赛进球统计(单队口径: 主队进hg+客队进ag)
    if lg:
        league_goals.setdefault(lg, []).append(hg)
        league_goals[lg].append(ag)
        league_cnt[lg] = league_cnt.get(lg, 0) + 1

# 联赛单队场均进球
league_avg = {lg: sum(g)/len(g) for lg, g in league_goals.items()}
# 兜底: 全库单队场均
global_avg = sum(sum(g) for g in league_goals.values()) / sum(len(g) for g in league_goals.values()) if league_goals else 1.5

def _shrink(avg: float, n: int, lg: str) -> float:
    """联赛收缩: 样本少时向联赛均值回归"""
    K = 5.0
    league_mu = league_avg.get(lg or '', global_avg)
    if n <= 0:
        return round(league_mu, 2)
    return round((n * avg + K * league_mu) / (n + K), 2)

def _main_league(team: str) -> str:
    tl = team_league.get(team, {})
    return max(tl, key=tl.get) if tl else ''

# 内存中算好每队 (λ, λ, conceded) —— 与原实现同口径
team_lambda_home = {}
for team, stats in home_stats.items():
    n = len(stats['g'])
    lg = _main_league(team)
    team_lambda_home[team] = (_shrink(sum(stats['g'])/n, n, lg),
                              _shrink(sum(stats['c'])/n, n, lg))

team_lambda_away = {}
for team, stats in away_stats.items():
    n = len(stats['g'])
    lg = _main_league(team)
    team_lambda_away[team] = (_shrink(sum(stats['g'])/n, n, lg),
                              _shrink(sum(stats['c'])/n, n, lg))

# 一次性写入所有行（原口径: 全表先重置 1.0/1.5，再按队覆盖）
D = (1.0, 1.0, 1.5, 1.5, 1.5, 1.5)  # home_lambda, away_lambda, home_avg_goals, away_avg_goals, home_avg_conceded, away_avg_conceded
all_rows = cur.execute('SELECT rowid, home_team, away_team FROM poisson_predictions').fetchall()
writes = []
for rowid, h, a in all_rows:
    hl = team_lambda_home.get(h)
    al = team_lambda_away.get(a)
    writes.append((
        hl[0] if hl else D[0],
        al[0] if al else D[1],
        hl[0] if hl else D[2],   # 原实现: home_avg_goals = h_lambda
        al[0] if al else D[3],   # 原实现: away_avg_goals = a_lambda
        hl[1] if hl else D[4],
        al[1] if al else D[5],
        rowid,
    ))
cur.executemany("""UPDATE poisson_predictions SET
    home_lambda=?, away_lambda=?, home_avg_goals=?, away_avg_goals=?,
    home_avg_conceded=?, away_avg_conceded=? WHERE rowid=?""", writes)
conn.commit()
_t(f'λ 批量写 {len(writes)} 行')

h_teams = cur.execute('SELECT COUNT(DISTINCT home_team) FROM poisson_predictions WHERE home_lambda != 1.0').fetchone()[0]
a_teams = cur.execute('SELECT COUNT(DISTINCT away_team) FROM poisson_predictions WHERE away_lambda != 1.0').fetchone()[0]
print(f'更新: {h_teams} 主队 + {a_teams} 客队 λ 值')

# 4. 检查FC首尔和蔚山HD
for team in ['FC首尔', '蔚山HD']:
    row = cur.execute('SELECT home_lambda, home_avg_goals, away_lambda, away_avg_goals FROM poisson_predictions WHERE home_team=? LIMIT 1', (team,)).fetchone()
    if row:
        print(f'{team}: 主λ={row[0]}, 主均进={row[1]}, 客λ={row[2]}, 客均进={row[3]}')

conn.close()
_t('总计')
print('\n✅ 完成! 重新运行 team_similarity.py --force 和 ai_analysis.py 即可')
