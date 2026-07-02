#!/usr/bin/env python3
"""Smart match: fetch 500.com page per date, find match by league + team"""
import urllib.request, re, sqlite3, json, time

# Load existing matches to avoid redoing
matched_mids = set()
if __name__ == '__main__':
    try:
        with open('matched_fids.json') as f:
            for m in json.load(f):
                matched_mids.add(m[0])
    except: pass

db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT id, match_id, date, home_team, away_team, league FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' ORDER BY date")
missing = [r for r in cur.fetchall() if r[0] not in matched_mids]
print(f"Unmatched: {len(missing)}")

# Manual mapping for known discrepancies
MANUAL_MAP = {
    # DB team -> possible 500.com names
    '迈季宽广': ['新未来城体育', '新未来城'],
    '布赖合作': ['布赖代合作', '布赖代合作青年队'],
    '拉斯永恒': ['阿尔拉斯永恒'],
    '赫尔火花': ['赫尔辛基', '赫尔城'],  # unsure
    '塞那乔其': ['塞那乔恩'],
    '塞纳乔琪': ['塞那乔恩', '塞那乔其'],
    '坦山猫': ['坦佩雷联'],
    '塞伊奈': ['塞那乔恩', 'SJK'],
    '库奥皮奥': ['古比斯', '库普斯'],
    '托林斯': ['托里恩斯', '托林斯'],
    '胡巴卡德': ['胡巴尔卡德', '阿尔卡德西亚'],
    '埃尔夫斯堡': ['埃尔夫斯堡'],
    '哈斯塔德': ['哈尔姆斯塔德', '哈姆斯塔德'],
    '安山新军': ['安山小绿人', '安山'],
    '时刻准备': ['最强者', '阿拉维准备'],
    '米拉索尔': ['米拉索尔'],
    '瓦斯科伽马': ['瓦斯科达伽马'],
    '费特斯塔': ['腓特烈斯塔'],
    '华奇巴托': ['瓦奇巴托'],
    '布星': ['布加勒斯特星'],
    '布特快速': ['布加勒斯特快速'],
    '克约大学': ['克拉约瓦大学'],
    '克卢大学': ['克卢日大学'],
    '塞雷那': ['塞雷那', '拉塞雷纳'],
    '利雅得': ['利雅得青年人', '利雅得体育', '利雅得新月'],
    '费城': ['费城联合'],
    '迈国际': ['迈阿密国际'],
    '圣吉联合': ['圣吉罗斯联合'],
    '阿独立': ['阿根廷独立'],
    '布迪纳摩': ['布加勒斯特迪纳摩', '萨格勒布迪纳摩'],
    '瓦斯科伽马': ['瓦斯科达伽马'],
    '中央大学': ['委内瑞拉中央大学'],
    '帕梅拉斯': ['帕尔梅拉斯'],
    '米竞技': ['米内罗竞技'],
    '戈亚斯': ['戈亚斯'],
    '累西腓': ['累西腓体育'],
}

# Collect by date
from collections import defaultdict
by_date = defaultdict(list)
for row in missing:
    by_date[row[2][:10]].append(row)

new_matches = []
still_unmatched = []

for date_str, rows in sorted(by_date.items()):
    url = f"https://live.500.com/wanchang.php?e={date_str}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        chunks = []
        while True:
            try: chunk = resp.read(65536); chunks.append(chunk)
            except: break
        data = b"".join(chunks).decode("gb2312", errors="ignore")
    except Exception as e:
        print(f"[{date_str}] ERROR: {e}")
        for row in rows:
            still_unmatched.append(row)
        continue
    
    # Parse all matches on this date
    all_matches = []
    for fid, gy in re.findall(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]+)"', data):
        parts = gy.split(",")
        if len(parts) >= 3:
            all_matches.append((fid, parts[0], parts[1], parts[2]))
    
    for row in rows:
        mid, match_id, dt, home, away, league = row
        
        # Build possible names
        home_aliases = MANUAL_MAP.get(home, []) + [home]
        away_aliases = MANUAL_MAP.get(away, []) + [away]
        
        best_match = None
        best_score = 0
        
        for fid, l500, h500, a500 in all_matches:
            # League check (relaxed)
            league_match = False
            if league[:2] in l500 or l500[:2] in league[:2]:
                league_match = True
            # Also try general sports check
            common_chars = len(set(league[:3]) & set(l500[:3]))
            if common_chars >= 2:
                league_match = True
            
            # Home check
            home_ok = False
            for alias in home_aliases:
                if alias in h500 or h500 in alias:
                    home_ok = True
                    break
                # Character overlap >= 70%
                if len(set(alias) & set(h500)) >= 0.7 * min(len(alias), len(h500)):
                    home_ok = True
                    break
            
            if not home_ok:
                continue
            
            # Away check
            away_ok = False
            for alias in away_aliases:
                if alias in a500 or a500 in alias:
                    away_ok = True
                    break
                if len(set(alias) & set(a500)) >= 0.7 * min(len(alias), len(a500)):
                    away_ok = True
                    break
            
            if not away_ok:
                continue
            
            # Score
            score = 0
            if league_match: score += 2
            for alias in home_aliases:
                if alias == h500: score += 10
                elif alias in h500 or h500 in alias: score += 5
                else: score += 2 * len(set(alias) & set(h500)) / max(len(alias), 1)
            for alias in away_aliases:
                if alias == a500: score += 10
                elif alias in a500 or a500 in alias: score += 5
                else: score += 2 * len(set(alias) & set(a500)) / max(len(alias), 1)
            
            if score > best_score:
                best_score = score
                best_match = (fid, l500, h500, a500)
        
        if best_match and best_score >= 3.0:
            new_matches.append((mid, match_id, best_match[0]))
            print(f"  ✅ {date_str} {league}: {home} vs {away} -> fid={best_match[0]} ({best_match[1]}: {best_match[2]} vs {best_match[3]})")
        else:
            still_unmatched.append(row)
            if len(still_unmatched) <= 10:
                score_str = f"score={best_score:.1f}:{best_match[1]}:{best_match[2]}vs{best_match[3]}" if best_match else "none"
                print(f"  ❌ {date_str} {league}: {home} vs {away} ({score_str})")

print(f"\nNew matches: {len(new_matches)}")
print(f"Still unmatched: {len(still_unmatched)}")

# Save new matches
if new_matches:
    with open('matched_fids_v3.json', 'w') as f:
        json.dump(new_matches, f, ensure_ascii=False)
    print(f"Saved to matched_fids_v3.json")

# Show still unmatched
if still_unmatched:
    print("\nUnmatched list:")
    for row in still_unmatched:
        mid, match_id, dt, home, away, league = row
        print(f"  {dt[:10]} {league}: {home} vs {away}")
