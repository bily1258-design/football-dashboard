#!/usr/bin/env python3
import json

r = json.load(open('docs/data/results.json'))
matches = r['matches']

targets = []
for x in matches:
    mt = x.get('match_time', '')
    score = x.get('score', '')
    if score and score != '-':
        continue
    if '07-18' in str(mt) and '00:00' not in str(mt):
        has_odds = x.get('odds_pinnacle_win') or x.get('odds_hkjc_win')
        targets.append({
            'fid': x.get('fid'),
            'time': mt,
            'event': x['event'],
            'home': x['home_team'],
            'away': x['away_team'],
            'odds': has_odds,
        })

print(f'目标 {len(targets)} 场:')
for t in targets:
    flag = ' ✓有赔率' if t['odds'] else ' ✗无赔率'
    print(f"  fid={t['fid']} {t['time']} {t['event']:12s} {t['home']:16s} vs {t['away']:16s}{flag}")
