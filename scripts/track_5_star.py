#!/usr/bin/env python3
"""跟踪 2026-08-16/17 客客客★ 5场 (验证避雷可忽略判断)
场次: 沃特福德vs南安普敦 20:30 | 沙佩科恩斯vs巴伊亞 22:00 | 卡爾馬vs哈馬比 22:30 | 巴蒂卡vs莫斯科斯巴達 00:30 | 米拉索vs弗拉門戈 05:30
"""
import sys, os, re, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_zqdc import get_scores_from_over_page

MATCHES = [
    # (日期, 主队关键词列表, 客队关键词列表, 显示名, HKJC客赔, LGBM客, TS平)
    ('2026-08-16', ['沃特福德'], ['南安普敦'], '沃特福德 vs 南安普敦', 1.96, 0.36, 0.22),
    ('2026-08-16', ['沙佩科恩斯'], ['巴伊亞', '巴伊亚'], '沙佩科恩斯 vs 巴伊亞', 1.90, 0.35, 0.23),
    ('2026-08-16', ['卡爾馬', '卡尔马'], ['哈馬比', '哈马比'], '卡爾馬 vs 哈馬比', 1.61, 0.38, 0.23),
    ('2026-08-17', ['巴蒂卡', '巴尔提卡'], ['莫斯科斯巴達', '莫斯科斯巴达', '斯巴達'], '巴蒂卡 vs 莫斯科斯巴達', 1.90, 0.38, 0.22),
    ('2026-08-17', ['米拉索'], ['弗拉門戈', '弗拉门戈'], '米拉索 vs 弗拉門戈', 1.67, 0.38, 0.24),
]

def find_score(date_str, home_kw, away_kw, scores_map):
    """在 {队名: score} 映射里按关键词找比分; titan007 队名是简体"""
    for (h, a), sc in scores_map.items():
        hh = str(h); aa = str(a)
        if any(k in hh for k in home_kw) and any(k in aa for k in away_kw):
            return sc
        # 反向: 主客颠倒的排除
    return None

def main():
    print('=== 客客客★ 5场跟踪 (避雷可忽略验证) ===')
    print(f'查询时间: {__import__("datetime").datetime.now().strftime("%m-%d %H:%M")}\n')
    all_scores = {}
    for date_str in ('2026-08-16', '2026-08-17'):
        try:
            s = get_scores_from_over_page(date_str)
            all_scores[date_str] = s
            print(f'{date_str}: Over页解析 {len(s)} 场')
        except Exception as e:
            all_scores[date_str] = {}
            print(f'{date_str}: 解析失败 {e}')
    print()
    hit = 0
    pending = 0
    for date_str, hk, ak, name, odds, lgbm, tsd in MATCHES:
        sc = find_score(date_str, hk, ak, all_scores.get(date_str, {}))
        if sc is None:
            print(f'⏳ {name} | 无比分(未开赛或未收录)')
            pending += 1
            continue
        try:
            h, a = sc.split('-')
            h, a = int(h.strip()), int(a.strip())
        except Exception:
            print(f'? {name} | 比分异常 {sc!r}')
            pending += 1
            continue
        win = h < a
        hit += win
        mark = '✅客胜命中' if win else ('❌' + ('主胜' if h > a else '平局'))
        print(f"{'✅' if win else '❌'} {name} | {sc} | {mark} | 客赔{odds} LGBM{lgbm*100:.0f}% TS平{tsd*100:.0f}%")
    print()
    done = len(MATCHES) - pending
    if done:
        print(f'已完赛 {done}/{len(MATCHES)}, 客胜命中 {hit}/{done} ({hit/done*100:.0f}%)')
    if pending:
        print(f'未开赛 {pending} 场: 稍后再查')

if __name__ == '__main__':
    main()
