#!/usr/bin/env python3
"""
从 wanchang.php（无参、浏览器 UA）实时抓取并回填比分到 DB。
一次请求拿到全部完场比分（207场），匹配 DB 中缺赛果的记录。
"""
import os, re, sys, sqlite3, time, urllib.request
from datetime import datetime

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORK_DIR)
REPO_DIR = os.path.dirname(WORK_DIR)

try:
    from team_aliases import canonical, match_key
except ImportError:
    def canonical(name): return name
    def match_key(h, a): return (h.strip(), a.strip())

# 复用 backfill_from_500com 的别名表
_500COM_ALIASES = {
    'MP米凯利': 'MP米克力', 'SJK阿卡泰米阿': 'SJK学院',
    '塞那乔恩': '塞纳乔琪', '塞那乔其': '塞纳乔琪',
    '库普斯': '库奥皮奥', '古比斯': '库奥皮奥',
    '国际图尔库': '国际图尔', '图尔库国际': '国际图尔',
    'TPS土尔库': 'TPS图尔',
    '查路': '坦山猫',
    '赫尔辛基': '赫尔火花',
    '埃尔维斯': '坦山猫',
    '塞伊奈约基': '塞伊奈',
    '华沙莱吉亚': '华沙军团', '摩托鲁宾': '莫托路宾',
    '波兹南莱赫': '波兹莱赫', '莱克普斯纳': '波兹莱赫',
    '乔治罗尼亚': '比亚韦', '什切青波贡': '什切青',
    '施切钦波贡': '什切青',
    'GKS卡托威斯': '卡托威斯', '卡杜华斯': '卡托威斯',
    '扎布热': '扎布热矿工',
    '卢宾扎格列比': '卢宾',
    '名古屋鲸八': '名古屋鲸鱼', '长崎航海': '长崎成功丸',
    '清水鼓动': '清水心跳',
    '坡州市民': '坡州前线', '清州FC': '忠北清州',
    '克里西乌马': '克里丘马',
    '博塔弗戈': '博塔弗戈',
    '蓬塔格罗萨铁路': '铁路工人',
    '兰赫姆': '兰黑姆', '桑内斯': '桑德尼斯',
    '斯特罗姆加斯特': '斯托姆加斯特',
    '桑德维肯斯': '桑德维根斯',
    '永斯基': '卢恩斯基尔', '厄勒布鲁': '奥雷布洛',
    '奥迪沃德': '奥迪沃特',
    '埃尔夫斯堡': '埃夫斯堡', '兰斯科罗纳': '兰斯科罗纳',
    '北欧联合': '北欧联FC', '阿西里斯卡': '北欧联FC',
    '厄格里特': '厄斯特松德',
    'IBV韦斯文尼查': 'IBV韦斯特曼纳',
    '谢尔伯恩': '舒尔本', '沃特福德联合': '沃特联队',
    '沃特福德': '沃特联队',
    '科布漫步者': '科布漫步', '科布多西部': '科布漫步',
    '维也纳快速': '维快速',
    '克卢日大学': '克卢日',
    '圣菲联': '圣菲联合', '独立队': '阿独立',
    '独立FBC': '阿独立', '飓风': '飓风队',
    '贝尔谢巴夏普尔': '加尔达贝尔',
    '米德尔斯堡': '米堡',
    '阿晓斯费马': '奥胡斯费马',
    '斯洛伐克': '斯洛文尼',
}

def canonical_500(name):
    cleaned = re.sub(r'^\[世\d+\]\d*', '', name).strip()
    cleaned = re.sub(r'\[\d+\]', '', cleaned).strip()
    cleaned = re.sub(r'^\d+', '', cleaned).strip()
    cleaned = re.sub(r'\d+$', '', cleaned).strip()
    cleaned = re.sub(r'\d*\[世\d+\]$', '', cleaned).strip()
    if cleaned in _500COM_ALIASES:
        return _500COM_ALIASES[cleaned]
    return canonical(cleaned)

def fetch_wanchang():
    """抓 wanchang.php（无参，浏览器 UA），返回解析结果列表"""
    url = 'https://live.500.com/wanchang.php'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        raw = resp.read()
        html = raw.decode('gbk', errors='replace')
    except Exception as e:
        print(f'❌ 请求失败: {e}')
        return []

    results = []
    trs = re.findall(r'<tr[^>]*id="a(\d+)"[^>]*>(.*?)</tr>', html, re.S)
    for fid, content in trs:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', content, re.S)
        clean = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
        if len(clean) < 8 or clean[0] in ('赛事', '场次'):
            continue
        status = clean[3]
        if status != '完':
            continue

        # 比分提取
        pk = re.search(r'<div class="pk">(.*?)</div>', content, re.S)
        hs, aw = None, None
        if pk:
            hs_m = re.search(r'class="clt1"[^>]*>\s*(\d+)\s*<', pk.group(1))
            aw_m = re.search(r'class="clt3"[^>]*>\s*(\d+)\s*<', pk.group(1))
            if hs_m and aw_m:
                hs, aw = int(hs_m.group(1)), int(aw_m.group(1))
        
        if hs is None:
            continue
        
        score = f'{hs}-{aw}'
        outcome = '主胜' if hs > aw else ('平局' if hs == aw else '客胜')
        
        results.append({
            'fid': int(fid),
            'home': clean[4], 'away': clean[6],
            'score': score, 'home_score': hs, 'away_score': aw,
            'outcome': outcome,
        })
    return results

def team_match(db_home, db_away, r_home, r_away):
    ch = canonical_500(r_home)
    ca = canonical_500(r_away)
    dh = canonical(db_home)
    da = canonical(db_away)
    if ch == dh and ca == da:
        return True
    if (ch in dh or dh in ch) and (ca in da or da in ca):
        return True
    return False

def main():
    db_path = os.path.join(REPO_DIR, 'data', 'football.db')
    
    print('📥 抓取 wanchang.php（无参，浏览器 UA）...')
    results = fetch_wanchang()
    print(f'   解析到 {len(results)} 场完赛比分')
    
    if not results:
        print('❌ 无数据，退出')
        return
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 找出 DB 中缺赛果、且 kickoff_time 已过的记录
    cur.execute("""
        SELECT id, home_team, away_team, kickoff_time, fid_500, date
        FROM poisson_predictions
        WHERE (actual_outcome IS NULL OR actual_outcome = '' OR actual_outcome NOT GLOB '*[0-9]-[0-9]*')
          AND datetime(kickoff_time) <= datetime('now', '+8 hours', 'start of day', '+23 hours')
        ORDER BY kickoff_time
    """)
    missing = cur.fetchall()
    print(f'📋 DB 缺赛果（预计已完赛）: {len(missing)} 场')
    
    updated = 0
    fid_updated = 0
    no_match = []
    
    for row in missing:
        mid, home, away, kt, fid_db, mdate = row
        matched = False
        for res in results:
            if team_match(home, away, res['home'], res['away']):
                # 更新比分
                cur.execute("""
                    UPDATE poisson_predictions 
                    SET actual_outcome = ?, home_score = ?, away_score = ?
                    WHERE id = ?
                """, (res['outcome'], res['home_score'], res['away_score'], mid))
                
                # 补 fid（如果DB里没有）
                if not fid_db:
                    cur.execute("UPDATE poisson_predictions SET fid_500 = ? WHERE id = ?",
                                (res['fid'], mid))
                    fid_updated += 1
                
                updated += 1
                matched = True
                print(f'  ✅ {mdate} {kt} {home} vs {away} → {res["score"]} {res["outcome"]} (fid={res["fid"]})')
                break
        
        if not matched:
            no_match.append(f'  ❌ {mdate} {kt} {home} vs {away}')
    
    conn.commit()
    conn.close()
    
    print(f'\n🎉 更新完成: {updated} 场比分, {fid_updated} 个 fid')
    if no_match:
        print(f'⚠️ 未匹配 {len(no_match)} 场:')
        for nm in no_match[:20]:
            print(nm)

if __name__ == '__main__':
    main()
