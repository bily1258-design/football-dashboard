#!/usr/bin/env python3
"""push_db.py — 将 football.db 推送到 GitHub Release

GA workflow 从 Release 下载 DB，避免二进制文件污染 git 历史

用法：
  python push_db.py                          # 推送当前DB
  python push_db.py --db /path/to/other.db   # 指定DB路径
"""

import os, sys, json, urllib.request, urllib.error, hashlib

REPO = 'bily1258-design/football-dashboard'
TAG = 'db-latest'
DB_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
    '..', 'data', 'shared_state', 'football.db')


def get_token():
    token = os.environ.get('GITHUB_TOKEN', '')
    if token:
        return token
    secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
        '..', '..', 'SECRET.md')
    if os.path.exists(secret_path):
        with open(secret_path, 'r') as f:
            for line in f:
                if 'GitHub PAT' in line and '"' in line:
                    return line.split('"')[1]
    return ''


def api_request(url, token, method='GET', data=None):
    headers = {
        'Authorization': f'token {token}',
        'User-Agent': 'Python/3.13',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if data is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        if resp.status == 204:
            return None
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def ensure_release(token):
    """确保 Release 存在，返回 release info"""
    url = f'https://api.github.com/repos/{REPO}/releases/tags/{TAG}'
    release = api_request(url, token)
    if release:
        return release
    # 创建 Release
    url = f'https://api.github.com/repos/{REPO}/releases'
    return api_request(url, token, 'POST', {
        'tag_name': TAG,
        'name': 'Latest DB',
        'body': 'Auto-updated football.db',
        'draft': False,
        'prerelease': False,
    })


def upload_asset(token, release_id, db_path):
    """上传 DB 到 Release"""
    filename = 'football.db'
    with open(db_path, 'rb') as f:
        content = f.read()

    # 先删除旧资产
    url = f'https://api.github.com/repos/{REPO}/releases/{release_id}/assets'
    assets = api_request(url, token) or []
    for a in assets:
        if a['name'] == filename:
            api_request(a['url'], token, 'DELETE')
            break

    # 上传新资产（用URL参数，比multipart更可靠）
    upload_url = f'https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets?name={filename}'
    with open(db_path, 'rb') as f:
        content = f.read()

    req = urllib.request.Request(upload_url, data=content, headers={
        'Authorization': f'token {token}',
        'User-Agent': 'Python/3.13',
        'Content-Type': 'application/octet-stream',
        'X-GitHub-Api-Version': '2022-11-28',
    }, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        print(f'✅ DB已上传: {result["browser_download_url"]} ({len(content)//1024}KB)')
        return True
    except urllib.error.HTTPError as e:
        print(f'❌ 上传失败: {e.code} {e.read().decode()[:200]}')
        return False


def push_db(db_path=None):
    if not db_path:
        db_path = DB_DEFAULT
    if not os.path.exists(db_path):
        print(f'[ERROR] DB not found: {db_path}')
        return False

    token = get_token()
    if not token:
        print('[ERROR] GITHUB_TOKEN not set')
        return False

    print(f'📦 推送 DB: {db_path} ({os.path.getsize(db_path)//1024}KB)')

    release = ensure_release(token)
    if not release:
        print('[ERROR] 无法创建/获取 Release')
        return False

    return upload_asset(token, release['id'], db_path)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=str, default=None)
    args = p.parse_args()
    sys.exit(0 if push_db(args.db) else 1)
