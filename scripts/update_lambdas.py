#!/usr/bin/env python3
"""从 DB 中已有比分统计每队进球均值作为 lambda"""
import sqlite3, re, os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

rows = cur.execute("""SELECT home_team, away_team, reference_score FROM poisson_predictions 
    WHERE reference_score IS NOT NULL AND reference_score != ''""").fetchall()

home_stats = {}
away_stats = {}

for h, a, score in rows:
    m = re.search(r'(\d+)\s*-\s*(\d+)', score)
    if not m: continue
    hg, ag = int(m.group(1)), int(m.group(2))
    home_stats.setdefault(h, {'g':[],'c':[]})['g'].append(hg)
    home_stats[h]['c'].append(ag)
    away_stats.setdefault(a, {'g':[],'c':[]})['g'].append(ag)
    away_stats[a]['c'].append(hg)

for team, stats in home_stats.items():
    n = len(stats['g'])
    h_lambda = round(sum(stats['g'])/n, 2) if n else 1.5
    h_conceded = round(sum(stats['c'])/n, 2) if n else 1.5
    cur.execute("""UPDATE poisson_predictions SET 
        home_lambda=?, home_avg_goals=?, home_avg_conceded=?
        WHERE home_team=?""",
        (h_lambda, h_lambda, h_conceded, team))

for team, stats in away_stats.items():
    n = len(stats['g'])
    a_lambda = round(sum(stats['g'])/n, 2) if n else 1.5
    a_conceded = round(sum(stats['c'])/n, 2) if n else 1.5
    cur.execute("""UPDATE poisson_predictions SET 
        away_lambda=?, away_avg_goals=?, away_avg_conceded=?
        WHERE away_team=?""",
        (a_lambda, a_lambda, a_conceded, team))

h_l = cur.execute('SELECT COUNT(DISTINCT home_team) FROM poisson_predictions WHERE home_lambda != 1.0').fetchone()[0]
a_l = cur.execute('SELECT COUNT(DISTINCT away_team) FROM poisson_predictions WHERE away_lambda != 1.0').fetchone()[0]
conn.commit()
conn.close()
print(f'✅ 更新 {h_l} 主队 + {a_l} 客队 lambda/场均数据')
