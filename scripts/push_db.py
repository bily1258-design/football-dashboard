#!/usr/bin/env python3
"""push_db.py — 将 football.db 推送到 GitHub Release

push前自动从Release下载旧DB，将旧DB中的赛果(actual_outcome)
回填到新DB的同key记录，防止赛果丢失。

用法：
  python push_db.py                          # 推送当前DB
  python push_db.py --db /path/to/other.db   # 指定DB路径
  python push_db.py --no-merge               # 跳过赛果回填，直接覆盖
"""

import os, sys, json, urllib.request, urllib.error, tempfile, sqlite3

REPO = 'bily1258-design/football-dashboard'
TAG = 'db-latest'
DB_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
    '..', 'data', 'football.db')


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
    url = f'https://api.github.com/repos/{REPO}/releases'
    return api_request(url, token, 'POST', {
        'tag_name': TAG,
        'name': 'Latest DB',
        'body': 'Auto-updated football.db',
        'draft': False,
        'prerelease': False,
    })


def download_release_db(token):
    """从Release下载旧DB到临时文件，返回路径或None"""
    release = api_request(f'https://api.github.com/repos/{REPO}/releases/tags/{TAG}', token)
    if not release:
        return None
    assets = release.get('assets', [])
    for a in assets:
        if a['name'] == 'football.db':
            url = a['url']  # API URL需要Accept header
            req = urllib.request.Request(url, headers={
                'Authorization': f'token {token}',
                'User-Agent': 'Python/3.13',
                'Accept': 'application/octet-stream',
                'X-GitHub-Api-Version': '2022-11-28',
            })
            try:
                resp = urllib.request.urlopen(req, timeout=60)
                tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
                tmp.write(resp.read())
                tmp.close()
                print(f'📥 下载旧DB: {os.path.getsize(tmp.name)//1024}KB')
                return tmp.name
            except Exception as e:
                print(f'⚠️ 下载旧DB失败: {e}')
                return None
    return None


def merge_outcomes(new_db_path, old_db_path):
    """从旧DB回填赛果到新DB的同key记录
    
    同(date, home_team, away_team)的记录：
    - 旧DB有actual_outcome，新DB没有 → 回填
    - 新DB已有actual_outcome → 保留新DB的
    """
    old_conn = sqlite3.connect(old_db_path)
    old_c = old_conn.cursor()

    new_conn = sqlite3.connect(new_db_path)
    new_c = new_conn.cursor()

    # 读取旧DB的赛果
    old_c.execute('SELECT date, home_team, away_team, actual_outcome, deviation_analysis FROM poisson_predictions WHERE actual_outcome IS NOT NULL AND actual_outcome != ""')
    old_outcomes = {}
    for r in old_c.fetchall():
        key = (r[0], r[1], r[2])
        old_outcomes[key] = (r[3], r[4])  # (actual_outcome, deviation_analysis)

    # 遍历新DB，回填缺失的赛果
    new_c.execute('SELECT date, home_team, away_team, actual_outcome FROM poisson_predictions')
    filled = 0
    for r in new_c.fetchall():
        key = (r[0], r[1], r[2])
        curr_outcome = r[3]
        # 新DB没有赛果，旧DB有 → 回填
        if (not curr_outcome or curr_outcome == 'None' or curr_outcome == '') and key in old_outcomes:
            outcome, deviation = old_outcomes[key]
            new_c.execute(
                'UPDATE poisson_predictions SET actual_outcome = ? WHERE date = ? AND home_team = ? AND away_team = ?',
                (outcome, key[0], key[1], key[2])
            )
            if deviation:
                new_c.execute(
                    'UPDATE poisson_predictions SET deviation_analysis = ? WHERE date = ? AND home_team = ? AND away_team = ?',
                    (deviation, key[0], key[1], key[2])
                )
            filled += 1

    new_conn.commit()
    new_conn.close()
    old_conn.close()
    return filled


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
        resp = urllib.request.urlopen(req, timeout=180)
        result = json.loads(resp.read())
        print(f'✅ DB已上传: {result["browser_download_url"]} ({len(content)//1024}KB)')
        return True
    except urllib.error.HTTPError as e:
        print(f'❌ 上传失败: {e.code} {e.read().decode()[:200]}')
        return False


def push_db(db_path=None, no_merge=False):
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

    # 赛果回填：从Release下载旧DB，把赛果merge进新DB
    if not no_merge:
        old_db = download_release_db(token)
        if old_db:
            filled = merge_outcomes(db_path, old_db)
            print(f'🔄 赛果回填: {filled} 条记录')
            try:
                os.unlink(old_db)
            except:
                pass
        else:
            print('⚠️ 无旧DB可合并，直接推送')

    release = ensure_release(token)
    if not release:
        print('[ERROR] 无法创建/获取 Release')
        return False

    return upload_asset(token, release['id'], db_path)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=str, default=None)
    p.add_argument('--no-merge', action='store_true', help='跳过赛果回填')
    args = p.parse_args()
    sys.exit(0 if push_db(args.db, args.no_merge) else 1)
