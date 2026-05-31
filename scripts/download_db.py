#!/usr/bin/env python3
"""从GitHub Release下载football.db"""
import urllib.request, json, os, sys

REPO = 'bily1258-design/football-dashboard'
TAG = 'db-latest'
TOKEN = os.environ.get('GITHUB_TOKEN', '')
OUT = sys.argv[1] if len(sys.argv) > 1 else 'data/shared_state/football.db'

# 获取release信息
headers = {'User-Agent': 'download_db'}
if TOKEN:
    headers['Authorization'] = f'token {TOKEN}'

req = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/tags/{TAG}', headers=headers)
resp = urllib.request.urlopen(req, timeout=30)
release = json.loads(resp.read())

for asset in release.get('assets', []):
    if asset['name'] == 'football.db':
        url = asset['url']
        dl_headers = dict(headers)
        dl_headers['Accept'] = 'application/octet-stream'
        print(f"下载 {asset['size']} bytes...")
        req2 = urllib.request.Request(url, headers=dl_headers)
        resp2 = urllib.request.urlopen(req2, timeout=120)
        data = resp2.read()
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, 'wb') as f:
            f.write(data)
        print(f"✅ {OUT}: {len(data)} bytes")
        sys.exit(0)

print("❌ Release中未找到football.db")
sys.exit(1)
