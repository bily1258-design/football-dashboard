#!/usr/bin/env python3
"""从GitHub Release下载football.db（带重试）"""
import urllib.request, json, os, sys, time

REPO = 'bily1258-design/football-dashboard'
TAG = 'db-latest'
TOKEN = os.environ.get('GITHUB_TOKEN', '')
OUT = sys.argv[1] if len(sys.argv) > 1 else 'data/shared_state/football.db'

headers = {'User-Agent': 'download_db'}
if TOKEN:
    headers['Authorization'] = f'token {TOKEN}'

# 获取asset URL
for attempt in range(3):
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{REPO}/releases/tags/{TAG}',
            headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        release = json.loads(resp.read())
        break
    except Exception as e:
        print(f"获取Release信息失败(尝试{attempt+1}/3): {e}")
        if attempt < 2:
            time.sleep(5)

for asset in release.get('assets', []):
    if asset['name'] == 'football.db':
        url = asset['url']
        size = asset['size']
        print(f"下载 {size} bytes...")
        
        for attempt in range(3):
            try:
                dl_headers = dict(headers)
                dl_headers['Accept'] = 'application/octet-stream'
                req2 = urllib.request.Request(url, headers=dl_headers)
                resp2 = urllib.request.urlopen(req2, timeout=300)
                
                os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
                with open(OUT, 'wb') as f:
                    while True:
                        chunk = resp2.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        print(f"  {f.tell()}/{size} bytes", end='\r')
                
                actual = os.path.getsize(OUT)
                print(f"\n✅ {OUT}: {actual} bytes")
                if actual == size:
                    sys.exit(0)
                else:
                    print(f"⚠️ 大小不匹配: 期望{size}, 实际{actual}")
            except Exception as e:
                print(f"下载失败(尝试{attempt+1}/3): {e}")
                if attempt < 2:
                    time.sleep(10)

print("❌ 下载失败")
sys.exit(1)
