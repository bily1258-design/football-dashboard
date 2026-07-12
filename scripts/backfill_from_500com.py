#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_from_500com.py — 从500.com完场页批量回填历史赛果 v4
v4: 新增 fid 提取 + fid_500 写入DB，后续可用fid精准关联赛果/赔率

用法:
  python scripts/backfill_from_500com.py --db data/football.db
  python scripts/backfill_from_500com.py --db data/football.db --date 2026-06-23
  python scripts/backfill_from_500com.py --db data/football.db --last 7
  python scripts/backfill_from_500com.py --db data/football.db --date-from 2026-06-01 --date-to 2026-06-30
  python scripts/backfill_from_500com.py --db data/football.db --dry-run
  python scripts/backfill_from_500com.py --db data/football.db --fid-only  # 只回填fid，不更新赛果
"""

import os
import re
import sys
import sqlite3
import time
import argparse
import urllib.request
from datetime import datetime

# --- 复用 team_aliases ---
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK_DIR)
try:
    from team_aliases import canonical, match_key
except ImportError:
    def canonical(name): return name
    def match_key(home, away): return (home.strip(), away.strip())

# --- 500.com 额外别名（500.com特有译名 → DB标准名）---
_500COM_ALIASES = {
    # 芬兰
    'MP米凯利': 'MP米克力', 'SJK阿卡泰米阿': 'SJK学院',
    '塞那乔恩': '塞纳乔琪', '塞那乔其': '塞纳乔琪',
    '库普斯': '库奥皮奥', '古比斯': '库奥皮奥',
    '国际图尔库': '国际图尔', '图尔库国际': '国际图尔',
    'TPS土尔库': 'TPS图尔', '图尔库': '图尔库国际',
    '查路': '坦山猫', '查普斯': '查普斯', '哈卡': '哈卡',
    'EIF埃克纳斯': 'EIF埃克纳斯', '雅罗': '雅罗',
    '格尼斯坦': '格尼斯坦', '瓦萨': '瓦萨',
    # 芬兰 - 赫尔火花/埃尔维斯/塞伊奈的混乱映射
    # DB里: 坦山猫=埃尔维斯的别名, 赫尔火花=赫尔辛基的别名
    # 500.com: 赫尔辛基=HJK, 埃尔维斯=Elves
    '赫尔辛基': '赫尔火花',
    '埃尔维斯': '坦山猫',
    '塞伊奈约基': '塞伊奈', '塞那乔恩': '塞纳乔琪',
    # 波兰
    '华沙莱吉亚': '华沙军团', '摩托鲁宾': '莫托路宾',
    '波兹南莱赫': '波兹莱赫', '莱克普斯纳': '波兹莱赫',
    '乔治罗尼亚': '比亚韦', '什切青波贡': '什切青',
    '施切钦波贡': '什切青',  # 另一种写法
    'GKS卡托威斯': '卡托威斯', '卡杜华斯': '卡托威斯',
    '扎布热': '扎布热矿工', 'Zag ebie': '扎布热矿工',  # 编码问题
    '卢宾扎格列比': '卢宾', '卢宾': '卢宾',
    '维德祖罗兹': '维德祖罗兹',  # 直接匹配
    '格里维治': '格里维治',
    '华沙普洛克': '华沙普洛克',
    '拉多麦科': '拉多麦科',
    # 日本
    '名古屋鲸八': '名古屋鲸鱼', '长崎航海': '长崎成功丸',
    '清水鼓动': '清水心跳', '町田泽维亚': '町田泽维亚',
    # 韩国
    '坡州市民': '坡州前线', '清州FC': '忠北清州',
    # 巴西 - 巴甲用无后缀名
    '克里西乌马': '克里丘马', '塞阿拉': '塞阿拉',
    '福塔雷萨': '福塔雷萨', '隆德里纳': '隆德里纳',
    '尤文图德': '尤文图德',
    '博塔弗戈': '博塔弗戈',  # 巴甲直接叫博塔弗戈
    '格雷米奥': '格雷米奥', '科林蒂安': '科林蒂安',
    '巴伊亚': '巴伊亚', 'EC巴伊亚': '巴伊亚',
    '蓬塔格罗萨铁路': '铁路工人',
    # 挪威
    '兰赫姆': '兰黑姆', '桑内斯': '桑德尼斯',
    '斯特罗姆加斯特': '斯托姆加斯特',
    '桑德维肯斯': '桑德维根斯', '诺霍斯': '诺霍斯',
    '康斯文格': '康斯文格',
    # 瑞典
    '永斯基': '卢恩斯基尔', '厄勒布鲁': '奥雷布洛',
    '奥迪沃德': '奥迪沃特', '厄斯特松德': '厄斯特松德',
    '法尔肯堡': '法尔肯堡', '布莱格': '布莱格',
    '埃尔夫斯堡': '埃夫斯堡', '兰斯科罗纳': '兰斯科罗纳',
    '北欧联合': '北欧联FC', '阿西里斯卡': '北欧联FC',
    '厄格里特': '厄斯特松德',  # DB里厄格里特=厄斯特松德的别名
    # 冰岛
    'IBV韦斯文尼查': 'IBV韦斯特曼纳',
    '斯塔尔南': '斯塔尔南', '托尔阿克雷里': '托尔阿克雷里',
    # 爱尔兰
    '谢尔伯恩': '舒尔本', '沃特福德联合': '沃特联队',
    '沃特福德': '沃特联队',
    '科布漫步者': '科布漫步', '科布多西部': '科布漫步',
    # 奥地利
    '维也纳快速': '维快速', '里德': '里德',
    # 罗马尼亚
    '克卢日大学': '克卢日', '阿格斯': '阿格斯',
    '卡萨皮亚': '卡萨皮亚', '托伦斯': '托林斯',
    # 阿根廷
    '圣菲联': '圣菲联合', '独立队': '阿独立',
    '独立FBC': '阿独立', '飓风': '飓风队',
    '巴拉卡斯中央': '巴拉卡斯中央',
    # 以色列
    '贝尔谢巴夏普尔': '加尔达贝尔', '弗拉姆': '弗拉姆',
    # 英格兰
    '米德尔斯堡': '米堡', '赫尔城': '赫尔城',
    # 丹麦
    '阿晓斯费马': '奥胡斯费马', '奥尔堡': '奥尔堡',
    # 国际赛 - 国家队名在500.com可能有排名前缀
    '格鲁吉亚': '格鲁吉亚', '罗马尼亚': '罗马尼亚',
    '威尔士': '威尔士', '加纳': '加纳',
    '瑞典': '瑞典', '希腊': '希腊',
    '西班牙': '西班牙', '伊拉克': '伊拉克',
    '法国': '法国', '科特迪瓦': '科特迪瓦',
    '塞浦路斯': '塞浦路斯', '斯洛伐克': '斯洛文尼',
}


def canonical_500(name):
    """500.com队名 → DB标准名，处理排名前缀"""
    # 去掉排名前缀: [世19]1墨西哥 → 墨西哥
    cleaned = re.sub(r'^\[世\d+\]\d*', '', name).strip()
    if cleaned in _500COM_ALIASES:
        return _500COM_ALIASES[cleaned]
    return canonical(cleaned)


def team_match(db_home, db_away, r_home, r_away):
    """队名匹配：两边都归一化后比较"""
    ch = canonical_500(r_home)
    ca = canonical_500(r_away)
    dh = canonical(db_home)
    da = canonical(db_away)
    
    # 精确匹配
    if ch == dh and ca == da:
        return True
    # 包含匹配
    if (ch in dh or dh in ch) and (ca in da or da in ca):
        return True
    return False


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


def fetch_page(url, encoding='utf-8'):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode('gbk', errors='replace')
    except Exception as e:
        print(f'  ❌ 请求失败: {e}')
        return None


def parse_wanchang_html(html):
    """解析500.com完场页HTML — v4: 新增fid提取 + 修复比分解析
    
    v4修复：
    - 从 <tr id="a{fid}"> 提取fid
    - 优先从PK div(clt1/clt3)提取比分，避免亚盘盘口干扰
    - 世界杯等有亚盘的比赛，td[5]格式为"5两球/两球半0"，不含纯比分
    """
    results = []
    
    # 匹配带id属性的完整tr行
    full_trs = re.findall(r'<tr([^>]*)>(.*?)</tr>', html, re.S)
    
    for attrs, content in full_trs:
        # 提取fid: id="a1425100" → fid=1425100
        fid_match = re.search(r'id="a(\d+)"', attrs)
        fid = int(fid_match.group(1)) if fid_match else None
        
        tds = re.findall(r'<td[^>]*>(.*?)</td>', content, re.S)
        if len(tds) < 8:
            continue
        
        clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        
        if clean_tds[0] in ('赛事', '场次'):
            continue
        
        status = clean_tds[3] if len(clean_tds) > 3 else ''
        if status != '完':
            continue
        
        league = clean_tds[0]
        home_raw = clean_tds[4] if len(clean_tds) > 4 else ''
        away_raw = clean_tds[6] if len(clean_tds) > 6 else ''
        
        # 清理队名 - 去排名和编号
        home = re.sub(r'\[\d+\]', '', home_raw).strip()
        home = re.sub(r'^\d+', '', home).strip()
        away = re.sub(r'\[\d+\]', '', away_raw).strip()
        away = re.sub(r'\d+$', '', away).strip()
        # 去掉排名前缀如 [世19]1
        home = re.sub(r'^\[世\d+\]\d*', '', home).strip()
        away = re.sub(r'\d*\[世\d+\]$', '', away).strip()
        
        # === 比分提取（v4修复） ===
        # 方法1: 优先从PK div的clt1/clt3提取（最可靠）
        # 格式: <a class="clt1">5</a><a class="fhuise">盘口</a><a class="clt3">0</a>
        h_score = a_score = None
        pk_match = re.search(r'<div class="pk">(.*?)</div>', content, re.S)
        if pk_match:
            pk_html = pk_match.group(1)
            home_score_m = re.search(r'class="clt1"[^>]*>\s*(\d+)\s*<', pk_html)
            away_score_m = re.search(r'class="clt3"[^>]*>\s*(\d+)\s*<', pk_html)
            if home_score_m and away_score_m:
                h_score = int(home_score_m.group(1))
                a_score = int(away_score_m.group(1))
        
        # 方法2: 从td[5]提取（普通比赛格式 "3-2"）
        if h_score is None:
            score_raw = clean_tds[5] if len(clean_tds) > 5 else ''
            # 只匹配纯数字比分格式，避免匹配到含盘口的 "5两球0"
            m = re.match(r'^(\d+)\s*[-/]\s*(\d+)$', score_raw.replace(' ', ''))
            if m:
                h_score = int(m.group(1))
                a_score = int(m.group(2))
        
        if h_score is not None and a_score is not None:
            score = f'{h_score}-{a_score}'
            if h_score > a_score:
                outcome = f'主胜 {score}'
            elif h_score == a_score:
                outcome = f'平局 {score}'
            else:
                outcome = f'客胜 {score}'
            
            results.append({
                'source': 'wanchang',
                'fid': fid,  # v4: 500.com比赛ID
                'league': league,
                'home': home,
                'away': away,
                'score': score,
                'home_score': h_score,
                'away_score': a_score,
                'outcome': outcome,
            })
    
    return results


def fetch_wanchang_by_date(date_str):
    url = f'https://live.500.com/wanchang.php?e={date_str}'
    html = fetch_page(url, encoding='gbk')
    if not html:
        return []
    return parse_wanchang_html(html)


def backfill_db(results, db_path, dry_run=False, fid_only=False):
    """回填赛果和fid到DB
    
    Args:
        results: parse_wanchang_html 返回的结果列表
        db_path: 数据库路径
        dry_run: 只显示不写入
        fid_only: 只回填fid_500字段，不更新actual_outcome
    """
    if not results:
        return 0, 0, []
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if fid_only:
        # 只回填fid: 找所有fid_500为空的记录
        cursor.execute("""
            SELECT id, home_team, away_team, kickoff_time, fid_500
            FROM poisson_predictions
            WHERE fid_500 IS NULL
        """)
    else:
        cursor.execute("""
            SELECT id, home_team, away_team, kickoff_time, fid_500
            FROM poisson_predictions
            WHERE (actual_outcome IS NULL OR actual_outcome = '' OR actual_outcome NOT GLOB '*[0-9]-[0-9]*')
               OR fid_500 IS NULL
        """)
    db_records = [dict(r) for r in cursor.fetchall()]
    
    updated = 0
    fid_updated = 0
    details = []
    
    # 预建索引：500.com 完场结果按 fid 索引（精准匹配）
    by_fid = {}
    for res in results:
        if res.get('fid'):
            by_fid[res['fid']] = res
    # 未建索引的（没fid），留到队名模糊匹配
    unnamed = [r for r in results if not r.get('fid')]
    
    for rec in db_records:
        matched_res = None
        
        # 优先用 fid_500 精准匹配
        if rec.get('fid_500') and rec['fid_500'] in by_fid:
            matched_res = by_fid[rec['fid_500']]
        # 其次用队名模糊匹配（500.com有fid但DB还没写入的）
        elif by_fid:
            for fid, res in by_fid.items():
                if team_match(rec['home_team'], rec['away_team'], res['home'], res['away']):
                    matched_res = res
                    break
        # 最后用没fid的结果做队名匹配
        if matched_res is None and unnamed:
            for res in unnamed:
                if team_match(rec['home_team'], rec['away_team'], res['home'], res['away']):
                    matched_res = res
                    break
        
        if matched_res:
            if not dry_run:
                sets = []
                params = []
                
                # 写入fid（如果结果有fid且DB记录没有）
                if matched_res.get('fid') and not rec.get('fid_500'):
                    sets.append("fid_500 = ?")
                    params.append(matched_res['fid'])
                    fid_updated += 1
                
                # 写入赛果（非fid_only模式）
                if not fid_only:
                    sets.append("actual_outcome = ?")
                    params.append(matched_res['outcome'])
                    sets.append("reference_score = ?")
                    params.append(matched_res['score'])
                
                if sets:
                    params.append(rec['id'])
                    cursor.execute(
                        f"UPDATE poisson_predictions SET {', '.join(sets)} WHERE id = ?",
                        params
                    )
                    updated += 1
            
            fid_tag = f' fid={matched_res["fid"]}' if matched_res.get('fid') else ''
            details.append(
                f"  ✅ {rec['home_team']} vs {rec['away_team']} ← "
                f"[{matched_res['league']}] {matched_res['home']} {matched_res['score']} {matched_res['away']}{fid_tag}"
            )
    
    if not dry_run and (updated or fid_updated):
        conn.commit()
    conn.close()
    return updated, fid_updated, details


def get_unfilled_dates(db_path, last_n=None):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    query = """
        SELECT DISTINCT date(kickoff_time) as d
        FROM poisson_predictions
        WHERE (actual_outcome IS NULL OR actual_outcome = '' OR actual_outcome NOT GLOB '*[0-9]-[0-9]*')
          AND date(kickoff_time) <= date('now')
        ORDER BY d DESC
    """
    if last_n:
        query += f" LIMIT {last_n}"
    c.execute(query)
    dates = [row[0] for row in c.fetchall()]
    conn.close()
    return dates


def main():
    parser = argparse.ArgumentParser(description="批量回填历史赛果（500.com完场页）v4")
    parser.add_argument('--db', required=True, help='数据库路径')
    parser.add_argument('--date', help='指定日期 YYYY-MM-DD')
    parser.add_argument('--date-from', help='起始日期 YYYY-MM-DD')
    parser.add_argument('--date-to', help='结束日期 YYYY-MM-DD')
    parser.add_argument('--last', type=int, help='只回填最近N天')
    parser.add_argument('--dry-run', action='store_true', help='只显示不写入')
    parser.add_argument('--fid-only', action='store_true', help='只回填fid_500字段，不更新赛果')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示每条匹配详情')
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    elif args.date_from or args.date_to:
        from datetime import timedelta
        start = datetime.strptime(args.date_from or '2020-01-01', '%Y-%m-%d')
        end = datetime.strptime(args.date_to or datetime.now().strftime('%Y-%m-%d'), '%Y-%m-%d')
        dates = []
        cur = start
        while cur <= end:
            dates.append(cur.strftime('%Y-%m-%d'))
            cur += timedelta(days=1)
    else:
        dates = get_unfilled_dates(args.db, args.last)
    
    if not dates:
        print('✅ 所有日期赛果已填满，无需回填')
        return
    
    conn = sqlite3.connect(args.db)
    c = conn.cursor()
    
    if args.fid_only:
        c.execute("SELECT COUNT(*) FROM poisson_predictions WHERE fid_500 IS NULL")
    else:
        c.execute("""
            SELECT COUNT(*) FROM poisson_predictions
            WHERE (actual_outcome IS NULL OR actual_outcome = '' OR actual_outcome NOT GLOB '*[0-9]-[0-9]*')
              AND date(kickoff_time) <= date('now')
        """)
    missing = c.fetchone()[0]
    conn.close()
    
    mode_tag = ' [fid-only]' if args.fid_only else ''
    print(f'📋 待回填: {missing}条记录, {len(dates)}个日期 ({dates[-1]} ~ {dates[0]}){mode_tag}')
    
    total_updated = 0
    total_fid = 0
    for date_str in dates:
        results = fetch_wanchang_by_date(date_str)
        if not results:
            print(f'  {date_str}: ⚠️ 无赛果数据')
            continue
        
        # 统计有fid的结果数
        fid_count = sum(1 for r in results if r.get('fid'))
        
        updated, fid_updated, details = backfill_db(results, args.db, args.dry_run, args.fid_only)
        total_updated += updated
        total_fid += fid_updated
        
        tag = ' [dry]' if args.dry_run else ''
        print(f'  {date_str}: {len(results)}场完场({fid_count}场有fid), 回填{tag} {updated}条赛果, {fid_updated}条fid')
        
        if args.verbose:
            for d in details:
                print(d)
        
        time.sleep(1.5)
    
    print(f'\n🎉 总计: {total_updated}条赛果, {total_fid}条fid (还剩 {missing - total_updated} 条未匹配)')


if __name__ == '__main__':
    main()
