#!/usr/bin/env python3
import urllib.request, re

# 不follow redirect
class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        print(f'重定向: {code} -> {newurl}')
        return None

opener = urllib.request.build_opener(NoRedirectHandler)
req = urllib.request.Request('https://live.500.com/', headers={
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
})
try:
    resp = opener.open(req, timeout=20)
    print(f'首页状态: {resp.status}')
    print(f'首页内容大小: {len(resp.read())}')
except urllib.error.HTTPError as e:
    print(f'首页 HTTPError: {e.code} -> {e.headers.get("Location","")}')
except Exception as e:
    print(f'首页其他: {e}')

# 试试手机版
print()
url_m = 'http://live.m.500.com/score/index.html'
try:
    req2 = urllib.request.Request(url_m, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
    })
    resp2 = urllib.request.urlopen(req2, timeout=10)
    body2 = resp2.read()
    print(f'手机版: {resp2.status}, {len(body2)}字节')
    text2 = body2.decode('gbk', errors='replace')
    
    # 搜索西班牙
    for line in text2.split('\n'):
        if '西班牙' in line:
            print(f'  西班牙行: {line.strip()[:200]}')
    
    # 检查id="a模式
    has_id_a = 'id="a' in text2
    print(f'包含 id="a: {has_id_a}')
    
    # 世界杯行数
    wc_lines = [l.strip() for l in text2.split('\n') if '世界杯' in l]
    print(f'世界杯行: {len(wc_lines)}')
    for l in wc_lines[:5]:
        print(f'  {l[:200]}')
    
    # 搜索fid
    fids = re.findall(r'fid=(\d+)', text2)
    print(f'fid模式: {len(fids)}个')
    for f in fids[:5]:
        print(f'  fid={f}')
except Exception as e:
    print(f'手机版错误: {e}')

print()
# 最后试一次live.500.com，用resp.read()而不是先检查
try:
    req3 = urllib.request.Request('https://live.500.com/', headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    resp3 = urllib.request.urlopen(req3, timeout=10)
    body3 = resp3.read()
    print(f'桌面版直连: {resp3.url}, {len(body3)}字节')
    if len(body3) > 5000:
        print('返回了完整页面')
        text3 = body3.decode('gbk', errors='replace')
        pat = rb'id="a(\d+)"[^>]*gy="([^"]*)"'
        matches = re.findall(pat, body3)
        print(f'  regex匹配: {len(matches)}个')
    else:
        print(f'  内容: {body3[:300]}')
except Exception as e:
    print(f'桌面版错误: {e}')
