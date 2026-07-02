#!/usr/bin/env python3
import urllib.request, re, time

# 模拟脚本的三次连续请求
ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

for i, url in enumerate([
    'https://live.500.com/wanchang.php',
    'https://live.500.com/',
    'https://live.500.com/weekfixture.php',
]):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': ua})
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read()
        pat = rb'id="a(\d+)"[^>]*gy="([^"]*)"'
        matches = re.findall(pat, body)
        print(f'{i+1}. {url:45s} -> {resp.url}, {len(body):>6}字节, {len(matches)}场')
        print(f'   最终URL: {resp.url}')
    except Exception as e:
        print(f'{i+1}. {url:45s} -> 错误: {e}')
    time.sleep(1)
PYEOF
