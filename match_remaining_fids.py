#!/usr/bin/env python3
"""精确扫描500.com页面，为剩余128场缺fid比赛补全"""
import urllib.request, re, sqlite3, json, sys, time

# === 更全面的队名映射（500.com页面名 → DB名）===
# DB用的简称/别名 → 500.com可能用的全称
TEAM_ALIASES = {
    # === 沙特 ===
    "利雅得": "利雅得胜利", "利雅胜利": "利雅得胜利", "利雅得胜利": "利雅得胜利",
    "利雅新月": "利雅得新月", "利雅得新月": "利雅得新月",
    "吉达国民": "吉达国民",
    "拉斯永恒": "阿尔阿赫利",
    "布赖合作": "布赖代合作",
    "赛哈海湾": "赛哈特海湾",
    "迈季宽广": "新未来城体育",
    "达马克": "达马克FC",
    "吉达联合": "伊蒂哈德",
    "胡巴卡德": "阿尔卡迪西亚",
    # === 芬超 ===
    "赫尔火花": "格尼斯坦",
    "雅罗": "Jaro",
    "塞那乔其": "塞伊奈约基",
    "图尔库国际": "国际图尔库",
    "玛丽港": "奥兰岛",
    "赫尔辛基": "HJK赫尔辛基",
    "库奥皮奥": "古比斯",
    "塞纳乔琪": "塞伊奈约基",
    "瓦萨": "VPS瓦萨",
    # === 葡超 ===
    "葡国民": "葡萄牙国民",
    "吉马良斯": "吉马良斯维多利亚",
    "卡沙比亚": "卡萨皮亚",
    "里奥阿维": "里奥艾维",
    "法马利康": "法马利考",
    "阿维卡": "AVS",
    "托林斯": "托里伦斯",
    "卡萨皮亚": "卡萨皮亚",
    "杜连斯": "唐迪拉",
    "卡沙比亚": "卡萨皮亚",
    # === 西乙 ===
    "桑德竞技": "桑坦德竞技",
    "巴多利德": "巴利亚多利德",
    "阿梅里亚": "阿尔梅里亚",
    "拉斯帕尔马斯": "拉斯帕尔马斯",
    "莱昂文化": "利安尼沙",
    "布尔戈斯": "布尔戈斯",
    "马拉加": "马拉加",
    "拉科鲁尼亚": "拉科鲁尼亚",
    "希洪竞技": "希洪竞技",
    # === 智利甲 ===
    "华奇巴托": "瓦奇巴托",
    "拉卡莱拉联": "卡拉雷联",
    "科布雷索": "科布雷索",
    "智利大学": "智利大学",
    "科洛科洛": "科洛科洛",
    "纽夫莱": "纽布伦斯",
    "康塞普森": "康塞普西翁大学",
    "埃弗顿": "埃弗顿VM",
    "希金斯": "奥希金斯",
    "康塞大学": "康塞普西翁大学",
    "意大利人": "意大利人",
    "帕莱斯蒂诺": "帕莱斯蒂诺",
    # === 挪超/挪甲 ===
    "瓦勒伦加": "瓦勒伦加",
    "萨普斯堡": "萨普斯堡",
    "孔斯温": "孔斯温格",
    "奥德": "奥特",
    "斯特勒门": "斯塔尔门/斯特罗门",
    "海于格松": "海于格松",
    "奥萨尼": "阿萨内",
    "桑德尼斯": "桑讷菲尤尔/桑德尼斯",
    "利恩": "利恩",
    "阿萨纳": "阿萨内",
    "腓特烈": "费德列斯达",
    "费特斯塔": "费德列斯达",
    "桑纳菲": "桑讷菲尤尔",
    "奥斯KFUM": "KFUM奥斯陆",
    "特罗姆瑟": "特罗姆瑟",
    "布兰": "布兰",
    "摩斯": "莫斯",
    "桑德尼斯": "桑讷菲尤尔",
    # === 瑞超 ===
    "埃夫斯堡": "埃尔夫斯堡",
    "赫根": "赫根",
    "埃尔夫斯堡": "埃尔夫斯堡",
    "米亚尔比": "米亚尔比",
    "厄格里特": "厄斯特松德",
    # === 巴西甲/乙 ===
    "米竞技": "米内罗竞技",
    "米拉索": "米拉索尔",
    "铁路工人": "瓜拉尼",
    "累西腓": "累西腓体育",
    "雷加塔斯": "雷加塔斯巴西",
    "庞普雷塔": "庞特普雷塔",
    "隆迪那": "隆德里纳",
    "福塔雷萨": "福塔莱萨",
    "庞特普雷塔": "庞特普雷塔",
    "维拉诺瓦": "维拉诺瓦",
    "奥瓦": "阿瓦伊",
    "沙佩科恩斯": "沙佩科恩斯",
    "雷莫": "雷莫",
    "巴拉纳竞技": "巴拉纳竞技",
    "博塔弗戈": "博塔弗戈",
    "诺瓦桑蒂诺": "新奥里藏特",
    "塞阿拉": "塞阿拉",
    "戈亚斯": "戈亚斯",
    "瓦斯科伽马": "瓦斯科达伽马",
    "布拉干蒂诺": "布拉甘蒂诺红牛",
    # === 阿甲 ===
    "阿青年人": "阿根廷青年",
    "贝格拉诺": "贝尔格拉诺",
    "河床": "河床",
    "阿独立": "独立",
    "圣菲联合": "圣塔菲联",
    # === 解放者杯/南美杯 ===
    "时刻准备": "最强者",
    "米拉索尔": "米拉索尔",
    "帕梅拉斯": "帕尔梅拉斯",
    "波特诺": "波特诺山丘",
    "弗拉门戈": "弗拉门戈",
    "拉普大学": "拉普拉塔大学生",
    "蒙国民": "乌拉圭民族",
    "大学体育": "体育大学",
    "托利马": "托利马体育",
    "基多体大": "基多大学",
    "科金博联": "科金博",
    "玻利瓦尔": "玻利瓦尔",
    "里独立": "利马联/体育学院",
    "科林蒂安": "科林蒂安",
    "普拉滕斯": "普拉滕斯",
    "亚自由": "亚松森自由",
    "中央大学": "中央大学生",
    "巴兰基亚": "巴兰基亚青年",
    "拉巴斯": "拉巴斯/强者",
    # === 南美杯 ===
    "图尔克": "Deportes Tolima/托利马",
    "利斯特雷": "利斯特雷",
    "波士顿河": "波士顿",
    "帕莱斯蒂诺": "帕莱斯蒂诺",
    "亚奥林": "亚松森奥林匹亚",
    "格雷米奥": "格雷米奥",
    "佩特莱罗": "佩特莱罗",
    "河床": "河床",
    "布拉干蒂诺": "布拉甘蒂诺红牛",
    "西恩夏诺": "西索尔",
    "港发院": "港发院",
    "拉斯彼德": "拉斯彼德拉斯",
    "强者": "最强者",
    "卡拉沃沃": "卡拉波波",
    # === 美职联 ===
    "奥兰多": "奥兰多城",
    "亚特联": "亚特兰大联",
    "休斯敦": "休斯敦迪纳摩",
    "温哥华": "温哥华白帽",
    "西雅图": "西雅图海湾人",
    "洛城银河": "洛杉矶银河",
    "迈国际": "迈阿密国际",
    "费城": "费城联合",
    "哥伦布": "哥伦布机员",
    "圣路易斯": "圣路易斯城",
    # === 意乙 ===
    "史泰比亚": "斯塔比亚",
    "蒙扎": "蒙扎",
    # === 比甲 ===
    "布鲁日": "布鲁日",
    "圣吉联合": "圣吉罗斯",
    "韦斯特洛": "韦斯特洛",
    "标准列日": "标准列日",
    "根特": "根特",
    "安德莱赫": "安德莱赫特",
    "安特卫普": "安特卫普",
    # === 法甲 ===
    "巴黎FC": "巴黎FC",
    "巴黎圣曼": "巴黎圣日尔曼",
    "南特": "南特",
    "图卢兹": "图卢兹",
    # === 罗甲 ===
    "克约大学": "克卢日大学",
    "克卢大学": "克卢日",
    "布星": "布加勒斯特星",
    "布特快速": "布加勒斯特快速",
    "博托沙尼": "博托沙尼",
    "奥拉迪亚/奥特鲁加": "奥特鲁加拉蒂/奥特鲁加",
    "法鲁尔": "法鲁尔康斯坦察",
    "梅洛布斯": "梅洛",
    "佩特罗鲁": "佩特罗鲁",
    "斯洛博齐亚": "斯洛博齐亚",
    "阿拉德联队": "UT亚拉德",
    "赫曼施塔特": "赫曼施塔特",
    "布迪纳摩": "布加勒斯特迪纳摩",
    # === 奥甲 ===
    "阿特拉赫": "阿尔塔奇",
    "里德": "里德",
    "维快速": "维也纳快速",
    "沃尔夫斯": "沃尔夫斯贝格",
    # === 希腊超 ===
    "潘塞莱科": "潘塞莱科斯",
    "帕纳多里": "帕纳托利科斯",
    # === 葡甲 ===
    "费雷拉": "费雷拉",
    "佩纳菲耶": "佩纳菲耶尔",
    "雷克斯": "莱克索斯",
    "鲁席尼亚": "卢西塔尼亚",
    # === 德甲 ===
    "沃夫斯堡": "沃尔夫斯堡",
    "帕德博恩": "帕德博恩",
    # === 欧冠 ===
    "巴黎圣曼": "巴黎圣日尔曼",
    "阿森纳": "阿森纳",
    # === 国际友谊 ===
    "斯洛伐克": "斯洛伐克",
    "黑山": "黑山",
    # === 冰岛超 ===
    "托尔阿克雷里": "托尔",
    "雷克雅未克": "雷克雅未克",
    "维京古": "维京古尔",
    "KA阿克雷里": "KA阿克雷里",
    "哈夫纳夫约杜尔": "哈夫纳夫约杜尔",
    "IBV韦斯特曼纳": "IBV韦斯特曼纳",
    # === 瑞典超甲 ===
    "松兹瓦尔": "松兹瓦尔",
    "奥斯达": "奥斯达",
    # === 阿根廷杯 ===
    "圣菲联合": "圣塔菲联",
    "阿独立": "独立",
    # === 美公开赛 ===
    "亚特联": "亚特兰大联",
}

def fetch_url(url, timeout=20):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36'
            })
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read()
            return raw.decode('gb2312', errors='ignore')
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"  ❌ {url}: {e}", file=sys.stderr)
            return None

def extract_fids(html):
    """从页面提取 fid + 联赛/主/客"""
    matches = []
    for m in re.finditer(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]*)"', html):
        fid = m.group(1)
        gy = m.group(2)
        parts = gy.split(',')
        if len(parts) >= 3:
            matches.append({
                'fid': fid,
                'league': parts[0].strip(),
                'home': parts[1].strip(),
                'away': parts[2].strip()
            })
    return matches

def normalize(name):
    return name.replace(' ', '').replace('&amp;', '&').replace('（', '(').replace('）', ')').replace('-', '').strip()

def fuzzy_match(short, long):
    """检查 short 是否包含于 long 或 long 包含于 short"""
    if not short or not long:
        return False
    short = short.lower()
    long = long.lower()
    if short == long:
        return True
    if short in long:
        return len(short) >= 2
    if long in short:
        return len(long) >= 2
    return False

# 连接DB
db = sqlite3.connect('data/football.db')
cur = db.cursor()

# 获取所有缺fid的比赛
cur.execute("""SELECT id, date, league, home_team, away_team 
    FROM poisson_predictions 
    WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0 
    ORDER BY date""")
miss_rows = cur.fetchall()
print(f"需要匹配: {len(miss_rows)} 场")

# 按日期分组
dates_needed = {}
for r in miss_rows:
    dt = r[1]
    if dt not in dates_needed:
        dates_needed[dt] = []
    dates_needed[dt].append(r)

# 逐日期获取页面并精确匹配
total_matched = 0
total_miss = 0
results = []
still_missing = []

for dt in sorted(dates_needed.keys()):
    db_games = dates_needed[dt]
    
    # 先试 wanchang
    html = fetch_url(f'https://live.500.com/wanchang.php?e={dt}')
    page_matches = extract_fids(html) if html else []
    
    # 再试首页 (对于今天/最近的比赛)
    if not page_matches:
        html_home = fetch_url('https://live.500.com/')
        page_matches = extract_fids(html_home) if html_home else []
    
    print(f"\n📅 {dt}: {len(db_games)}场DB vs {len(page_matches)}场500.com")
    
    for g in db_games:
        idx, _, league, db_home, db_away = g
        db_home_n = normalize(db_home)
        db_away_n = normalize(db_away)
        
        # 别名转换
        db_home_aliased = normalize(TEAM_ALIASES.get(db_home, db_home))
        db_away_aliased = normalize(TEAM_ALIASES.get(db_away, db_away))
        
        best = None
        best_score = 0
        best_type = ''
        
        for pm in page_matches:
            ph = normalize(pm['home'])
            pa = normalize(pm['away'])
            
            # 策略1: 完全匹配
            if ph == db_home_n and pa == db_away_n:
                best_score = 100
                best = pm
                best_type = 'exact'
                break
            # 策略1b: 互换
            if ph == db_away_n and pa == db_home_n:
                if 90 > best_score:
                    best_score = 90
                    best = pm
                    best_type = 'swap'
                    continue
            
            # 策略2: 别名匹配
            if ph == db_home_aliased and pa == db_away_aliased:
                if 80 > best_score:
                    best_score = 80
                    best = pm
                    best_type = 'alias'
                    continue
            if ph == db_away_aliased and pa == db_home_aliased:
                if 70 > best_score:
                    best_score = 70
                    best = pm
                    best_type = 'alias_swap'
                    continue
            
            # 策略3: 模糊匹配 (包含关系)
            score = 0
            h_score = 0
            a_score = 0
            
            # 主页队名包含 DB队名
            if fuzzy_match(db_home_n, ph):
                h_score = max(h_score, 1)
            if fuzzy_match(db_home_aliased, ph):
                h_score = max(h_score, 1)
            if fuzzy_match(db_away_n, pa):
                a_score = max(a_score, 1)
            if fuzzy_match(db_away_aliased, pa):
                a_score = max(a_score, 1)
            
            # 互换
            if fuzzy_match(db_home_n, pa):
                h_score = max(h_score, 0.5)
            if fuzzy_match(db_away_n, ph):
                a_score = max(a_score, 0.5)
            
            # DB队名包含 主页队名
            if fuzzy_match(ph, db_home_n) or fuzzy_match(ph, db_home_aliased):
                h_score = max(h_score, 0.8)
            if fuzzy_match(pa, db_away_n) or fuzzy_match(pa, db_away_aliased):
                a_score = max(a_score, 0.8)
            
            if h_score > 0 and a_score > 0:
                score = h_score + a_score
                if score > best_score:
                    best_score = score
                    best = pm
                    best_type = f'fuzzy({h_score},{a_score})'
        
        if best and best_score >= 1.0:
            # 写入DB
            cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (int(best['fid']), idx))
            total_matched += 1
            results.append((idx, f"{best['fid']}", f"{dt} {league}: {db_home} vs {db_away} -> {best['fid']} ({best['home']} vs {best['away']}) [{best_type}]"))
            print(f"  ✅ ID={idx}: {db_home} vs {db_away} -> fid={best['fid']} ({best_type})")
        else:
            total_miss += 1
            still_missing.append(g)
            print(f"  ❌ ID={idx}: {db_home} vs {db_away} ({dt} {league}) — 页面无匹配")

db.commit()

print(f"\n{'='*50}")
print(f"结果: 匹配 {total_matched}, 仍缺 {total_miss}")
print(f"DB total: {db.execute('SELECT COUNT(fid_500) FROM poisson_predictions WHERE fid_500 IS NOT NULL AND fid_500!=\"\" AND fid_500!=0').fetchone()[0]} 已有fid")

if still_missing:
    print(f"\n仍缺 {len(still_missing)} 场:")
    for g in still_missing:
        print(f"  {g[1]} {g[2]}: {g[3]} vs {g[4]}")

# 保存匹配记录
with open('matched_fids_v2.json', 'w') as f:
    json.dump([[r[0], r[1]] for r in results], f, ensure_ascii=False)
print(f"\n匹配结果已保存到 matched_fids_v2.json ({len(results)}条)")

db.close()
