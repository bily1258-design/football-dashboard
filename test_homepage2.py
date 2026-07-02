#!/usr/bin/env python3
import urllib.request, re

# 试试不同 User-Agent 稳定获取首页
uas = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
]

for ua in uas:
    try:
        req = urllib.request.Request('https://live.500.com/', headers={
            'User-Agent': ua,
        })
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read()
        pat = rb'id="a(\d+)"[^>]*gy="([^"]*)"'
        matches = re.findall(pat, body)
        print(f'UA: {ua[:50]}... -> {resp.url}, {len(body)}字节, {len(matches)}场')
    except Exception as e:
        print(f'UA: {ua[:50]}... -> 错误: {e}')

# 直接用手机版格式
print()
print('=== 手机版 live.m.500.com ===')
try:
    req = urllib.request.Request('http://live.m.500.com/score/index.html', headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36',
    })
    resp = urllib.request.urlopen(req, timeout=10)
    body = resp.read()
    print(f'手机版: {len(body)}字节')
    # 手机版可能有 json 数据或不同结构
    text = body.decode('gbk', errors='replace')
    # 搜索关键词
    for kw in ['西班牙', '奥地利', '世界杯', 'fid']:
        if kw in text:
            print(f'  包含 "{kw}"')
    
    # 找数据相关的script
    for line in text.split('\n'):
        line_s = line.strip()
        if '西班牙' in line_s:
            print(f'  西班牙行: {line_s[:200]}')
    
    # 试试 json/数据段
    import json
    # 找 500.com API
    urls_found = re.findall(r'(https?://[^\s"\']+(?:api|json|data)[^\s"\']*)', text)
    print(f'  API URL: {urls_found}')
except Exception as e:
    print(f'手机版错误: {e}')
