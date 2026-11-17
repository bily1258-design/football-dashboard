#!/usr/bin/env python3
"""backfill_h2h_consistency.py — 一次性回填历史 h2h_consistency 影子标记。

口径与 scripts/ai_analysis.py::_h2h_consistency_tag 完全一致, 直接调用,
避免两份逻辑漂移。仅写展示层字段, 不动 ah_*/星级/账本。
用法: python3 scripts/backfill_h2h_consistency.py [--dry-run]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import ai_analysis as A  # noqa: E402

RP = os.path.join(ROOT, 'docs', 'data', 'results.json')
DRY = '--dry-run' in sys.argv


def main():
    with open(RP, encoding='utf-8') as f:
        data = json.load(f)
    matches = data.get('matches', [])
    dist = {}
    changed = 0
    for m in matches:
        tag = A._h2h_consistency_tag(m)
        if m.get('h2h_consistency') != tag:
            changed += 1
        m['h2h_consistency'] = tag
        dist[tag] = dist.get(tag, 0) + 1
    print(f'总场次: {len(matches)}  变动: {changed}')
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f'  {k}: {v}')
    if DRY:
        print('dry-run, 未落盘')
        return
    tmp = RP + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, RP)
    print(f'已写入 {RP} ({os.path.getsize(RP)/1e6:.1f}MB)')


if __name__ == '__main__':
    main()
