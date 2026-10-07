#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""队名简繁「彻底统一」——把所有存储层的队名收敛到唯一形态（繁体正字）。

为什么需要：不同来源/不同月份写进库的队名有简体残留（`智利天主大学`），
也有 `opencc s2t` 过度转换的異体残留（`蘇裏南`/`費爾幹納`）。两者会让同一支球队
在库里裂成 2~3 个名字：λ 取数、相似度历史、阵容参考都会因此少算一截。

统一规则（canonical = 繁体正字，与现有入口 `s2t` 口径一致，但可逆）：
    canon(x) = s2t(t2s(x))
  · 真简体   `智利天主大学` → t2s 不变 → s2t   → `智利天主大學`   ✅
  · 繁体正字 `瓦爾貝里`     → t2s `瓦尔贝里` → s2t → `瓦爾貝里`   ✅ 原样（不会被 s2t 改成 `瓦爾貝裏`）
  · 过转残留 `蘇裏南`       → t2s `苏里南`   → s2t → `蘇里南`     ✅ 还原正字
  · 英文/数字名            原样

层序（**先库后入口**，顺序反了 λ 会崩）：DB 7 表 → 日档 → `results.json` → 轻量库/派生元数据。

唯一键冲突：改名后若与既有行撞 PK，先删「信息更少/更旧」的那一行再 UPDATE。
    match_tech_stats PK(home_team,away_team,date)
    team_stats_cache PK(team_name,league,stat_type)
    poisson_predictions 无唯一键，但按 (date,home_team,away_team) 做逻辑去重

用法：
    python3 scripts/unify_team_names.py                 # dry-run，只打改动计划
    python3 scripts/unify_team_names.py --verbose       # 附带逐条改名样例
    python3 scripts/unify_team_names.py --apply         # 写盘（自动备份 DB 到 ~/db_backups/）
    python3 scripts/unify_team_names.py --apply --skip-files   # 只动 DB
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'football.db')
RESULTS = os.path.join(ROOT, 'docs', 'data', 'results.json')
BACKUP_DIR = os.path.expanduser('~/db_backups')

try:
    from opencc import OpenCC
    _S2T, _T2S = OpenCC('s2t'), OpenCC('t2s')
except Exception as e:                                    # pragma: no cover
    print(f'[FATAL] opencc 不可用: {e}')
    sys.exit(2)


def canon(name):
    """唯一形态：繁体正字。空值原样返回。"""
    if not isinstance(name, str) or not name.strip():
        return name
    return _S2T.convert(_T2S.convert(name))


# ---------------------------------------------------------------- DB 层
# (表, [队名列], 逻辑唯一键或 None, 删重时保留优先级列)
DB_TABLES = [
    ('match_analysis',      ['home_team', 'away_team'], None, ()),
    ('xg_features',         ['home_team', 'away_team'], None, ()),
    ('match_formations',    ['home_team', 'away_team'], None, ()),
    ('match_lineups',       ['team'],                   None, ()),
    ('match_tech_stats',    ['home_team', 'away_team'], ('home_team', 'away_team', 'date'), ('sid',)),
    ('team_stats_cache',    ['team_name'],              ('team_name', 'league', 'stat_type'), ('updated_at',)),
    ('poisson_predictions', ['home_team', 'away_team'], ('date', 'home_team', 'away_team'),
                            ('actual_outcome', 'reference_score', 'id')),
]


def db_rows(cur, table, cols, uniq=None):
    """取 rowid + 队名列 + 逻辑唯一键里用到的非队名列（漏取会让键变 None → 假撞键）。"""
    sel_cols = list(cols)
    for c in (uniq or ()):
        if c not in sel_cols:
            sel_cols.append(c)
    sel = ', '.join(['rowid'] + [f'"{c}"' for c in sel_cols])
    return [dict(zip(['rowid'] + sel_cols, r)) for r in cur.execute(f'SELECT {sel} FROM {table}')]


def plan_db(con, verbose=False):
    """返回 {table: {'updates': [(rowid, col, old, new)], 'dupes': [rowid_to_delete], 'rows': n}}"""
    cur = con.cursor()
    plan = {}
    for table, cols, uniq, keep_cols in DB_TABLES:
        rows = db_rows(cur, table, cols, uniq)
        updates, groups, dupes, preexisting = [], {}, [], []
        for r in rows:
            new_key, raw_key = [], []
            for c in cols:
                v = r.get(c)
                nv = canon(v) if isinstance(v, str) else v
                new_key.append(nv)
                raw_key.append(v)
                if nv != v:
                    updates.append((r['rowid'], c, v, nv))
            if uniq:
                key_vals, raw_vals = [], []
                for uc in uniq:
                    if uc in cols:
                        i = cols.index(uc)
                        key_vals.append(new_key[i])
                        raw_vals.append(raw_key[i])
                    else:
                        key_vals.append(r.get(uc))
                        raw_vals.append(r.get(uc))
                k = tuple(key_vals)
                score = tuple(0 if r.get(kc) in (None, '', 0) else 1 for kc in keep_cols)
                groups.setdefault(k, []).append((r['rowid'], score, tuple(raw_vals)))
        # 只在「改名导致的撞键」里删重；改名前后**本来就重复**的行属既有问题，不在本次范围（只报告）
        for k, members in groups.items():
            if len(members) < 2:
                continue
            if len({m[2] for m in members}) == 1:
                preexisting.append(members)
                continue
            best = max(members, key=lambda m: m[1])
            for m in members:
                if m[0] != best[0]:
                    dupes.append(m[0])
        plan[table] = {'updates': updates, 'dupes': dupes, 'rows': len(rows),
                       'preexisting': preexisting}
    return plan


# ---------------------------------------------------------------- 文件层
FILE_FIELDS = ('home_team', 'away_team')


def _sniff_style(text):
    """嗅探原文件格式：(是否紧凑单行, 是否带空格分隔符, 是否以换行结尾)。

    坑: 本仓 data/*.json 与 docs/data/results.json 是**紧凑单行且无结尾换行**
    （生产端 separators=(',',':')），results_light.json 之类是 indent=2。
    写盘前不还原这三项，改名这种小改动会变成整文件 46MB→51MB 重写。
    """
    compact = '\n' not in text.strip()
    head = text[:400]
    spaced = '": ' in head or ', "' in head
    return compact, spaced, text.endswith('\n')


def _dump(path, obj, original_text):
    """严格按原文件格式写回（紧凑/分隔符/结尾换行），避免无意义全文件重写。"""
    compact, spaced, nl = _sniff_style(original_text)
    with open(path, 'w', encoding='utf-8') as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False,
                      **({} if spaced else {'separators': (',', ':')}))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        if nl:
            f.write('\n')


def iter_json_matches(path):
    obj = json.load(open(path, encoding='utf-8'))
    ms = obj.get('matches') if isinstance(obj, dict) else obj
    return obj, (ms or [])


def plan_files():
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'matches_*.json')))
    files += sorted(glob.glob(os.path.join(ROOT, 'docs', 'data', 'results.json')))
    out = []
    for p in files:
        obj, ms = iter_json_matches(p)
        changes = 0
        samples = []
        for m in ms:
            if not isinstance(m, dict):
                continue
            for c in FILE_FIELDS:
                v = m.get(c)
                if isinstance(v, str) and canon(v) != v:
                    changes += 1
                    if len(samples) < 5:
                        samples.append(f'{c}: {v} → {canon(v)}')
        if changes:
            out.append({'path': p, 'changes': changes, 'rows': len(ms), 'samples': samples})
    return out


def apply_files(verbose=False):
    total = 0
    for p in sorted(glob.glob(os.path.join(ROOT, 'data', 'matches_*.json'))) + [RESULTS]:
        raw = open(p, encoding='utf-8').read()
        obj, ms = iter_json_matches(p)
        n = 0
        for m in ms:
            if not isinstance(m, dict):
                continue
            for c in FILE_FIELDS:
                v = m.get(c)
                if isinstance(v, str) and canon(v) != v:
                    if verbose and n < 3:
                        print(f'      {os.path.basename(p)} {c}: {v} → {canon(v)}')
                    m[c] = canon(v)
                    n += 1
        if n:
            _dump(p, obj, raw)
            total += n
            print(f'  ✔ {os.path.relpath(p, ROOT):48s} 改 {n} 处')
    return total


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='写盘（默认只 dry-run）')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--skip-files', action='store_true', help='只处理 DB')
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f'[FATAL] 找不到 {DB_PATH}')
        sys.exit(2)

    print('=' * 72)
    print('队名统一（canon = s2t(t2s(x))，繁体正字）  ' + ('APPLY' if args.apply else 'DRY-RUN'))
    print('=' * 72)

    # ---- 1) DB
    con = sqlite3.connect(DB_PATH)
    plan = plan_db(con, args.verbose)
    total_upd = sum(len(v['updates']) for v in plan.values())
    total_dup = sum(len(v['dupes']) for v in plan.values())
    print(f'\n[1/3] DB {os.path.relpath(DB_PATH, ROOT)}')
    for t, v in plan.items():
        if v['updates'] or v['dupes'] or v['preexisting']:
            pre = sum(len(m) - 1 for m in v['preexisting'])
            print(f'  {t:22s} 行={v["rows"]:7d}  改名={len(v["updates"]):6d}  '
                  f'改名致撞键删重={len(v["dupes"]):4d}  '
                  f'（既有重复组={len(v["preexisting"]):3d} 多{pre:4d}行·不在本次范围）')
            if args.verbose:
                for rid, c, o, n in v['updates'][:3]:
                    print(f'       rowid={rid} {c}: {o} → {n}')
    print(f'  小计: 改名 {total_upd} 处 / 改名致撞键删重 {total_dup} 行')

    if args.apply:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        bak = os.path.join(BACKUP_DIR, f'football_{time.strftime("%Y%m%d_%H%M%S")}.db')
        shutil.copy2(DB_PATH, bak)
        print(f'  备份 → {bak}')
        cur = con.cursor()
        for t, v in plan.items():
            if not (v['updates'] or v['dupes']):
                continue
            for rid in v['dupes']:
                cur.execute(f'DELETE FROM {t} WHERE rowid=?', (rid,))
            for rid, c, _o, n in v['updates']:
                cur.execute(f'UPDATE {t} SET "{c}"=? WHERE rowid=?', (n, rid))
            print(f'    {t}: 删 {len(v["dupes"])} / 改 {len(v["updates"])}')
        con.commit()

    # ---- 2) 文件
    if not args.skip_files:
        print('\n[2/3] 日档 + results.json')
        fp = plan_files()
        if not fp:
            print('  （无改动）')
        for f in fp:
            print(f'  {os.path.relpath(f["path"], ROOT):48s} 改 {f["changes"]:6d} / {f["rows"]} 行')
            if args.verbose:
                for s in f['samples']:
                    print(f'       {s}')
        if args.apply:
            print('  写盘中…')
            n = apply_files(args.verbose)
            print(f'  文件共改 {n} 处')

    # ---- 3) 后续
    print('\n[3/3] 后续必跑')
    print('  python3 scripts/refresh_results_meta.py --apply     # 重算派生元数据')
    print('  python3 scripts/gen_light_results.py                # 派生轻量库')
    print('  python3 scripts/update_lambdas.py                   # λ 按新名重算（重命名后必跑）')

    # 残留自检
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    rows_left = []
    for table, cols, _u, _k in DB_TABLES:
        for c in cols:
            for (v,) in cur2.execute(f'SELECT DISTINCT "{c}" FROM {table} WHERE "{c}" IS NOT NULL'):
                if isinstance(v, str) and canon(v) != v:
                    rows_left.append((table, c, v, canon(v)))
    print(f'\n自检：DB 仍非规范形态的队名 {len(rows_left)} 个')
    for t, c, v, n in rows_left[:10]:
        print(f'  {t}.{c}: {v} → 应为 {n}')
    if not args.apply:
        print('\n（dry-run 结束；加 --apply 写盘）')


if __name__ == '__main__':
    main()
