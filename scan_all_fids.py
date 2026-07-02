#!/usr/bin/env python3
"""Scan 500.com for all fids from historical (wanchang) and future (weekfixture) dates"""
import urllib.request, re, json, sqlite3, time, sys

def fetch_url(url, timeout=20):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=timeout)
            chunks = []
            while True:
                try:
                    chunk = resp.read(65536)
                    if not chunk: break
                    chunks.append(chunk)
                except Exception:
                    break
            return b"".join(chunks).decode('gb2312', errors='ignore')
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"  FAILED after 3 attempts: {e}", file=sys.stderr)
            return None

db = sqlite3.connect('data/football.db')
cur = db.cursor()
cur.execute("SELECT DISTINCT substr(date,1,10) FROM poisson_predictions WHERE fid_500 IS NULL OR fid_500 = '' ORDER BY date")
dates_needed = [r[0] for r in cur.fetchall()]
cur.execute("SELECT DISTINCT substr(date,1,10) FROM poisson_predictions ORDER BY date")
all_dates = [r[0] for r in cur.fetchall()]

print(f"Missing-fid dates: {len(dates_needed)}")
print(f"All dates in DB: {len(all_dates)} (from {all_dates[0]} to {all_dates[-1]})")

all_data = {}

# Scan wanchang for all dates that have missing fids
for dt in dates_needed:
    url = f'https://live.500.com/wanchang.php?e={dt}'
    html = fetch_url(url)
    if html is None:
        continue
    matches = re.findall(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]+)"', html)
    for fid, gy in matches:
        parts = gy.split(",")
        if len(parts) >= 3:
            all_data[(parts[0], parts[1], parts[2])] = fid
    print(f"  wanchang {dt}: {len(matches)} matches (total {len(all_data)})")

# Scan future from weekfixture
for e in range(0, 8):
    url = f'https://live.500.com/weekfixture.php?e={e}'
    html = fetch_url(url)
    if html is None:
        continue
    matches = re.findall(r'<tr[^>]*id="a(\d+)"[^>]*gy="([^"]+)"', html)
    for fid, gy in matches:
        parts = gy.split(",")
        if len(parts) >= 3:
            all_data[(parts[0], parts[1], parts[2])] = fid
    print(f"  weekfixture e={e}: {len(matches)} matches (total {len(all_data)})")

# Save
with open('500_data.json', 'w') as f:
    json.dump([[k[0], k[1], k[2], v] for k, v in all_data.items()], f, ensure_ascii=False)
print(f"\nSaved {len(all_data)} entries to 500_data.json")
