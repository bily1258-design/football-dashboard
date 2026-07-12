#!/usr/bin/env python3
"""重新校准 DB 中所有预测记录：联赛分层参数 + isotonic 校准 + 信心分层

用法:
  python scripts/recalibrate_db.py --db data/football.db
"""
import sys, os, json, sqlite3, argparse

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_PATH = os.path.join(REPO_DIR, "data", "calibration_params.json")

# 默认参数（与 predict_from_odds.py 保持一致）
BASE_TOTAL_GOALS = 2.4
HOME_ADV = 0.15
SKILL_FACTOR = 0.6
POISSON_WEIGHT = 0.5
IMPLIED_WEIGHT = 0.5

_league_params = {}
_calib_map = []

def load_calibration():
    global _league_params, _calib_map
    if not os.path.exists(CALIB_PATH):
        print(f'[WARN] 校准参数文件不存在: {CALIB_PATH}')
        return False
    with open(CALIB_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    _league_params = data.get('league_params', {})
    _calib_map = data.get('global_calibration', [])
    print(f'📋 校准参数: {len(_league_params)} 联赛, isotonic {len(_calib_map)} 点')
    return True

def get_league_params(league):
    if league in _league_params:
        return _league_params[league]
    return None

def calibrate_prob(prob):
    if not _calib_map:
        return prob
    for i in range(len(_calib_map) - 1):
        lo_p, lo_a = _calib_map[i]
        hi_p, hi_a = _calib_map[i+1]
        if lo_p <= prob <= hi_p:
            t = (prob - lo_p) / max(hi_p - lo_p, 1e-9)
            return lo_a + t * (hi_a - lo_a)
    if prob < _calib_map[0][0]:
        return _calib_map[0][1]
    return _calib_map[-1][1]

def confidence_tier(prob):
    if prob >= 0.55: return 'high'
    elif prob >= 0.50: return 'medium'
    elif prob >= 0.45: return 'low'
    return 'very_low'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=str, default=None)
    args = parser.parse_args()
    
    db_path = args.db or os.path.join(REPO_DIR, 'data', 'football.db')
    if not os.path.exists(db_path):
        print(f'[ERROR] DB不存在: {db_path}')
        return 1
    
    if not load_calibration():
        print('[ERROR] 无法加载校准参数，退出')
        return 1
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # 确保3个新列存在
    for col, ctype in [('confidence_tier', 'TEXT'), ('calibrated_prob', 'REAL'), ('best_direction_cn', 'TEXT')]:
        try:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass
    conn.commit()
    
    # 读取所有有 final_win/final_draw/final_loss 的记录
    cur.execute("SELECT id, league, final_win, final_draw, final_loss, prediction FROM poisson_predictions")
    rows = cur.fetchall()
    print(f'📊 需校准记录: {len(rows)}')
    
    updated = 0
    tier_counts = {'high': 0, 'medium': 0, 'low': 0, 'very_low': 0}
    
    for row in rows:
        fw = row['final_win'] or 0
        fd = row['final_draw'] or 0
        fl = row['final_loss'] or 0
        if fw + fd + fl == 0:
            continue
        
        # 概率最高方向
        if fw >= fd and fw >= fl:
            best_dir = '主胜'
            max_prob = fw
        elif fl >= fd:
            best_dir = '客胜'
            max_prob = fl
        else:
            best_dir = '平局'
            max_prob = fd
        
        # 校准概率
        cal_prob = calibrate_prob(max_prob)
        tier = confidence_tier(cal_prob)
        tier_counts[tier] += 1
        
        cur.execute("""
            UPDATE poisson_predictions 
            SET confidence_tier = ?, calibrated_prob = ?, best_direction_cn = ?
            WHERE id = ?
        """, (tier, round(cal_prob, 3), best_dir, row['id']))
        updated += 1
    
    conn.commit()
    conn.close()
    
    print(f'✅ 已更新: {updated} 条')
    print(f'📊 信心分层: high={tier_counts["high"]}, medium={tier_counts["medium"]}, low={tier_counts["low"]}, very_low={tier_counts["very_low"]}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
