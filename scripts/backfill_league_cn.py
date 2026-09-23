#!/usr/bin/env python3
"""backfill_league_cn.py — 把存量 JSON 里的联赛名归一化成中文

背景：HKJC 场次的联赛名来自 titan007 源，早期存的是英文代号（'SPL'、'NCAL Cup'…），
看板上显示成英文。本脚本用 titan007_utils._normalize_league（含 HKJC_LEAGUE_CN 表）
把存量数据里的联赛名刷成中文，并把同一联赛的不同中文写法统一。

用法:
  python3 scripts/backfill_league_cn.py --dry-run          # 只看会改哪些
  python3 scripts/backfill_league_cn.py                    # 改 docs/data/results.json + results_light.json
  python3 scripts/backfill_league_cn.py data/matches_hkjc_20260922.json ...
"""
import json, os, sys, collections, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from titan007_utils import _normalize_league

DEFAULT_FILES = [
    os.path.join(ROOT, 'docs', 'data', 'results.json'),
    os.path.join(ROOT, 'docs', 'data', 'results_light.json'),
]


def backfill_file(path, dry_run=False):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        matches = data.get('matches')
        holder = data
    else:
        matches = data
        holder = None
    if not isinstance(matches, list):
        print(f'[SKIP] {path} 无 matches 列表')
        return 0

    changed = collections.Counter()
    for m in matches:
        if not isinstance(m, dict):
            continue
        old = (m.get('event') or '').strip()
        new = _normalize_league(old)
        if new and new != old:
            changed[(old, new)] += 1
            if not dry_run:
                m['event'] = new

    # 2026-09-24: 源数据只有 event(联赛名), 存量 results.json 的 league 字段全空 →
    # 用（已归一化的）event 兜底填 league。只在原本就带 league 字段的文件里补（results_light 不含该字段, 不动）。
    filled = 0
    for m in matches:
        if not isinstance(m, dict) or 'league' not in m:
            continue
        if (m.get('league') or '').strip():
            continue
        lg = _normalize_league((m.get('event') or '').strip())
        if lg:
            filled += 1
            if not dry_run:
                m['league'] = lg

    if (changed or filled) and not dry_run:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(holder if holder is not None else matches, f,
                      ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)

    total = sum(changed.values())
    print(f'[{path}] 联赛名归一化 {total} 条 / league 兜底 {filled} 条' + (' (dry-run)' if dry_run else ''))
    for (o, n), c in changed.most_common():
        print(f'   {c:5d}  {o} → {n}')
    return total + filled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    files = args.files or DEFAULT_FILES
    grand = 0
    for p in files:
        if not os.path.exists(p):
            print(f'[MISS] {p}')
            continue
        grand += backfill_file(p, args.dry_run)
    print(f'合计改动 {grand} 条')


if __name__ == '__main__':
    main()
