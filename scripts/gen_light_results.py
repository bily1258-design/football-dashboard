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
    # 500.com 北单让球兜底 (初=让球胜平负, 即=胜负过关)
    'ahbd_cur_away', 'ahbd_cur_handicap', 'ahbd_cur_handicap_text', 'ahbd_cur_home',
    'ahbd_open_away', 'ahbd_open_handicap', 'ahbd_open_handicap_text', 'ahbd_open_home',
    'ahbd_open_push',
    # 北单「过」盘口赢盘概率 (命中列 上/下 方向来源, 见 backfill_bd_probs.py)
    'ahbd_away_covers_prob', 'ahbd_home_covers_prob', 'ahbd_pred_desc', 'ahbd_push_prob',
    'away_team', 'beidan_no', 'best_value', 'comparison', 'date', 'event', 'fid', 'hit',
    'home_team', 'importance_weight', 'jingcai_no', 'lgbm_confidence', 'lgbm_draw',
    'lgbm_loss', 'lgbm_prediction', 'lgbm_win', 'low_priority', 'match_time',
    'model_draw', 'model_loss', 'model_prediction', 'model_win', 'odds_win',
    'pin_comparison', 'postponed', 'score', 'similar_matches', 'source',
    'total_goals_top3', 'ts_draw', 'ts_loss', 'ts_win', 'warning',
}

# similar_matches 子项: script.js 只用 home_team/away_team/score/similarity/similarity_raw
KEEP_SIM = {'home_team', 'away_team', 'score', 'similarity', 'similarity_raw'}

# 时间窗口: 看板只列最近 14 天日期, 全量 matches 会无限增长 (2026-10-28: 7180 场=18MB, 手机加载卡死)
WINDOW_DAYS = int(os.environ.get('LIGHT_DAYS', '30'))
# similar_matches 只展示前 3 条 (script.js renderSimilarMatches 内 Math.min(...,3))
MAX_SIM = 3

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
            v = [slim_similar(s) for s in v[:MAX_SIM]]
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
    all_matches = data.get('matches', [])
    dates = sorted({m.get('date') for m in all_matches if m.get('date')})
    keep_dates = set(dates[-WINDOW_DAYS:]) if WINDOW_DAYS > 0 else set(dates)
    trimmed = [m for m in all_matches if m.get('date') in keep_dates]
    light['matches'] = [slim_match(m) for m in trimmed]

    # 表头统计按窗口重算, 避免「7180 场」与实际行数不一致
    scored = [m for m in trimmed if m.get('score') and m.get('hit')]
    hit_n = sum(1 for m in scored if '\u2713' in (m.get('hit') or ''))
    light['total_matches'] = len(trimmed)
    light['hit_count'] = hit_n
    light['total_scored'] = len(scored)
    light['hit_rate'] = round(hit_n / len(scored), 4) if scored else 0
    light['daily_stats'] = [x for x in data.get('daily_stats', []) if x.get('date') in keep_dates]
    light['window_days'] = WINDOW_DAYS
    if keep_dates:
        light['date_range'] = f'{min(keep_dates)} ~ {max(keep_dates)}'

    with open(DST, 'w', encoding='utf-8') as f:
        json.dump(light, f, ensure_ascii=False, separators=(',', ':'))

    light_size = os.path.getsize(DST)
    print(f'[gen_light] {full_size/1e6:.1f}MB → {light_size/1e6:.1f}MB '
          f'({(1-light_size/full_size)*100:.0f}%↓) → {DST}')


if __name__ == '__main__':
    main()
