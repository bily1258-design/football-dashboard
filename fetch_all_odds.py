#!/usr/bin/env python3
"""Batch fetch odds for all FIDs missing odds data."""
import subprocess
import sys
import time

FIDS = [
    1363369, 1363519, 1363595, 1363713, 1363778, 1363835, 1363899,
    1370857, 1370858, 1372737, 1372738, 1372740, 1372741,
    1373535, 1373536, 1373537, 1373538,
    1373539, 1373540, 1373541, 1373542,
    1382682, 1382683, 1382684, 1382685, 1382686
]

COMPANIES = ['pinnacle', 'bet365', 'liji', 'mingsheng', 'william']

total = len(FIDS) * len(COMPANIES)
done = 0
errors = 0

for fid in FIDS:
    for company in COMPANIES:
        try:
            r = subprocess.run(
                [sys.executable, 'scripts/fetch_500com_odds.py',
                 '--db', 'data/football.db',
                 '--fid', str(fid),
                 '--company', company],
                capture_output=True, text=True, timeout=30
            )
            done += 1
            if r.returncode != 0:
                errors += 1
                print(f"ERR: fid={fid} company={company}: {r.stderr.strip()[:100]}")
            else:
                out = r.stdout.strip()
                if out:
                    print(f"OK: fid={fid} company={company}: {out[:80]}")
        except subprocess.TimeoutExpired:
            errors += 1
            print(f"TIMEOUT: fid={fid} company={company}")
        except Exception as e:
            errors += 1
            print(f"EXCEPTION: fid={fid} company={company}: {e}")
        
        # Small delay to be polite
        time.sleep(0.3)

print(f"\n=== DONE: {done}/{total} attempts, {errors} errors ===")
