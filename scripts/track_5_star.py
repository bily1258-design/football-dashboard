#!/usr/bin/env python3
"""跟踪 2026-08-16/17 客客客★ 5场 (验证避雷可忽略判断)
场次: 沃特福德vs南安普敦 20:30 | 沙佩科恩斯vs巴伊亞 22:00 | 卡爾馬vs哈馬比 22:30 | 巴蒂卡vs莫斯科斯巴達 00:30 | 米拉索vs弗拉門戈 05:30
数据源: bfdata_ut.js 实时 (主力) + Over_日期.htm 历史 (兜底)
"""
import sys, os, re, urllib.request, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_zqdc import fetch_bfdata, get_scores_from_over_page

MATCHES = [
    # (日期, 主队关键词, 客队关键词, 显示名, HKJC客赔, LGBM客, TS平)
    ('2026-08-16', ['沃特福德'], ['南安普敦'], '沃特福德 vs 南安普敦', 1.96, 0.36, 0.22),
    ('2026-08-16', ['沙佩科恩斯'], ['巴伊亞', '巴伊亚'], '沙佩科恩斯 vs 巴伊亞', 1.90, 0.35, 0.23),
    ('2026-08-16', ['卡爾馬', '卡尔马'], ['哈馬比', '哈马比'], '卡爾馬 vs 哈馬比', 1.61, 0.38, 0.23),
    ('2026-08-17', ['巴蒂卡', '巴尔提卡'], ['莫斯科斯巴達', '莫斯科斯巴达', '斯巴達'], '巴蒂卡 vs 莫斯科斯巴達', 1.90, 0.38, 0.22),
    ('2026-08-17', ['米拉索'], ['弗拉門戈', '弗拉门戈'], '米拉索 vs 弗拉門戈', 1.67, 0.38, 0.24),
]

def bfdata_scores():
    """从 bfdata_ut.js 提取已完场比分: {队名对: score}; 队名已转繁体"""
    out = {}
    for m in fetch_bfdata():
        f = m['fields']
        if len(f) <= 15:
            continue
        try:
            status = int(f[13])
            hs, aas = int(f[14]), int(f[15])
        except (ValueError, IndexError):
            continue
        if status in (-1, 3):  # 完场
            out[(m['hometeam'], m['awayteam'])] = f'{hs}-{aas}'
    return out

def over_scores(date_str):
    """Over 历史页: {(home, away): score}"""
    try:
        return get_scores_from_over_page(date_str)
    except Exception:
        return {}

def find(score_map, home_kw, away_kw):
    for (h, a), sc in score_map.items():
        hh, aa = str(h), str(a)
        if any(k in hh for k in home_kw) and any(k in aa for k in away_kw):
            return sc
    return None

def main():
    print('=== 客客客★ 5场跟踪 (避雷可忽略验证) ===')
    print(f'查询时间: {datetime.datetime.now().strftime("%m-%d %H:%M")}\n')
    srcs = {}
    try:
        srcs['bfdata'] = bfdata_scores()
        print(f'bfdata: {len(srcs["bfdata"])} 场完场')
    except Exception as e:
        srcs['bfdata'] = {}
        print(f'bfdata: 失败 {e}')
    for ds in ('2026-08-16', '2026-08-17'):
        srcs[ds] = over_scores(ds)
        print(f'Over_{ds}: {len(srcs[ds])} 场')
    print()
    hit, done = 0, 0
    for date_str, hk, ak, name, odds, lgbm, tsd in MATCHES:
        sc = find(srcs['bfdata'], hk, ak) or find(srcs.get(date_str, {}), hk, ak)
        if sc is None:
            print(f'⏳ {name} | 无比分(未开赛或未收录)')
            continue
        try:
            h, a = [int(x.strip()) for x in sc.split('-')]
        except Exception:
            print(f'? {name} | 比分异常 {sc!r}')
            continue
        done += 1
        win = h < a
        hit += win
        mark = '客胜命中' if win else ('主胜' if h > a else '平局')
        print(f"{'✅' if win else '❌'} {name} | {sc} | {mark} | 客赔{odds} LGBM{lgbm*100:.0f}% TS平{tsd*100:.0f}%")
    print()
    if done:
        print(f'已完赛 {done}/{len(MATCHES)}, 客胜命中 {hit}/{done} ({hit/done*100:.0f}%)')
    if done < len(MATCHES):
        print(f'未开赛 {len(MATCHES)-done} 场: 稍后再查')

if __name__ == '__main__':
    main()
