import subprocess, json, glob
from datetime import datetime, timedelta

# Run backfill for all dates
dates = []
d = datetime(2026, 7, 18)
today = datetime(2026, 7, 24)
while d <= today:
    dates.append(d.strftime('%Y-%m-%d'))
    d += timedelta(days=1)

for date_str in dates:
    fpath = f'data/matches_hkjc_{date_str.replace("-", "")}.json'
    print(f'\n{"="*60}')
    print(f'>>> {date_str} ({fpath})')
    print(f'{"="*60}')
    r = subprocess.run(
        ['python3', 'scripts/fetch_hkjc_all.py', '--date', date_str, '--backfill'],
        capture_output=True, text=True, cwd='/data/data/com.termux/files/home/football-dashboard',
        timeout=60
    )
    for line in r.stdout.split('\n'):
        if 'BACKFILL' in line or '未' in line or '0 场' in line:
            print(f'  {line}')
    if r.stderr and 'Deprecation' not in r.stderr:
        for line in r.stderr.split('\n')[:3]:
            print(f'  [ERR] {line}')

# Summary
print(f'\n{"="*60}')
print('SUMMARY')
print(f'{"="*60}')
for date_str in dates:
    fpath = f'data/matches_hkjc_{date_str.replace("-", "")}.json'
    try:
        with open(fpath) as f:
            data = json.load(f)
        total = len(data['matches'])
        unscored = [m for m in data['matches'] if not m.get('score')]
        past_unscored = [m for m in unscored if m.get('match_time')]
        print(f'{date_str}: {total}场, 缺{len(unscored)}场(其中已过{len(past_unscored)}场)')
    except:
        print(f'{date_str}: 无法读取')
