#!/usr/bin/env python3
"""将 actual_outcome 中的比分提取到 reference_score，然后计算λ"""
import sqlite3, os, re, sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. 从 actual_outcome 提取比分到 reference_score
# actual_outcome 格式: "home (2-0)", "away (1-3)", "draw (0-0)"
updated = 0
rows = cur.execute("""
    SELECT rowid, actual_outcome FROM poisson_predictions 
    WHERE actual_outcome IS NOT NULL AND actual_outcome != ''
      AND (reference_score IS NULL OR reference_score = '')
""").fetchall()

print(f'需同步比分: {len(rows)} 场')

for rowid, outcome in rows:
    m = re.search(r'\((\d+)\s*-\s*(\d+)\)', outcome)
    if m:
        score = f'{m.group(1)}-{m.group(2)}'
        cur.execute('UPDATE poisson_predictions SET reference_score=? WHERE rowid=?', (score, rowid))
        updated += 1

conn.commit()
print(f'已同步: {updated} 场 到 reference_score')

# 2. 统计
total = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE reference_score IS NOT NULL AND reference_score != ""').fetchone()[0]
print(f'\n现在 reference_score 有值: {total} / 12495 场')

# 3. 重新计算每队的λ（基于全部比分数据 + 联赛收缩）
# 2026-08-07: 生涯平均会被极端比分污染(如慶南1场5-0 → λ=5.0)，
# 改为向联赛均值收缩: λ_final = (n*λ_sample + K*λ_league) / (n+K), K=5
print('\n=== 重新计算 λ（含联赛收缩） ===')
rows = cur.execute("""SELECT home_team, away_team, league, reference_score FROM poisson_predictions 
    WHERE reference_score IS NOT NULL AND reference_score != ''""").fetchall()

home_stats = {}
away_stats = {}
team_league = {}  # team -> 出现最多的联赛
league_goals = {}  # league -> 单队场均进球列表(主客都算)
league_cnt = {}

for h, a, lg, score in rows:
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

# 重置所有λ为默认值，再重新设置
cur.execute('UPDATE poisson_predictions SET home_lambda=1.0, away_lambda=1.0, home_avg_goals=1.5, away_avg_goals=1.5, home_avg_conceded=1.5, away_avg_conceded=1.5')

for team, stats in home_stats.items():
    n = len(stats['g'])
    lg = _main_league(team)
    h_lambda = _shrink(sum(stats['g'])/n, n, lg)
    h_conceded = _shrink(sum(stats['c'])/n, n, lg)
    cur.execute("""UPDATE poisson_predictions SET 
        home_lambda=?, home_avg_goals=?, home_avg_conceded=?
        WHERE home_team=?""", (h_lambda, h_lambda, h_conceded, team))

for team, stats in away_stats.items():
    n = len(stats['g'])
    lg = _main_league(team)
    a_lambda = _shrink(sum(stats['g'])/n, n, lg)
    a_conceded = _shrink(sum(stats['c'])/n, n, lg)
    cur.execute("""UPDATE poisson_predictions SET 
        away_lambda=?, away_avg_goals=?, away_avg_conceded=?
        WHERE away_team=?""", (a_lambda, a_lambda, a_conceded, team))

conn.commit()

h_teams = cur.execute('SELECT COUNT(DISTINCT home_team) FROM poisson_predictions WHERE home_lambda != 1.0').fetchone()[0]
a_teams = cur.execute('SELECT COUNT(DISTINCT away_team) FROM poisson_predictions WHERE away_lambda != 1.0').fetchone()[0]
print(f'更新: {h_teams} 主队 + {a_teams} 客队 λ 值')

# 4. 检查FC首尔和蔚山HD
for team in ['FC首尔', '蔚山HD']:
    row = cur.execute('SELECT home_lambda, home_avg_goals, away_lambda, away_avg_goals FROM poisson_predictions WHERE home_team=? LIMIT 1', (team,)).fetchone()
    if row:
        print(f'{team}: 主λ={row[0]}, 主均进={row[1]}, 客λ={row[2]}, 客均进={row[3]}')

# 5. 查看team_similarity的λ回退是否会有改善
# 上面已经更新了team级别的λ，现在每个队都有基于完整比分数据的λ
# λ回退机制会自动使用这些值

conn.close()
print('\n✅ 完成! 重新运行 team_similarity.py --force 和 ai_analysis.py 即可')
