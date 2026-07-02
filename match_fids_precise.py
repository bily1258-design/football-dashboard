#!/usr/bin/env python3
"""Match DB matches to 500.com fids by fetching per-date pages and matching by league+team"""
import urllib.request, re, sqlite3, json, sys, time
from collections import defaultdict

def fetch_wanchang(date_str):
    url = f"https://live.500.com/wanchang.php?e={date_str}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=15)
            chunks = []
            while True:
                try:
                    chunk = resp.read(65536)
                    if not chunk: break
                    chunks.append(chunk)
                except: break
            data = b"".join(chunks).decode("gb2312", errors="ignore")
            matches = re.findall(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]+)"', data)
            result = []
            for fid, gy in matches:
                parts = gy.split(",")
                if len(parts) >= 3:
                    result.append((fid, parts[0], parts[1], parts[2]))
            return result
        except Exception as e:
            time.sleep(3)
    return []

# Alias map: DB name -> possible names on 500.com
ALIASES = {
    '迈季宽广': ['新未来城体育', '新未来城'],
    '布赖合作': ['布赖代合作'],
    '赫尔火花': ['赫尔辛基'],
    '塞那乔其': ['塞那乔恩'],
    '塞纳乔琪': ['塞那乔恩'],
    '塞伊奈': ['塞那乔恩', 'SJK'],
    '雅罗': ['FF雅罗', 'FF雅罗B队'],
    '拉斯永恒': ['阿尔拉斯永恒', '拉斯永恒'],
    '坦山猫': ['坦佩雷'],
    '埃尔夫斯堡': ['埃尔夫斯堡'],
    '埃夫斯堡': ['埃尔夫斯堡'],
    '费特斯塔': ['腓特烈斯塔'],
    '哈斯塔德': ['哈姆斯塔德'],
    '时刻准备': ['最强者'],
    '布星': ['布加勒斯特星', 'CSA布加勒斯特星'],
    '布特快速': ['布加勒斯特快速'],
    '布斯巴达': ['布拉格斯拉维亚', '布拉格斯巴达'],
    '帕莱斯蒂诺': ['巴勒斯坦人'],
    '圣吉联合': ['圣吉罗斯'],
    '阿独立': ['阿根廷独立'],
    '迈国际': ['迈阿密国际'],
    '费城': ['费城联合'],
    '洛城银河': ['洛杉矶银河'],
    '亚特联': ['亚特兰大联'],
    '休斯敦': ['休斯敦'],
    '华奇巴托': ['瓦奇巴托'],
    '克约大学': ['克拉约瓦大学'],
    '克卢大学': ['克卢日大学'],
    '托林斯': ['托里恩斯', '托林斯'],
    '利雅得': ['利雅得青年人', '利雅得体育'],
    '葡国民': ['葡萄牙国民'],
    '赛哈海湾': ['塞哈特海湾'],
    '塞雷那': ['拉塞雷纳'],
    '塞阿拉': ['塞阿拉'],
    '科林蒂安': ['科林蒂安'],
    '瓦斯科伽马': ['瓦斯科达伽马'],
    '庞普雷塔': ['庞特普雷塔'],
    '桑德竞技': ['桑坦德竞技'],
    '巴多利德': ['巴利亚多利德'],
    '奥斯特华': ['俄斯特拉发'],
}

def normalize(s):
    """Remove spaces and punctuation"""
    return s.replace(' ', '').replace('-', '')

# Load DB data
db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT id, match_id, date, home_team, away_team, league FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' ORDER BY date")
missing = cur.fetchall()
print(f"Matches missing fids: {len(missing)}")

# Group by date
by_date = defaultdict(list)
for row in missing:
    by_date[row[2][:10]].append(row)

matched = []
unmatched = []

for date_str, rows in sorted(by_date.items()):
    matches_500 = fetch_wanchang(date_str)
    print(f"\n[{date_str}] {len(matches_500)} matches on 500.com, need to match {len(rows)} DB matches")
    
    for row in rows:
        mid, match_id, dt, home, away, league = row
        norm_home = normalize(home)
        norm_away = normalize(away)
        
        home_aliases = [home] + [normalize(a) for a in ALIASES.get(home, [])]
        away_aliases = [away] + [normalize(a) for a in ALIASES.get(away, [])]
        home_aliases = list(set(home_aliases))
        away_aliases = list(set(away_aliases))
        
        # Build scores for each 500.com match
        scored = []
        for fid, l500, h500, a500 in matches_500:
            score = 0
            # League match check (allow partial)
            common_league_chars = len(set(league[:3]) & set(l500[:3]))
            if common_league_chars >= 2:
                score += 5
            
            # Check home
            home_score = 0
            for alias in home_aliases:
                n_alias = normalize(alias)
                n_h500 = normalize(h500)
                # Exact match (allow partial containment)
                if alias == h500:
                    home_score = max(home_score, 10)
                elif n_alias == n_h500:
                    home_score = max(home_score, 8)
                elif alias in h500 or h500 in alias:
                    home_score = max(home_score, 6)
                elif norm_home[:2] in n_h500 or n_h500[:2] in norm_home[:2]:
                    home_score = max(home_score, 3)
            
            # Check away
            away_score = 0
            for alias in away_aliases:
                n_alias = normalize(alias)
                n_a500 = normalize(a500)
                if alias == a500:
                    away_score = max(away_score, 10)
                elif n_alias == n_a500:
                    away_score = max(away_score, 8)
                elif alias in a500 or a500 in alias:
                    away_score = max(away_score, 6)
                elif norm_away[:2] in n_a500 or n_a500[:2] in norm_away[:2]:
                    away_score = max(away_score, 3)
            
            # Cross check (home team name in away slot)
            cross_home_away = 0
            if home == a500 or norm_home == normalize(a500):
                cross_home_away = 3
            if away == h500 or norm_away == normalize(h500):
                cross_home_away = 3
            
            score += home_score + away_score + cross_home_away
            if score > 0:
                scored.append((score, fid, h500, a500))
        
        scored.sort(reverse=True)
        
        # Take only best match with enough score
        if scored and scored[0][0] >= 8:
            best = scored[0]
            matched.append((mid, match_id, best[1]))
            print(f"  ✅ {league:<10} {home:<10} vs {away:<10} -> fid={best[1]} ({best[2]} vs {best[3]})")
        elif scored and scored[0][0] >= 5:
            best = scored[0]
            print(f"  ~  {league:<10} {home:<10} vs {away:<10} -> fid={best[1]}? ({best[2]} vs {best[3]}) score={best[0]}")
        else:
            unmatched.append(row)
            if len(unmatched) <= 8:
                best_str = f"best={scored[0][0]}:{scored[0][2]}vs{scored[0][3]}" if scored else "no match"
                print(f"  ❌ {league:<10} {home:<10} vs {away:<10} ({best_str})")

print(f"\n\nResults: Matched {len(matched)}, Unmatched {len(unmatched)}")

# Save matched
if matched:
    # Load existing matched
    existing = []
    try:
        with open('matched_fids.json') as f:
            existing = json.load(f)
    except: pass
    
    existing_ids = {m[0] for m in existing}
    new = [(m[0], m[1], m[2]) for m in matched if m[0] not in existing_ids]
    combined = existing + new
    
    with open('matched_fids.json', 'w') as f:
        json.dump(combined, f, ensure_ascii=False)
    print(f"Saved {len(new)} new matches to matched_fids.json (total {len(combined)})")

if unmatched:
    print(f"\nStill unmatched ({len(unmatched)}):")
    for row in unmatched:
        print(f"  {row[2][:10]} {row[5]}: {row[3]} vs {row[4]}")
