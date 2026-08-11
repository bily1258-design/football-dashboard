#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡高权重场次追踪 (⚡>=1.14 临场窗口记录)
- 每次管道运行调用: python3 scripts/high_weight_tracker.py
- 逻辑: results.json 中 importance_weight>=1.14 的场次, 按 fid 去重追加到
  docs/data/high_weight_track.json; 已记录场次用最新 score/hit 回填
- 目的: 验证「顶级联赛⚡1.2 vs 次级联赛⚡1.14」开出不规律 (需攒2-3周)
"""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')
TRACK = os.path.join(ROOT, 'docs', 'data', 'high_weight_track.json')
THRESHOLD = 1.14

def argmax3(w, dr, l):
    m = max(w, dr, l)
    return 0 if m == w else (1 if m == dr else 2)

def parse_score(s):
    """'2 - 1' / '2-1' / '2:1' → (2,1); 无法解析返回 None"""
    try:
        parts = str(s).replace('-', ':').split(':')
        return int(parts[0].strip()), int(parts[1].strip())
    except Exception:
        return None

DIR_CN = ['主', '平', '客']

def main():
    if not os.path.exists(RESULTS):
        print('✗ results.json 不存在')
        return 1
    with open(RESULTS, encoding='utf-8') as f:
        data = json.load(f)

    track = []
    if os.path.exists(TRACK):
        with open(TRACK, encoding='utf-8') as f:
            raw = json.load(f)
        # 兼容两种存储格式: 旧版为list, 新版为 {'tracked': [...]}
        track = raw.get('tracked', []) if isinstance(raw, dict) else raw
    by_fid = {t['fid']: t for t in track}

    now = datetime.now()
    added = updated = 0
    for m in data.get('matches', []):
        w = round(m.get('importance_weight', 0), 2)
        if w < THRESHOLD:
            continue
        fid = m.get('fid')
        if not fid:
            continue
        md = argmax3(m.get('model_win', 0), m.get('model_draw', 0), m.get('model_loss', 0))
        tsd = argmax3(m.get('ts_win', 0), m.get('ts_draw', 0), m.get('ts_loss', 0))
        sc = parse_score(m.get('score', ''))
        hit_flag = False
        if sc is not None:
            h, a = sc
            actual = 0 if h > a else (1 if h == a else 2)
            hit_flag = (md == actual)  # 模型方向是否命中
        entry = {
            'fid': fid,
            'date': m.get('date', ''),
            'match_time': m.get('match_time', ''),
            'event': m.get('event', '') or m.get('league', ''),
            'home_team': m.get('home_team', ''),
            'away_team': m.get('away_team', ''),
            'weight': w,
            'model_dir': DIR_CN[md],
            'ts_dir': DIR_CN[tsd],
            'same_dir': (md == tsd),
            'score': m.get('score', ''),
            'hit': hit_flag,
            'recorded_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        }
        if fid in by_fid:
            # 回填最新赛果
            old = by_fid[fid]
            if old.get('score') != entry['score'] or old.get('hit') != entry['hit']:
                old['score'] = entry['score']
                old['hit'] = entry['hit']
                old['updated_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
                updated += 1
        else:
            by_fid[fid] = entry
            added += 1

    out = sorted(by_fid.values(), key=lambda t: (t['date'], t['match_time']))
    with open(TRACK, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'), 'threshold': THRESHOLD,
                   'note': '⚡=importance_weight(联赛等级×紧迫). 历史场次紧迫系数跌至0.90, 仅临场窗口权重>=1.14. 逐轮记录攒样本.', 'tracked': out},
                  f, ensure_ascii=False, indent=2)

    # 汇总
    with_score = [t for t in out if t['score']]
    same = [t for t in with_score if t['same_dir']]
    same_hit = sum(1 for t in same if t['hit'])
    print(f'⚡追踪: 累计 {len(out)} 场 (本轮新增 {added}, 回填 {updated})')
    if same:
        print(f'  有赛果 {len(with_score)} 场 | 同向 {len(same)} 场中 {same_hit} ({same_hit/len(same)*100:.1f}%)')
    else:
        print(f'  有赛果 {len(with_score)} 场, 暂无同向样本')
    return 0

if __name__ == '__main__':
    sys.exit(main())
