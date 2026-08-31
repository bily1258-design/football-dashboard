#!/usr/bin/env python3
"""gen_light_results.py — 从 results.json 生成看板专用精简版 results_light.json。

背景: results.json 含 stats(近况/h2h) 等大字段, 占 ~72% 体积, 但看板
(docs/script.js) 根本不消费它们。每次看板加载都要全量下载 + 解析 13MB,
导致加载变慢。此脚本只保留 script.js 实际引用的字段, 输出无缩进单行 JSON,
体积 ~77%↓, GitHub Pages 再叠加 gzip 后传输量极小。

用法: python3 scripts/gen_light_results.py
挂在 fetch_and_push.sh 里 backfill_ah_probs 之后 (亚盘补算完、推送前),
保证 light 版永远与最终 results.json 同步。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'docs', 'data', 'results.json')
DST = os.path.join(ROOT, 'docs', 'data', 'results_light.json')

# script.js 实际引用的 match 字段 (grep m\.[a-z_]+ 于 docs/script.js)
KEEP_MATCH = {
    'ah_away', 'ah_away_covers_prob', 'ah_handicap', 'ah_handicap_text',
    'ah_home', 'ah_home_covers_prob', 'ah_open_away', 'ah_open_handicap',
    'ah_open_handicap_text', 'ah_open_home', 'ah_push_prob',
    'away_team', 'best_value', 'comparison', 'date', 'event', 'fid', 'hit',
    'home_team', 'importance_weight', 'lgbm_confidence', 'lgbm_draw',
    'lgbm_loss', 'lgbm_prediction', 'lgbm_win', 'low_priority', 'match_time',
    'model_draw', 'model_loss', 'model_prediction', 'model_win', 'odds_win',
    'pin_comparison', 'postponed', 'score', 'similar_matches', 'source',
    'total_goals_top3', 'ts_draw', 'ts_loss', 'ts_win', 'value_bets', 'warning',
}

# similar_matches 子项: script.js 只用 home_team/away_team/score/similarity
KEEP_SIM = {'home_team', 'away_team', 'score', 'similarity'}

# total_goals_top3 子项: script.js 只用 total_goals/prob
KEEP_GOALS = {'total_goals', 'prob'}


def slim_similar(item):
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k in KEEP_SIM}
    return item


def slim_goals(item):
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k in KEEP_GOALS}
    return item


def slim_match(m):
    out = {}
    for k in KEEP_MATCH:
        if k not in m:
            continue
        v = m[k]
        if v is None:
            continue
        if k == 'similar_matches' and isinstance(v, list):
            v = [slim_similar(s) for s in v]
        elif k == 'total_goals_top3' and isinstance(v, list):
            v = [slim_goals(g) for g in v]
        out[k] = v
    return out


def main():
    if not os.path.exists(SRC):
        print(f'[gen_light] 源文件不存在: {SRC}')
        sys.exit(1)

    with open(SRC, encoding='utf-8') as f:
        data = json.load(f)

    full_size = os.path.getsize(SRC)
    light = dict(data)
    light['matches'] = [slim_match(m) for m in data['matches']]

    with open(DST, 'w', encoding='utf-8') as f:
        json.dump(light, f, ensure_ascii=False, separators=(',', ':'))

    light_size = os.path.getsize(DST)
    print(f'[gen_light] {full_size/1e6:.1f}MB → {light_size/1e6:.1f}MB '
          f'({(1-light_size/full_size)*100:.0f}%↓) → {DST}')


if __name__ == '__main__':
    main()
