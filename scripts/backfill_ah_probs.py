#!/usr/bin/env python3
"""补算亚盘覆盖概率（ah_home_covers_prob / ah_away_covers_prob / ah_push_prob）
复用 ai_analysis.py 中的 compute_ah_probs 和 build_team_strength_model"""

import sys, os, json, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Import from ai_analysis
sys.path.insert(0, os.path.dirname(__file__))
from ai_analysis import compute_ah_probs, build_team_strength_model
from ai_analysis import DB_PATH as AI_DB_PATH  # same path but explicit

BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH = os.path.join(BASE_DIR, 'data', 'football.db')
RESULTS_PATH = os.path.join(BASE_DIR, 'docs', 'data', 'results.json')

def main():
    # 1. 加载球队模型
    logger.info("加载球队实力模型...")
    team_model = build_team_strength_model(DB_PATH)
    if not team_model or not team_model.get('strengths'):
        logger.error("球队模型加载失败")
        return 1
    logger.info(f"  模型包含 {len(team_model['strengths'])} 支球队")

    # 2. 加载 results.json
    with open(RESULTS_PATH) as f:
        data = json.load(f)
    matches = data['matches']
    logger.info(f"共 {len(matches)} 场比赛")

    # 3. 统计需要补算的场次
    need = [m for m in matches 
            if m.get('ah_handicap') is not None 
            and m.get('ah_home_covers_prob') is None]
    already = [m for m in matches 
               if m.get('ah_home_covers_prob') is not None]
    no_ah = [m for m in matches 
             if m.get('ah_handicap') is None]
    
    logger.info(f"已有亚盘概率: {len(already)}")
    logger.info(f"需补算亚盘概率: {len(need)}")
    logger.info(f"无亚盘盘口: {len(no_ah)}")

    # 4. 循环补算
    ok = 0
    for m in need:
        h_v = m.get('ah_handicap')
        if h_v is None:
            continue
        
        h2h = m.get('stats', {}).get('h2h') if m.get('stats') else None
        
        ah_r = compute_ah_probs(
            team_model,
            m.get('home_team', ''), m.get('away_team', ''),
            home_form_pts=m.get('home_form_pts', 0) or 0,
            away_form_pts=m.get('away_form_pts', 0) or 0,
            home_rank=m.get('home_rank', 0) or 0,
            away_rank=m.get('away_rank', 0) or 0,
            handicap_value=h_v,
            h2h_stats=h2h,
        )
        if ah_r:
            m['ah_home_covers_prob'] = ah_r[0]
            m['ah_push_prob'] = ah_r[1]
            m['ah_away_covers_prob'] = ah_r[2]
            m['ah_pred_desc'] = ah_r[3]
            ok += 1

    logger.info(f"补算成功: {ok}/{len(need)}")

    # 5. 写回
    with open(RESULTS_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已写入 {RESULTS_PATH}")

    # 验证
    final_has = sum(1 for m in matches if m.get('ah_home_covers_prob') is not None)
    logger.info(f"最终有亚盘概率: {final_has}/{len(matches)}")

    return 0

if __name__ == '__main__':
    sys.exit(main())
