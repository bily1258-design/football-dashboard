#!/usr/bin/env python3
"""从 1x2d.titan007.com/{sid}.js 回补历史开盘赔率（Pinnacle/HKJC）到 poisson_predictions。

1x2d.js 每家公司格式:
  "177|timestamp|Pinnacle|初盘w|初盘d|初盘l|初盘prob_w|初盘prob_d|初盘prob_l|初盘返还|终盘w|终盘d|终盘l|终盘prob_w|终盘prob_d|终盘prob_l|终盘返还|凯利w|凯利d|凯利l|时间|地区|..."
字段索引: 0=公司id, 2=公司名, 3,4,5=初盘, 9=初盘返还, 10,11,12=终盘, 16=终盘返还, 17,18,19=凯利

断点续传: 只处理 pinnacle_open_w IS NULL 或 <=1.01 的场次。
"""
import sqlite3, os, re, sys, time, json, urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')
PROGRESS_FILE = os.path.join(PROJECT_DIR, 'data', 'cache', 'backfill_progress.json')

PIN_ID = '177'
HK_ID = '432'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.titan007.com/'}
DELAY = 0.35  # 限速，避免封IP

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 确保列存在
cols = [c[1] for c in cur.execute('PRAGMA table_info(poisson_predictions)').fetchall()]
for name in ('pinnacle_open_w', 'pinnacle_open_d', 'pinnacle_open_l',
             'hkjc_open_w', 'hkjc_open_d', 'hkjc_open_l'):
    if name not in cols:
        cur.execute(f'ALTER TABLE poisson_predictions ADD COLUMN {name} REAL')
conn.commit()


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return json.load(open(PROGRESS_FILE))
        except Exception:
            return {}
    return {}


def save_progress(d):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    json.dump(d, open(PROGRESS_FILE, 'w'))


def fetch_1x2d(sid):
    url = f'https://1x2d.titan007.com/{sid}.js'
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8-sig', errors='replace')
    i = raw.find('var game=')
    if i < 0:
        return None
    seg = raw[i:raw.find(';', i)]
    items = re.findall(r'"([^"]+)"', seg)
    result = {}
    for it in items:
        p = it.split('|')
        if len(p) < 20:
            continue
        cid = p[0]
        try:
            o = (float(p[3]), float(p[4]), float(p[5]))
            c = (float(p[10]), float(p[11]), float(p[12]))
        except (ValueError, IndexError):
            continue
        if min(o) > 1.01 and min(c) > 1.01:
            result[cid] = {'open': o, 'close': c}
    return result


def parse_odds(v):
    """(w,d,l) -> 三独立值或 None"""
    if v is None:
        return (None, None, None)
    return v


def main():
    # 目标: 有 close 但缺 open 的场次（断点续传基于 progress 文件）
    progress = load_progress()
    rows = cur.execute("""
        SELECT match_id, home_team, away_team, date FROM poisson_predictions
        WHERE pinnacle_close_w > 1.01
          AND (pinnacle_open_w IS NULL OR pinnacle_open_w < 1.01)
        ORDER BY date DESC
    """).fetchall()
    todo = [r for r in rows if str(r[0]) not in progress]
    print(f'待回补: {len(todo)} 场 (总缺open {len(rows)}, 已完成 {len(progress)})', flush=True)

    ok_pin = ok_hk = fail = 0
    t0 = time.time()
    for idx, (sid, h, a, d) in enumerate(todo):
        try:
            data = fetch_1x2d(sid)
            if data:
                pin = data.get(PIN_ID)
                hk = data.get(HK_ID)
                if pin:
                    cur.execute('UPDATE poisson_predictions SET pinnacle_open_w=?, pinnacle_open_d=?, pinnacle_open_l=? WHERE match_id=?',
                                (*pin['open'], str(sid)))
                    ok_pin += 1
                if hk:
                    cur.execute('UPDATE poisson_predictions SET hkjc_open_w=?, hkjc_open_d=?, hkjc_open_l=? WHERE match_id=?',
                                (*hk['open'], str(sid)))
                    ok_hk += 1
                if pin or hk:
                    conn.commit()
            else:
                fail += 1
            progress[str(sid)] = 'ok' if data else 'empty'
        except Exception as e:
            fail += 1
            progress[str(sid)] = f'err:{e}'
        if (idx + 1) % 50 == 0:
            save_progress(progress)
            el = time.time() - t0
            print(f'  进度 {idx+1}/{len(todo)} | Pin+{ok_pin} HK+{ok_hk} fail={fail} | '
                  f'{el:.0f}s ({el/max(idx+1,1):.2f}s/场)', flush=True)
            conn.commit()
        time.sleep(DELAY)

    save_progress(progress)
    # 最终统计
    total = cur.execute('SELECT COUNT(*) FROM poisson_predictions').fetchone()[0]
    pin_open = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE pinnacle_open_w > 1.01').fetchone()[0]
    hk_open = cur.execute('SELECT COUNT(*) FROM poisson_predictions WHERE hkjc_open_w > 1.01').fetchone()[0]
    print(f'\n完成: 本轮 Pin+{ok_pin} HK+{ok_hk} fail={fail}')
    print(f'DB {total} 场: Pinnacle open {pin_open} ({pin_open/total:.1%}), HKJC open {hk_open} ({hk_open/total:.1%})')
    conn.close()


if __name__ == '__main__':
    main()
