#!/usr/bin/env python3
"""检查前后日期搜索匹配 — 很多比赛日期在500.com上可能差1天"""
import urllib.request, re, sqlite3, json, sys, time
from datetime import datetime, timedelta

TEAM_ALIASES = {
    "利雅得": "利雅得胜利", "利雅胜利": "利雅得胜利",
    "利雅新月": "利雅得新月",
    "迈季宽广": "新未来城体育",
    "布赖合作": "布赖代合作",
    "赛哈海湾": "赛哈特海湾",
    "吉达联合": "伊蒂哈德",
    "胡巴卡德": "阿尔卡迪西亚",
    "赫尔火花": "格尼斯坦",
    "雅罗": "Jaro", "塞那乔其": "塞伊奈约基",
    "图尔库国际": "国际图尔库",
    "玛丽港": "奥兰岛", "赫尔辛基": "HJK赫尔辛基",
    "库奥皮奥": "古比斯", "瓦萨": "VPS瓦萨",
    "葡国民": "葡萄牙国民", "卡沙比亚": "卡萨皮亚",
    "法马利康": "法马利考", "阿维卡": "AVS",
    "托林斯": "托里伦斯", "杜连斯": "唐迪拉",
    "桑德竞技": "桑坦德竞技", "巴多利德": "巴利亚多利德",
    "莱昂文化": "利安尼沙",
    "华奇巴托": "瓦奇巴托", "拉卡莱拉联": "卡拉雷联",
    "科布雷索": "科布雷索", "康塞普森": "康塞普西翁",
    "埃弗顿": "埃弗顿VM", "希金斯": "奥希金斯",
    "康塞大学": "康塞普西翁", "意大利人": "意大利人",
    "瓦勒伦加": "瓦勒伦加", "孔斯温": "孔斯温格",
    "奥德": "奥特", "斯特勒门": "斯塔尔门",
    "海于格松": "海于格松", "奥萨尼": "阿萨内",
    "桑德尼斯": "桑讷菲尤尔", "利恩": "利恩",
    "阿萨纳": "阿萨内", "腓特烈": "腓特烈斯塔",
    "费特斯塔": "腓特烈斯塔",
    "桑纳菲": "桑讷菲尤尔",
    "奥斯KFUM": "KFUM奥斯陆",
    "埃夫斯堡": "埃尔夫斯堡", "厄格里特": "厄斯特松德",
    "米竞技": "米内罗竞技", "米拉索": "米拉索尔",
    "铁路工人": "累西腓体育", "累西腓": "累西腓体育",
    "雷加塔斯": "雷加塔斯巴西", "庞普雷塔": "庞特普雷塔",
    "隆迪那": "隆德里纳", "福塔雷萨": "福塔莱萨",
    "庞特普雷塔": "庞特普雷塔",
    "阿青年人": "阿根廷青年人", "贝格拉诺": "贝尔格拉诺",
    "阿独立": "独立", "圣菲联合": "圣塔菲联",
    "帕梅拉斯": "帕尔梅拉斯", "波特诺": "波特诺山丘",
    "拉普大学": "拉普拉塔大学生",
    "蒙国民": "乌拉圭民族", "大学体育": "体育大学",
    "基多体大": "基多大学", "科金博联": "科金博",
    "里独立": "体育学院", "巴兰基亚": "巴兰基亚青年",
    "拉巴斯": "最强者", "时刻准备": "最强者",
    "图尔克": "托利马体育", "利斯特雷": "利斯特雷",
    "亚奥林": "亚松森奥林匹亚", "波士顿河": "波士顿",
    "佩特莱罗": "佩特莱罗", "西恩夏诺": "西索尔",
    "港发院": "港发院", "拉斯彼德": "拉斯彼德拉斯",
    "强者": "最强者", "卡拉沃沃": "卡拉波波",
    "亚特联": "亚特兰大联", "休斯敦": "休斯敦迪纳摩",
    "迈国际": "迈阿密国际", "哥伦布": "哥伦布机员",
    "奥兰多": "奥兰多城", "西雅图": "西雅图海湾人",
    "洛城银河": "洛杉矶银河",
    "圣吉联合": "圣吉罗斯", "安德莱赫": "安德莱赫特",
    "巴黎圣曼": "巴黎圣日耳曼",
    "布星": "布加勒斯特星", "布特快速": "布加勒斯特快速",
    "克约大学": "克拉约瓦大学", "克卢大学": "克卢日大学",
    "阿特拉赫": "阿尔塔奇", "维快速": "维也纳快速",
    "沃尔夫斯": "沃尔夫斯贝格",
    "史泰比亚": "斯塔比亚", "蒙扎": "蒙扎",
    "潘塞莱科": "潘塞莱科斯", "帕纳多里": "帕纳托利科斯",
    "佩纳菲耶": "佩纳菲尔",
    "雷克斯": "莱克索斯", "鲁席尼亚": "卢西塔尼亚",
    "莫斯": "摩斯",
    "惠灵顿": "惠灵顿凤凰",
}

# 联赛名映射
LEAGUE_ALIASES = {
    "沙特联": "沙职", "沙职": "沙特联",
    "巴西甲": "巴甲", "巴甲": "巴西甲",
    "巴西乙": "巴乙", "巴乙": "巴西乙",
    "智利甲": "智甲", "智甲": "智利甲",
    "美职联": "美职联", "美职": "美职联",
    "南美杯": "南美杯", "解放者杯": "解放者杯",
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

def get_matches_for_date(dt):
    """获取某日期的所有500.com比赛"""
    url = f'https://live.500.com/wanchang.php?e={dt}'
    html = fetch_url(url)
    if not html: return []
    matches = []
    for m in re.finditer(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]*)"', html):
        fid = m.group(1)
        parts = m.group(2).split(',')
        if len(parts) >= 3:
            matches.append({
                'fid': fid, 'league': parts[0].strip(),
                'home': parts[1].strip(), 'away': parts[2].strip()
            })
    return matches

def norm(s):
    return s.replace(' ', '').lower()

def match_score(db_home, db_away, ph, pa, db_leag, p_leag):
    """匹配评分，越高越好"""
    score = 0
    db_h = norm(db_home)
    db_a = norm(db_away)
    ph_n = norm(ph)
    pa_n = norm(pa)
    
    # 队名完全匹配
    if db_h == ph_n and db_a == pa_n: score += 100
    elif db_h == pa_n and db_a == ph_n: score += 80  # 互换
    
    # 别名匹配
    aliased_h = norm(TEAM_ALIASES.get(db_home, db_home))
    aliased_a = norm(TEAM_ALIASES.get(db_away, db_away))
    if aliased_h == ph_n and aliased_a == pa_n: score += 90
    elif aliased_h == pa_n and aliased_a == ph_n: score += 70
    
    # 包含关系
    if db_h in ph_n or ph_n in db_h: score += 30
    if db_a in pa_n or pa_n in db_a: score += 30
    if aliased_h in ph_n or ph_n in aliased_h: score += 25
    if aliased_a in pa_n or pa_n in aliased_a: score += 25
    
    # 前缀匹配
    if len(db_h) >= 2 and db_h[:2] == ph_n[:2]: score += 5
    if len(db_a) >= 2 and db_a[:2] == pa_n[:2]: score += 5
    
    # 联赛名加分
    db_leag_n = norm(db_leag)
    p_leag_n = norm(p_leag)
    if db_leag_n == p_leag_n: score += 20
    elif LEAGUE_ALIASES.get(db_leag, '') == p_leag or LEAGUE_ALIASES.get(p_leag, '') == db_leag: score += 15
    
    return score

# 连接DB
db = sqlite3.connect('data/football.db')
cur = db.cursor()

# 查缺fid
cur.execute("""SELECT id, date, league, home_team, away_team 
    FROM poisson_predictions 
    WHERE fid_500 IS NULL OR fid_500='' OR fid_500=0 
    ORDER BY date""")
miss_rows = cur.fetchall()
print(f"需要匹配: {len(miss_rows)} 场")

total_matched = 0
results = []
still_missing = []

for idx, dt, league, home, away in miss_rows:
    dt_obj = datetime.strptime(dt[:10], '%Y-%m-%d')
    best_match = None
    best_score = 0
    best_fid = None
    best_date_used = None
    
    # 检查当天、前一天、后一天
    for offset in [0, -1, 1]:
        check_dt = (dt_obj + timedelta(days=offset)).strftime('%Y-%m-%d')
        page_matches = get_matches_for_date(check_dt)
        
        for pm in page_matches:
            s = match_score(home, away, pm['home'], pm['away'], league, pm['league'])
            if s > best_score:
                best_score = s
                best_match = pm
                best_fid = pm['fid']
                best_date_used = check_dt
    
    if best_fid and best_score >= 40:
        cur.execute("UPDATE poisson_predictions SET fid_500=? WHERE id=?", (int(best_fid), idx))
        total_matched += 1
        results.append((idx, best_fid))
        match_detail = f"  ✅ ID={idx}: {home} vs {away} ({dt}) -> fid={best_fid} score={best_score} [{best_date_used}] {best_match['home']} vs {best_match['away']}"
        print(match_detail)
    else:
        still_missing.append((idx, dt, league, home, away))
        if best_fid:
            print(f"  ❌ ID={idx}: {home} vs {away} ({dt}) -> 低分={best_score} (最近: {best_date_used} {best_match['home']} vs {best_match['away']})")
        else:
            print(f"  ❌ ID={idx}: {home} vs {away} ({dt}) -> 无匹配")

db.commit()

print(f"\n{'='*50}")
r = db.execute('SELECT COUNT(fid_500) FROM poisson_predictions WHERE fid_500 IS NOT NULL AND fid_500!="" AND fid_500!=0').fetchone()
print(f"新匹配: {total_matched}, 仍缺: {len(still_missing)}")
print(f"DB已有fid: {r[0]}")

if still_missing:
    print(f"\n仍缺 {len(still_missing)} 场:")
    for s in still_missing:
        print(f"  {s[1]} {s[2]}: {s[3]} vs {s[4]}")

with open('matched_fids_v3.json', 'w') as f:
    json.dump([[r[0], r[1]] for r in results], f, ensure_ascii=False)
print(f"保存到 matched_fids_v3.json ({len(results)}条)")

db.close()
