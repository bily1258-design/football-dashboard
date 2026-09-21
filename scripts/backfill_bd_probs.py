#!/usr/bin/env python3
"""按北单「胜负过关」盘口补算赢盘概率 (ahbd_*)

背景: 命中列的 上/下 方向取自「模型赢盘概率」, 而 命中 ✔/✘ 用比分+单场让球独立判。
       2026-09-21 起该列统一走北单胜负过关 (500.com /bjdcsf, 2way 半盘无走盘) 口径,
       不再用 titan007 的亚盘 (ah_*), 两处盘口对齐同一个「过」行。

让球口径 (与 ahbd_cur_handicap_text 一致): 正数=主队受让, 负数=主队让球
  → 主队过关 ⟺ 净胜 + 让球 > 0

写入字段: ahbd_home_covers_prob / ahbd_push_prob / ahbd_away_covers_prob / ahbd_pred_desc
         (不碰 ah_*, 不参与清单规则; 纯看板显示层)

用法: python3 scripts/backfill_bd_probs.py        # 跑在 fetch_500_bjdc.py 之后
"""

import sys, os, json, logging

BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(BASE_DIR, 'scripts'))
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

from ai_analysis import compute_ah_probs, build_team_strength_model  # noqa: E402

DB_PATH = os.path.join(BASE_DIR, 'data', 'football.db')
RESULTS_PATH = os.path.join(BASE_DIR, 'docs', 'data', 'results.json')


def bd_handicap(m):
    """北单让球 (优先胜负过关『过』行, 兜底让球胜平负『让』行); 无则为 None"""
    for k in ('ahbd_cur_handicap', 'ahbd_open_handicap'):
        v = m.get(k)
        if v is not None and v != '':
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def main():
    logger.info("加载球队实力模型...")
    team_model = build_team_strength_model(DB_PATH)
    if not team_model or not team_model.get('strengths'):
        logger.error("球队模型加载失败")
        return 1
    logger.info(f"  模型包含 {len(team_model['strengths'])} 支球队")

    with open(RESULTS_PATH) as f:
        data = json.load(f)
    matches = data['matches']
    have_line = [m for m in matches if bd_handicap(m) is not None]
    logger.info(f"共 {len(matches)} 场; 有北单让球: {len(have_line)} 场")

    ok = fail = 0
    for m in have_line:
        h = bd_handicap(m)
        h2h = m.get('stats', {}).get('h2h') if m.get('stats') else None
        r = compute_ah_probs(
            team_model,
            m.get('home_team', ''), m.get('away_team', ''),
            home_form_pts=m.get('home_form_pts', 0) or 0,
            away_form_pts=m.get('away_form_pts', 0) or 0,
            home_rank=m.get('home_rank', 0) or 0,
            away_rank=m.get('away_rank', 0) or 0,
            handicap_value=h,
            h2h_stats=h2h,
        )
        if not r:
            fail += 1
            continue
        m['ahbd_home_covers_prob'] = r[0]
        m['ahbd_push_prob'] = r[1]
        m['ahbd_away_covers_prob'] = r[2]
        m['ahbd_pred_desc'] = r[3]
        ok += 1

    logger.info(f"北单口径赢盘概率: 成功 {ok} / 失败 {fail}")

    with open(RESULTS_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    logger.info(f"已写入 {RESULTS_PATH}")

    final = sum(1 for m in matches if m.get('ahbd_home_covers_prob') is not None)
    logger.info(f"最终有北单赢盘概率: {final}/{len(matches)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
