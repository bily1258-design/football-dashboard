#!/usr/bin/env python3
"""Debug: why backfill can't match 葡萄牙 vs 西班牙"""
import sys
sys.path.insert(0, 'scripts')
import sqlite3
from backfill_from_500com import parse_wanchang_html, team_match, canonical_500, fetch_page

# 1. Get DB record for 葡萄牙 vs 西班牙 on 2026-07-07
conn = sqlite3.connect('data/football.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute(
    "SELECT id, date, home_team, away_team, kickoff_time, fid_500, actual_outcome "
    "FROM poisson_predictions WHERE home_team=? AND away_team=? AND date=?",
    ('葡萄牙', '西班牙', '2026-07-07')
)
rec = dict(cur.fetchone())
print(f"DB record: {rec}")
# Check canonical
from team_aliases import canonical
print(f"  canonical(home='{rec['home_team']}') = '{canonical(rec['home_team'])}'")
print(f"  canonical(away='{rec['away_team']}') = '{canonical(rec['away_team'])}'")

# 2. Fetch 完场 page
url = 'https://live.500.com/wanchang.php?e=2026-07-07'
html = fetch_page(url, 'gbk')
if not html:
    print("FAILED to fetch page!")
    sys.exit(1)
results = parse_wanchang_html(html)
print(f"Parsed {len(results)} results from wanchang")

# 3. Pre-build by_fid
by_fid = {}
for res in results:
    if res.get('fid'):
        by_fid[res['fid']] = res
unnamed = [r for r in results if not r.get('fid')]
print(f"by_fid: {len(by_fid)}, unnamed: {len(unnamed)}")

# 4. Find the specific match
for fid, res in by_fid.items():
    if '葡萄牙' in res['home'] or '葡萄牙' in res['away']:
        print(f"\n=== Found Portugal match: fid={fid} ===")
        print(f"  home='{res['home']}', away='{res['away']}'")
        print(f"  canonical_500('{res['home']}') = '{canonical_500(res['home'])}'")
        print(f"  canonical_500('{res['away']}') = '{canonical_500(res['away'])}'")
        tm = team_match(rec['home_team'], rec['away_team'], res['home'], res['away'])
        print(f"  team_match() = {tm}")
        if not tm:
            print(f"  MATCH FAILED!")
            print(f"  '{canonical_500(res['home'])}' == '{canonical(rec['home_team'])}' → {canonical_500(res['home']) == canonical(rec['home_team'])}")
            print(f"  '{canonical_500(res['away'])}' == '{canonical(rec['away_team'])}' → {canonical_500(res['away']) == canonical(rec['away_team'])}")

# 5. Try the FULL matching loop (same as backfill_db)
print("\n=== Full matching loop ===")
matched_res = None
for rec2 in [rec]:
    if rec2.get('fid_500') and rec2['fid_500'] in by_fid:
        matched_res = by_fid[rec2['fid_500']]
        print(f"Match by fid: {matched_res}")
    elif by_fid:
        for fid, res in by_fid.items():
            if team_match(rec2['home_team'], rec2['away_team'], res['home'], res['away']):
                matched_res = res
                print(f"Match by team: fid={fid} {res['home']} vs {res['away']} => {res['score']}")
                break
        if matched_res is None:
            print("NO match found in by_fid!")
    if matched_res is None and unnamed:
        for res in unnamed:
            if team_match(rec2['home_team'], rec2['away_team'], res['home'], res['away']):
                matched_res = res
                print(f"Match in unnamed: {res}")
                break

conn.close()
