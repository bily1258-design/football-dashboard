#!/usr/bin/env python3
"""sync_postponed_flags.py — 把日档里的「推迟」标记同步进 results.json。

背景: 「推迟」标记写在日档 (data/matches_*.json) 的 score 字段上, 而看板/清单读的是
results.json (docs/data/)。results.json 是拼装快照: 若标记是在上次全量重建之后才回填的,
库里那一行仍是空比分 → 看板比分位空白, 用户看不到「推迟」。

做法: 扫描所有日档, 收集 score=='推迟' 或 postponed 为真的记录 (按 fid), 再把这些
fid 在 results.json 里仍为空比分的行补上 score='推迟' + postponed=True。
只补空比分, 绝不覆盖已有比分; 无 fid 的跳过。

用法:
    python3 scripts/sync_postponed_flags.py            # dry-run (默认, 只报告)
    python3 scripts/sync_postponed_flags.py --apply    # 写盘
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')
DAY_GLOBS = ['data/matches_*.json']


def collect_marked():
    """日档里所有带推迟标记的 fid → 队名 (用于报告)."""
    marked = {}
    for pattern in DAY_GLOBS:
        for fp in sorted(glob.glob(os.path.join(ROOT, pattern))):
            try:
                with open(fp, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            matches = data.get('matches', data if isinstance(data, list) else [])
            for m in matches:
                score = (m.get('score') or '').strip()
                if score != '推迟' and not m.get('postponed'):
                    continue
                fid = str(m.get('fid') or '')
                if not fid:
                    continue
                marked.setdefault(fid, (m.get('home_team'), m.get('away_team'), score or 'postponed'))
    return marked


def main():
    apply = '--apply' in sys.argv
    marked = collect_marked()
    with open(RESULTS, encoding='utf-8') as f:
        data = json.load(f)
    matches = data.get('matches', [])

    fixed = []
    for m in matches:
        fid = str(m.get('fid') or '')
        if not fid or fid not in marked:
            continue
        if (m.get('score') or '').strip():
            continue  # 已有比分, 不动
        score, postponed = marked[fid][2], True
        m['score'] = '推迟' if score == '推迟' else '推迟'
        m['postponed'] = postponed
        fixed.append((fid, m.get('date', '')[:10], m.get('home_team'), m.get('away_team'), score))

    print(f"[SCAN] 日档带推迟标记 fid: {len(marked)} 个")
    print(f"[SYNC] results.json 需补标记的空比分行: {len(fixed)} 条")
    for row in fixed:
        print(f"   fid={row[0]} {row[1]} {row[2]}-{row[3]}  <- {row[4]}")

    if not fixed:
        print("[OK] 无需改动 (幂等)")
        return 0
    if not apply:
        print("[DRY-RUN] 未写盘; 加 --apply 生效")
        return 0

    data['matches'] = matches
    # 保持原紧凑单行格式 (历史教训: indent=2 会产生数万行空白 diff)
    with open(RESULTS, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"[WRITE] 已写回 {RESULTS}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
