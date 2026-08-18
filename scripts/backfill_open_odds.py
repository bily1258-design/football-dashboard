#!/usr/bin/env python3
"""回填开盘赔率到 poisson_predictions（从 matches_*.json 的 odds_*_open_* 字段）"""
import sqlite3, os, re, sys, json, glob

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')
DATA_DIR = os.path.join(PROJECT_DIR, 'data')


def _f(v):
    """容错转 float"""
    try:
        return float(v) if v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. 加列（幂等）
cols = [c[1] for c in cur.execute('PRAGMA table_info(poisson_predictions)').fetchall()]
new_cols = {
    'pinnacle_open_w': 'REAL',
    'pinnacle_open_d': 'REAL',
    'pinnacle_open_l': 'REAL',
    'hkjc_open_w': 'REAL',
    'hkjc_open_d': 'REAL',
    'hkjc_open_l': 'REAL',
}
added = []
for name, typ in new_cols.items():
    if name not in cols:
        cur.execute(f'ALTER TABLE poisson_predictions ADD COLUMN {name} {typ}')
        added.append(name)
conn.commit()
print(f'新增列: {added if added else "无（已存在）"}')

# 2. 从 json 回填
files = sorted(glob.glob(os.path.join(DATA_DIR, 'matches_2026*.json')))
print(f'JSON 文件: {len(files)} 个')

# 建立 fid -> (pin_open, hk_open) 映射
fid_map = {}
for f in files:
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        print(f'  跳过 {f}: {e}')
        continue
    ms = d['matches'] if isinstance(d, dict) else d
    for m in ms:
        fid = str(m.get('fid', ''))
        if not fid:
            continue
        pin = (
            _f(m.get('odds_pinnacle_open_win')),
            _f(m.get('odds_pinnacle_open_draw')),
            _f(m.get('odds_pinnacle_open_loss')),
        )
        hk = (
            _f(m.get('odds_hkjc_open_win')),
            _f(m.get('odds_hkjc_open_draw')),
            _f(m.get('odds_hkjc_open_loss')),
        )
        fid_map[fid] = (pin, hk)

print(f'JSON 中带 fid 的比赛: {len(fid_map)} 场')

# 3. 更新 DB
updated_pin = updated_hk = 0
for fid, (pin, hk) in fid_map.items():
    if pin[0] > 1.01:
        cur.execute('UPDATE poisson_predictions SET pinnacle_open_w=?, pinnacle_open_d=?, pinnacle_open_l=? WHERE match_id=?',
                    (*pin, fid))
        updated_pin += cur.rowcount
    if hk[0] > 1.01:
        cur.execute('UPDATE poisson_predictions SET hkjc_open_w=?, hkjc_open_d=?, hkjc_open_l=? WHERE match_id=?',
                    (*hk, fid))
        updated_hk += cur.rowcount

conn.commit()

# 4. 统计
total = cur.execute('SELECT COUNT(*) FROM poisson_predictions').fetchone()[0]
pin_all = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE pinnacle_open_w > 1.01').fetchone()[0]
hk_all = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE hkjc_open_w > 1.01').fetchone()[0]
pin_close = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE pinnacle_close_w > 1.01').fetchone()[0]
print(f'\n回填: Pinnacle open {updated_pin} 场, HKJC open {updated_hk} 场')
print(f'DB 总计 {total} 场:')
print(f'  Pinnacle open: {pin_all} ({pin_all/total:.1%})')
print(f'  HKJC open:     {hk_all} ({hk_all/total:.1%})')
print(f'  Pinnacle close: {pin_close} ({pin_close/total:.1%})')
conn.close()
