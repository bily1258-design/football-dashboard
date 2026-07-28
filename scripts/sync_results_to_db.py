#!/usr/bin/env python3
"""
将 results.json (AI分析结果) 中的比赛同步到 poisson_predictions 表。
让 xG 抓取、历史相似匹配、总进球预测能覆盖当天比赛。
"""
import json, os, re, sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_PATH = os.path.join(PROJECT_DIR, 'docs', 'data', 'results.json')
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')

import sqlite3

def parse_score(score_str):
    """从比分字符串提取主客进球。支持 '2-4', '2 - 4', '2:4'"""
    if not score_str or score_str in ('-', '', 'v', 'vs'):
        return None, None
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(score_str))
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None

def score_to_actual_outcome(home_team, away_team, hg, ag):
    """生成 actual_outcome 格式: 'home (2-0)', 'away (1-3)', 'draw (0-0)'"""
    if hg > ag:
        return f"home ({hg}-{ag})"
    elif ag > hg:
        return f"away ({hg}-{ag})"
    else:
        return f"draw ({hg}-{ag})"

def main():
    # 读取 results.json
    if not os.path.exists(RESULTS_PATH):
        print(f"❌ 未找到 {RESULTS_PATH}")
        sys.exit(1)
    
    with open(RESULTS_PATH) as f:
        data = json.load(f)
    
    matches = data.get('matches', [])
    print(f"📄 results.json: {len(matches)} 场比赛")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 获取已在 poisson_predictions 中的 match_id 集合
    existing = set(row[0] for row in cur.execute(
        "SELECT DISTINCT match_id FROM poisson_predictions"
    ).fetchall())
    print(f"🗄️  poisson_predictions 已有: {len(existing)} 个 match_id")
    
    now = datetime.now().isoformat()
    inserted = 0
    skipped = 0
    errors = 0
    
    for m in matches:
        fid = str(m.get('fid', ''))
        if not fid:
            skipped += 1
            continue
        
        # 已存在则跳过
        if fid in existing:
            skipped += 1
            continue
        
        # 基础字段
        match_date = m.get('date', '')
        league = m.get('event', m.get('league', ''))
        home_team = m.get('home_team', '')
        away_team = m.get('away_team', '')
        kickoff_time = m.get('match_time', m.get('display_time', ''))
        source = m.get('source', m.get('source_type', 'unknown'))
        
        # 比分
        score_str = m.get('score', '')
        hg, ag = parse_score(score_str)
        ref_score = f"{hg}-{ag}" if hg is not None else ''
        actual = score_to_actual_outcome(home_team, away_team, hg, ag) if hg is not None else ''
        
        # 赔率 - 优先用直接字段
        odds_win = m.get('odds_win', 0) or 0
        odds_draw = m.get('odds_draw', 0) or 0
        odds_loss = m.get('odds_loss', 0) or 0
        
        # 开盘赔率（取 comparison 或 open_* 字段）
        comp = m.get('comparison', {})
        open_odds = comp.get('open', []) if comp else []
        pin_close_w = float(open_odds[0]) if len(open_odds) >= 1 else m.get('open_win_pin', 0) or 0
        pin_close_d = float(open_odds[1]) if len(open_odds) >= 2 else m.get('open_draw_pin', 0) or 0
        pin_close_l = float(open_odds[2]) if len(open_odds) >= 3 else m.get('open_loss_pin', 0) or 0
        
        # bet365 赔率（如果有）
        bet_w = m.get('odds_bet365_close_win', m.get('odds_bet365_win', 0)) or 0
        bet_d = m.get('odds_bet365_close_draw', m.get('odds_bet365_draw', 0)) or 0
        bet_l = m.get('odds_bet365_close_loss', m.get('odds_bet365_loss', 0)) or 0
        
        # 排名
        home_rank = m.get('home_rank', m.get('home_pts', ''))
        away_rank = m.get('away_rank', m.get('away_pts', ''))
        if isinstance(home_rank, (int, float)):
            home_rank = str(int(home_rank))
        if isinstance(away_rank, (int, float)):
            away_rank = str(int(away_rank))
        
        try:
            cur.execute("""INSERT OR IGNORE INTO poisson_predictions
                (match_id, date, league, home_team, away_team, kickoff_time,
                 source, reference_score, actual_outcome, created_at,
                 odds_win, odds_draw, odds_loss,
                 pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
                 bet365_close_w, bet365_close_d, bet365_close_l,
                 home_ranking, away_ranking)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                0, 0, 0,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?)""",
                (fid, match_date, league, home_team, away_team, kickoff_time,
                 source, ref_score, actual, now,
                 pin_close_w, pin_close_d, pin_close_l,
                 bet_w, bet_d, bet_l,
                 home_rank, away_rank))
            
            if cur.rowcount > 0:
                inserted += 1
                existing.add(fid)  # 避免同批次重复
        except Exception as e:
            errors += 1
    
    conn.commit()
    
    # 为新插入的行设置默认 λ
    cur.executescript("""
        UPDATE poisson_predictions SET
            home_lambda = ROUND(ABS(RANDOM() % 3 + 10) / 10.0 + 0.7, 2),
            away_lambda = ROUND(ABS(RANDOM() % 3 + 10) / 10.0 + 0.7, 2),
            home_avg_goals = 1.5,
            away_avg_goals = 1.5,
            home_avg_conceded = 1.5,
            away_avg_conceded = 1.5
        WHERE home_lambda IS NULL OR home_lambda = 0;
    """)
    conn.commit()
    conn.close()
    
    print(f"✅ 同步完成: 插入 {inserted} 场, 跳过 {skipped} 场, 错误 {errors} 场")

if __name__ == '__main__':
    main()
