#!/usr/bin/env python3
"""逐日期抓取500.com完场页，队名匹配写入fid"""
import urllib.request, re, sqlite3, json, sys, time
from datetime import datetime, timedelta

# 500.com队名 → DB队名的映射
TEAM_MAP = {
    '利雅得胜利': '利雅得',
    '利雅得新月': '利雅新月', '布赖代合作': '布赖合作',
    '吉达国民': '吉达国民', '伊蒂哈德': '吉达联合',
    '达马克FC': '达马克', '阿尔卡迪西亚': '胡巴卡德',
    '赛哈特海湾': '赛哈海湾', '新未来城体育': '迈季宽广',
    '阿尔阿赫利': '拉斯永恒',
    '格尼斯坦': '赫尔火花', 'Jaro': '雅罗',
    '塞伊奈约基': '塞那乔其', '国际图尔库': '图尔库国际',
    'HJK赫尔辛基': '赫尔辛基', '古比斯': '库奥皮奥',
    'VPS瓦萨': '瓦萨',
    '葡萄牙国民': '葡国民', '卡萨皮亚': '卡沙比亚',
    '里奥艾维': '里奥阿维', '法马利考': '法马利康',
    'AVS': '阿维卡', '托里伦斯': '托林斯',
    '唐迪拉': '杜连斯', '吉马良斯维多利亚': '吉马良斯',
    '桑坦德竞技': '桑德竞技', '巴利亚多利德': '巴多利德',
    '阿尔梅里亚': '阿梅里亚', '利安尼沙': '莱昂文化',
    '瓦奇巴托': '华奇巴托', '卡拉雷联': '拉卡莱拉联',
    '康塞普西翁': '康塞普森', '伊瓦顿': '埃弗顿',
    '奥希金斯': '希金斯', '纽布伦斯': '纽夫莱',
    '孔斯温格': '孔斯温', '桑讷菲尤尔': '桑德尼斯',
    'KFUM奥斯陆': '奥斯KFUM', '腓特烈斯塔': '腓特烈',
    '阿萨内': '阿萨纳', '莫斯': '摩斯',
    '埃尔夫斯堡': '埃尔夫斯堡', '厄斯特松德': '厄格里特',
    '米内罗竞技': '米竞技', '米拉索尔': '米拉索',
    '累西腓体育': '铁路工人', '雷加塔斯巴西': '雷加塔斯',
    '庞特普雷塔': '庞特普雷塔', '隆德里纳': '隆迪那',
    '福塔莱萨': '福塔雷萨', '阿瓦伊': '奥瓦',
    '布拉甘蒂诺红牛': '布拉干蒂诺', '瓦斯科达伽马': '瓦斯科伽马',
    '新奥里藏特': '诺瓦桑蒂诺',
    '阿根廷青年人': '阿青年人', '贝尔格拉诺': '贝格拉诺',
    '圣塔菲联': '圣菲联合',
    '帕尔梅拉斯': '帕梅拉斯', '波特诺山丘': '波特诺',
    '拉普拉塔大学生': '拉普大学', '乌拉圭民族': '蒙国民',
    '体育大学': '大学体育', '托利马体育': '托利马',
    '基多大学': '基多体大', '科金博': '科金博联',
    '巴兰基亚青年': '巴兰基亚', '最强者': '时刻准备',
    '亚松森自由': '亚自由', '亚松森奥林匹亚': '亚奥林',
    '西索尔': '西恩夏诺',
    '亚特兰大联': '亚特联', '休斯顿迪纳摩': '休斯敦',
    '迈阿密国际': '迈国际', '哥伦布机员': '哥伦布',
    '奥兰多城': '奥兰多', '西雅图海湾人': '西雅图',
    '洛杉矶银河': '洛城银河', '圣路易斯城': '圣路易斯',
    '圣吉罗斯': '圣吉联合', '安德莱赫特': '安德莱赫',
    '巴黎圣日耳曼': '巴黎圣曼', '巴黎圣日尔曼': '巴黎圣曼',
    '布加勒斯特星': '布星', '布加勒斯特快速': '布特快速',
    '克拉约瓦大学': '克约大学', '克卢日大学': '克卢大学',
    '布加勒斯特迪纳摩': '布迪纳摩',
    '斯塔比亚': '史泰比亚',
    '阿尔塔奇': '阿特拉赫', '维也纳快速': '维快速',
    '沃尔夫斯贝格': '沃尔夫斯',
    '潘塞莱科斯': '潘塞莱科', '帕纳托利科斯': '帕纳多里',
    '佩纳菲尔': '佩纳菲耶', '莱克索斯': '雷克斯',
    '卢西塔尼亚': '鲁席尼亚', '加拉茨钢铁': '奥特鲁加',
    '梅塔洛格布斯': '梅洛布斯', '阿拉德联合': '阿拉德联队',
    '托尔基': '图尔克', '利斯特雷': '利斯特雷',
    '阿尔韦斯瑞迪': '拉巴斯',
    # 欧冠
    '巴黎圣日耳曼': '巴黎圣曼', '巴黎圣日尔曼': '巴黎圣曼',
    '阿森纳': '阿森纳',
    # 冰岛超
    '托尔': '托尔阿克雷里', '雷克雅未克': '雷克雅未克',
    '维京古尔': '维京古', '哈夫纳夫约杜尔': '哈夫纳夫约杜尔',
    '韦斯特曼纳埃亚尔': 'IBV韦斯特曼纳',
    'KA阿克雷里': 'KA阿克雷里',
    # 联赛映射
    '巴甲': '巴西甲', '巴乙': '巴西乙', '智甲': '智利甲',
    '沙特联': '沙特联', '瑞典超甲': '瑞典超甲',
}

def fetch_url(url, timeout=20):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode('gb2312', errors='ignore')
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            return None

def norm(s):
    return s.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').lower()

def match_ok(db_home, db_away, ph, pa):
    """检查两队名是否匹配"""
    db_h = norm(db_home)
    db_a = norm(db_away)
    ph_n = norm(ph)
    pa_n = norm(pa)
    
    # 完全匹配
    if (db_h == ph_n and db_a == pa_n) or (db_h == pa_n and db_a == ph_n):
        return True
    
    # 转换后匹配
    db_h2 = norm(TEAM_MAP.get(ph, ph))
    db_a2 = norm(TEAM_MAP.get(pa, pa))
    if (db_h2 == db_h and db_a2 == db_a) or (db_h2 == db_a and db_a2 == db_h):
        return True
    
    # 反向转换
    db_h3 = norm(TEAM_MAP.get(db_home, db_home))
    db_a3 = norm(TEAM_MAP.get(db_away, db_away))
    if (db_h3 == ph_n and db_a3 == pa_n) or (db_h3 == pa_n and db_a3 == ph_n):
        return True
    
    # 互相包含（至少一方完全包含另一方）
    def contains(a, b):
        return a and b and (a in b or b in a)
    
    if contains(db_h, ph_n) and contains(db_a, pa_n):
        return True
    if contains(db_h, pa_n) and contains(db_a, ph_n):
        return True
    
    return False

db = sqlite3.connect('data/football.db')
cur = db.cursor()

# 所有缺fid的比赛，按日期分组
cur.execute("""SELECT id, date, league, home_team, away_team 
    FROM poisson_predictions 
    WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0 
    ORDER BY date""")
miss_rows = cur.fetchall()

dates = {}
for r in miss_rows:
    dt = r[1]
    if dt not in dates:
        dates[dt] = []
    dates[dt].append(r)

print(f"共 {len(miss_rows)} 场缺fid，分布在 {len(dates)} 个日期")

total_ok = 0
total_fail = 0

for dt in sorted(dates.keys()):
    games = dates[dt]
    
    # 抓当天页面
    html = fetch_url(f'https://live.500.com/wanchang.php?e={dt}')
    if not html:
        print(f"  ❌ {dt}: 页面获取失败")
        total_fail += len(games)
        continue
    
    page_matches = []
    for m in re.finditer(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]*)"', html):
        fid = m.group(1)
        parts = m.group(2).split(',')
        if len(parts) >= 3:
            page_matches.append((fid, parts[0].strip(), parts[1].strip(), parts[2].strip()))
    
    print(f"  📅 {dt}: {len(games)}场DB vs {len(page_matches)}场500.com")
    
    for g in games:
        idx, _, league, home, away = g
        matched = False
        for fid, pleague, ph, pa in page_matches:
            if match_ok(home, away, ph, pa):
                cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (int(fid), idx))
                print(f"    ✅ ID={idx}: {home} vs {away} -> fid={fid}")
                total_ok += 1
                matched = True
                break
        
        if not matched:
            print(f"    ❌ ID={idx}: {home} vs {away} — {dt} {league} 页面无匹配")
            total_fail += 1

db.commit()

r = db.execute('SELECT COUNT(*), COUNT(fid_500) FROM poisson_predictions').fetchone()
print(f"\n{'='*50}")
print(f"结果: 匹配 {total_ok}, 未匹配 {total_fail}")
print(f"DB: 总 {r[0]}, 有fid_500: {r[1]}, 缺: {r[0]-r[1]}")

# 列出仍缺的
if total_fail > 0:
    print(f"\n仍缺 {total_fail} 场:")
    cur.execute("SELECT date, league, home_team, away_team FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0 ORDER BY date")
    for r in cur.fetchall():
        print(f"  {r[0]} {r[1]}: {r[2]} vs {r[3]}")

db.close()
