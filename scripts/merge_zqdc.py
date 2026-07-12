#!/usr/bin/env python3
"""从 500.com zqdc 页面提取比赛并合并到 results.json

用法: python3 scripts/merge_zqdc.py --date 2026-07-11 [--update]
"""
import re
import json
import argparse
import urllib.request
from datetime import datetime

# 已知的期号映射（可扩展）
PERIOD_MAP = {
    '2026-07-03': '26072',
    '2026-07-08': '26073',
    '2026-07-10': '26074',
}

def find_period_for_date(date_str):
    """找到包含指定日期的期号"""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    day_of_year = d.timetuple().tm_yday
    
    # 26072 = 七月第1期 (07-03开始)
    # 每期大约3-5天
    # 26072 覆盖 07-03~07-07 (day 184-188)
    # 26073 覆盖 07-08~07-10 (day 189-191)
    # 26074 覆盖 07-10~07-14 (day 191-195)
    
    result = '26072'  # 默认最早的一期
    for period_start, period_num in sorted(PERIOD_MAP.items()):
        ps = datetime.strptime(period_start, '%Y-%m-%d')
        if d >= ps:
            result = period_num
    
    # 如果不在已知映射里，尝试从26074推算
    # 26074 开始于 07-10 (day 191), 26073 开始于 07-08 (day 189)
    # 每个期号大约增1对应~2天
    base_days = 191  # 26074 的起始日是 day 191
    base_period = 26074
    diff_days = day_of_year - base_days
    estimated = base_period + diff_days // 3  # 约3天一个期号
    estimated = max(26072, min(estimated, 26100))
    return str(estimated)


def fetch_zqdc_page(period):
    """抓取 zqdc 页面"""
    url = f'https://live.500.com/zqdc.php?e={period}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://live.500.com/',
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return raw.decode('gbk', errors='replace')


def parse_zqdc_page(text, target_date):
    """解析zqdc页面，提取指定日期的比赛"""
    target_md = target_date[5:10]  # MM-DD
    
    # 提取 odds list
    odds_m = re.search(r'var liveOddsList\s*=\s*(\{.*?\});', text, re.DOTALL)
    odds_data = json.loads(odds_m.group(1)) if odds_m else {}
    
    # 提取所有匹配行
    tr_pattern = re.compile(r'<tr[^>]*id="a(\d+)"[^>]*status="(\d+)"[^>]*gy="([^"]*)"[^>]*yy="([^"]*)"[^>]*>.*?</tr>', re.DOTALL)
    
    matches = []
    for fid, status, gy, yy in tr_pattern.findall(text):
        row_m = re.search(rf'<tr[^>]*id="a{fid}"[^>]*status="{status}"[^>]*>.*?</tr>', text, re.DOTALL)
        row = row_m.group(0) if row_m else ''
        
        # 时间
        t_m = re.search(r'(\d{2}-\d{2}\s+\d{2}:\d{2})', row)
        t = t_m.group(1) if t_m else '??'
        
        if not t.startswith(target_md):
            continue
        
        parts = gy.split(',')
        league = parts[0] if len(parts) > 0 else '?'
        home = parts[1] if len(parts) > 1 else '?'
        away = parts[2] if len(parts) > 2 else '?'
        
        # 比分
        s_m = re.search(r'class="pk">.*?<a[^>]*>(\d+)</a>\s*<span>-</span>\s*<a[^>]*>(\d+)</a>', row)
        score = f"{s_m.group(1)}-{s_m.group(2)}" if s_m else ''
        
        # 半场比分
        ht_m = re.search(r'<td[^>]*align="center"[^>]*class="red"[^>]*>(\d+\s*-\s*\d+)</td>', row)
        ht = ht_m.group(1).replace(' ', '') if ht_m else ''
        
        # 结果
        result_m = re.search(r'<td[^>]*align="center"[^>]*class="red"[^>]*>([胜负平]+)<', row)
        result = result_m.group(1) if result_m else ''
        
        # 赔率数据
        std_odds = odds_data.get(fid, {})
        avg_odds = std_odds.get('0', ['', '', ''])
        
        # 竞彩赔率 (key '3')
        jc_odds = std_odds.get('3', ['', '', ''])
        
        odds_w = avg_odds[0] if len(avg_odds) > 0 and avg_odds[0] else (jc_odds[0] if len(jc_odds) > 0 else '')
        odds_d = avg_odds[1] if len(avg_odds) > 1 and avg_odds[1] else (jc_odds[1] if len(jc_odds) > 1 else '')
        odds_l = avg_odds[2] if len(avg_odds) > 2 and avg_odds[2] else (jc_odds[2] if len(jc_odds) > 2 else '')
        
        kickoff_time = f"{target_date} {t[6:]}"
        
        matches.append({
            'fid': int(fid),
            'date': target_date,
            'league': league,
            'home': home,
            'away': away,
            'kickoff': kickoff_time,
            'score': score,
            'ht_score': ht,
            'result': result,
            'odds_w': float(odds_w) if odds_w else None,
            'odds_d': float(odds_d) if odds_d else None,
            'odds_l': float(odds_l) if odds_l else None,
            'source': 'beidan'
        })
    
    return matches


def format_as_dashboard_record(m):
    """将zqdc比赛格式化为dashboard记录格式"""
    w = m['odds_w']
    d_val = m['odds_d']
    l_val = m['odds_l']
    
    odds = {'w': w, 'd': d_val, 'l': l_val} if w else None
    
    # 从赔率计算市场隐含概率
    prob = None
    if w and d_val and l_val:
        margin = 1/w + 1/d_val + 1/l_val
        prob = {
            'w': round(1/w/margin, 3),
            'd': round(1/d_val/margin, 3),
            'l': round(1/l_val/margin, 3)
        }
    
    # 基准预测（使用最高概率方向）
    prediction = ''
    prediction_prob = 0
    if prob:
        max_dir = max(prob, key=prob.get)
        prediction = {'w': '主胜', 'd': '平局', 'l': '客胜'}.get(max_dir, '')
        prediction_prob = prob[max_dir]
    
    # 赛果判定
    result_type = ''
    if m['score']:
        parts = m['score'].split('-')
        if len(parts) == 2:
            h, a_val = int(parts[0]), int(parts[1])
            if h > a_val:
                result_type = '主胜'
            elif h < a_val:
                result_type = '客胜'
            else:
                result_type = '平局'
    
    return {
        'id': m['fid'],
        'date': m['date'],
        'league': m['league'],
        'home': m['home'],
        'away': m['away'],
        'kickoff': m['kickoff'],
        'prediction': prediction,
        'prediction_prob': prediction_prob,
        'odds': odds,
        'result': result_type,
        'score': m['score'],
        'source': 'beidan',
    }


def main():
    parser = argparse.ArgumentParser(description='合并zqdc北单数据')
    parser.add_argument('--date', default='2026-07-11', help='目标日期')
    parser.add_argument('--results', default='docs/data/results.json', help='results.json路径')
    parser.add_argument('--update', action='store_true', help='写入更新')
    args = parser.parse_args()
    
    # 尝试多个期号
    periods_to_try = [
        find_period_for_date(args.date),
        str(int(find_period_for_date(args.date)) + 1),
        str(int(find_period_for_date(args.date)) - 1),
    ]
    # 去重
    periods_to_try = list(dict.fromkeys(periods_to_try))
    
    all_matches = []
    for period in periods_to_try:
        try:
            text = fetch_zqdc_page(period)
            matches = parse_zqdc_page(text, args.date)
            all_matches.extend(matches)
            print(f"期号 {period}: 找到 {len(matches)} 场")
        except Exception as e:
            print(f"期号 {period}: 出错 {e}")
    
    # 去重（按fid）
    seen_fids = set()
    unique_matches = []
    for m in all_matches:
        if m['fid'] not in seen_fids:
            seen_fids.add(m['fid'])
            unique_matches.append(m)
    
    print(f"\n共 {len(unique_matches)} 场 {args.date} 的比赛")
    
    if not args.update:
        print("\n预览（--update 以写入）:")
        for m in sorted(unique_matches, key=lambda x: x['kickoff']):
            odds_str = f"{m['odds_w']:.2f}/{m['odds_d']:.2f}/{m['odds_l']:.2f}" if m['odds_w'] else "无赔率"
            print(f"  {m['kickoff'][-5:]} | {m['league']:12s} | {m['home']:18s} vs {m['away']:18s} | {m['score']:6s} | {odds_str}")
        return
    
    # 读取当前 results.json
    with open(args.results) as f:
        results = json.load(f)
    
    # 添加新比赛
    added = 0
    skipped = 0
    for m in unique_matches:
        record = format_as_dashboard_record(m)
        date = record['date']
        if date not in results['matches']:
            results['matches'][date] = []
        
        # 检查是否已存在（按id）
        existing_ids = {x.get('id') for x in results['matches'][date]}
        if record['id'] in existing_ids:
            skipped += 1
            continue
        
        results['matches'][date].append(record)
        added += 1
    
    # 按时间排序
    for date in results['matches']:
        results['matches'][date].sort(key=lambda x: x.get('kickoff', ''))
    
    # 更新日期列表
    all_dates = sorted(results['matches'].keys(), reverse=True)
    results['all_dates'] = all_dates
    
    with open(args.results, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 添加了 {added} 场，{skipped} 场已存在")


if __name__ == '__main__':
    main()
