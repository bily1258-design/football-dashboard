#!/usr/bin/env python3
"""
fetch_analysis_data.py — 从 titan007 分析页提取完整统计数据

数据来源:
  1. analysis 页 (zq.titan007.com/analysis/{sid}.htm) — h_data/a_data 近N场赛果
  2. detail 页 (bf.titan007.com/detail/{sid}.htm) — teamTvStatisticData 技术统计

写入:
  - match_analysis 表 (按SID索引，JSON字段存储各类统计)
  - team_stats_cache 表 (按球队名+联赛索引，聚合统计缓存)

用法:
  python3 scripts/fetch_analysis_data.py [sid1 sid2 ...]
    不给参数则从 results.json 读取待处理比赛
"""

import json
import re
import ast
import sqlite3
import sys
import os
import time
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── 配置 ──────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'football.db')
RESULTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'results.json')
RESULTS_PATH_ALT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'data', 'results.json')
REQUEST_DELAY = 1.0  # 请求间隔(秒)，避免被ban
MAX_RETRIES = 3

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept': 'text/html,application/xhtml+xml',
    'Connection': 'keep-alive',
}

# ─── 工具函数 ──────────────────────────────────────────

def safe_fetch(url, headers=None, timeout=15):
    """带重试的 HTTP GET"""
    hdrs = {**HEADERS, **(headers or {})}
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(url, headers=hdrs)
            resp = urlopen(req, timeout=timeout)
            raw = resp.read()
            # 检测编码
            ct = resp.headers.get('Content-Type', '')
            if 'gbk' in ct or 'gb2312' in ct:
                return raw.decode('gbk', errors='replace')
            return raw.decode('utf-8', errors='replace')
        except (URLError, HTTPError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(REQUEST_DELAY * (attempt + 1))
                continue
            raise
    return ""


def safe_parse_js_array(raw_str):
    """安全解析JS数组（清理HTML标签后解析）"""
    cleaned = re.sub(r'<[^>]+>', '', raw_str)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    try:
        return ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        pass
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return []


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── 解析层 ──────────────────────────────────────────

def fetch_analysis_page(sid):
    """获取分析页并提取核心JS变量"""
    url = f'https://zq.titan007.com/analysis/{sid}.htm'
    html = safe_fetch(url)
    if not html or 'Page Not Found' in html:
        return None

    result = {'sid': sid, 'html_size': len(html)}

    # 基本变量
    for pat, key in [
        (r'var\s+hometeam\s*=\s*["\']([^"\']+)', 'home_team'),
        (r'var\s+guestteam\s*=\s*["\']([^"\']+)', 'away_team'),
        (r'var\s+strTime\s*=\s*["\']([^"\']+)', 'match_time'),
    ]:
        m = re.search(pat, html)
        if m:
            result[key] = m.group(1)

    # h_data (主队近绩) / a_data (客队近绩) / v_data (历史交锋)
    for var_name in ['h_data', 'a_data', 'v_data']:
        m = re.search(rf'var\s+{var_name}\s*=\s*(.*?]);', html, re.DOTALL)
        if m:
            parsed = safe_parse_js_array(m.group(1))
            result[var_name] = parsed if isinstance(parsed, list) else []
        else:
            result[var_name] = []

    # 联赛名：从 h_data[0][2] 取（无独立 league_name 变量）
    if result.get('h_data') and len(result['h_data']) > 0:
        first_entry = result['h_data'][0]
        if isinstance(first_entry, list) and len(first_entry) > 2:
            result['league_name'] = str(first_entry[2])

    # 赔率
    for var_name, key in [('Vs_eOdds', 'eOdds'), ('Vs_hOdds', 'hOdds')]:
        m = re.search(rf'var\s+{var_name}\s*=\s*(.*?]);', html, re.DOTALL)
        if m:
            parsed = safe_parse_js_array(m.group(1))
            result[key] = parsed if isinstance(parsed, list) else []

    return result


def fetch_tech_stats(sid):
    """获取 detail 页的技术统计数据 (teamTvStatisticData)"""
    url = f'https://bf.titan007.com/detail/{sid}.htm'
    html = safe_fetch(url, headers={
        'Referer': f'https://bf.titan007.com/football/Over_{datetime.now().strftime("%Y%m%d")}.htm',
    })
    if not html:
        return None

    result = {}

    # teamTvStatisticData 变量
    m = re.search(r'teamTvStatisticData\s*=\s*"([^"]+)"', html)
    if m:
        result['teamTvStatisticData'] = m.group(1)
        result['teamTvStats'] = parse_team_tv_stats(m.group(1))

    # 比赛状态 / 比分
    m = re.search(r'var\s+homeTeamName\s*=\s*["\']([^"\']+)', html)
    if m:
        result['home_team'] = m.group(1)
    m = re.search(r'var\s+guestTeamName\s*=\s*["\']([^"\']+)', html)
    if m:
        result['away_team'] = m.group(1)

    return result


def parse_team_tv_stats(raw):
    """
    解析 teamTvStatisticData 编码变量
    
    格式: idx,home_val,away_val,home_pct,away_pct^idx,...
    
    已知索引:
      0 = 总进球 (goals total)
      2 = 控球率 (possession %)
      4 = 被射门 (shots total)
      5 = 射正 (shots on target)
      6 = 传球 (passes total)
      7 = 传球成功 (passes completed)
      8 = 犯规 (fouls)
      9 = 黄牌 (yellow cards)
      10 = 红牌 (red cards)
      11 = 角球 (corners)
    """
    if not raw:
        return {}
    
TECH_STAT_LABELS = {
    0: '总进球',
    2: '黄牌',
    4: '射门',
    5: '射正',
    6: '进攻',
    7: '危险进攻',
    11: '控球率',
    14: '传球成功率',
}


def parse_team_tv_stats(raw):
    """
    解析 teamTvStatisticData 编码字符串。
    格式: category,home_val,away_val,home_pct,away_pct
    百分比类(index 11 & 14): home_val 和 away_val 带 % 后缀
    """
    if not raw:
        return {}
    result = {}
    sections = raw.split('^')
    for sec in sections:
        parts = sec.split(',')
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
            home_raw = parts[1]
            away_raw = parts[2]
            home_pct = float(parts[3]) if parts[3] else 0
            away_pct = float(parts[4]) if parts[4] else 0
        except (ValueError, IndexError):
            continue

        label = TECH_STAT_LABELS.get(idx, f'stat_{idx}')
        # 解析数值（移除 % 后缀）
        is_pct = '%' in home_raw or '%' in away_raw
        try:
            home_val = float(home_raw.rstrip('%'))
        except ValueError:
            home_val = 0
        try:
            away_val = float(away_raw.rstrip('%'))
        except ValueError:
            away_val = 0

        entry = {
            'home': home_val if is_pct else int(home_val) if home_val == int(home_val) else home_val,
            'away': away_val if is_pct else int(away_val) if away_val == int(away_val) else away_val,
            'home_pct': home_pct,
            'away_pct': away_pct,
        }
        result[label] = entry
    return result


# ─── 统计计算层 ──────────────────────────────────────

def compute_form_stats(form_data, team_name):
    """
    从 h_data 或 a_data 计算球队统计
    
    输入: form_data (list of entries), team_name (球队名)
    
    返回 dict:
      - recent_form: [{date, opponent, score, half_score, result, handicap_result, venue}]
      - summary: 汇总统计
      - handicap_trend: 盘路走势
      - goal_distribution: 入球分布
      - ht_ft: 半全场统计
    """
    if not form_data:
        return {}
    
    stats = {
        'total_matches': len(form_data),
        'recent_results': [],
        'form_summary': {},
    }
    
    wins = draws = losses = 0
    handicap_wins = handicap_draws = handicap_losses = 0
    goals_for = goals_against = 0
    home_goals = away_goals = 0
    ht_win = ht_draw = ht_loss = 0
    ht_ft_matrix = {}  # 'WW', 'WD', 'WL', 'DW', 'DD', 'DL', 'LW', 'LD', 'LL'
    goal_dist = {0: 0, 1: 0, 2: 0, 3: 0, '4plus': 0}
    half_goal_dist = {0: 0, 1: 0, 2: 0, '3plus': 0}
    over_under = {'over': 0, 'under': 0, 'push': 0}
    odd_even = {'odd': 0, 'even': 0}
    
    entries = []
    for entry in form_data:
        if not isinstance(entry, list) or len(entry) < 11:
            continue
        
        try:
            date_str = str(entry[0]) if entry[0] else ''
            home_name = str(entry[5]) if entry[5] else ''
            away_name = str(entry[7]) if entry[7] else ''
            hs = int(entry[8]) if entry[8] is not None else -1
            as_ = int(entry[9]) if entry[9] is not None else -1
            half = str(entry[10]) if entry[10] else ''
            
            # 判断主客场
            is_home = (home_name == team_name)
            
            # 此队得分
            team_score = hs if is_home else as_
            opp_score = as_ if is_home else hs
            
            if team_score < 0 or opp_score < 0:
                continue
            
            # 赛果
            if team_score > opp_score:
                result = '胜'
                wins += 1
            elif team_score == opp_score:
                result = '平'
                draws += 1
            else:
                result = '负'
                losses += 1
            
            goals_for += team_score
            goals_against += opp_score
            
            if is_home:
                home_goals += team_score
            else:
                away_goals += team_score
            
            # 入球分布
            total_goals = team_score + opp_score
            if total_goals <= 3:
                goal_dist[total_goals] = goal_dist.get(total_goals, 0) + 1
            else:
                goal_dist['4plus'] = goal_dist.get('4plus', 0) + 1
            
            # 单双
            if total_goals % 2 == 0:
                odd_even['even'] += 1
            else:
                odd_even['odd'] += 1
            
            # 大小球 (参考线 2.5)
            if total_goals >= 3:
                over_under['over'] += 1
            else:
                over_under['under'] += 1
            
            # 半场
            if half and '-' in half:
                parts = half.split('-')
                try:
                    hh = int(parts[0])
                    ha = int(parts[1])
                    if hh > ha:
                        ht_win += 1
                    elif hh == ha:
                        ht_draw += 1
                    else:
                        ht_loss += 1
                    
                    # 半全场组合
                    ht_result = '胜' if hh > ha else ('平' if hh == ha else '负')
                    ft_result = result
                    ht_ft_key = ht_result + ft_result
                    ht_ft_matrix[ht_ft_key] = ht_ft_matrix.get(ht_ft_key, 0) + 1
                    
                    # 半场进球分布
                    half_total = hh + ha
                    if half_total <= 2:
                        half_goal_dist[half_total] = half_goal_dist.get(half_total, 0) + 1
                    else:
                        half_goal_dist['3plus'] = half_goal_dist.get('3plus', 0) + 1
                except (ValueError, IndexError):
                    pass
            
            # 盘路 (entry[11] = handicap, entry[12] = handicap_result?)
            # [11] = 让球数, [12] = 盘路结果 (1=赢, -1=输, 0=走)
            handicap_result = None
            if len(entry) > 12:
                try:
                    hr = int(entry[12]) if entry[12] is not None else None
                    if hr == 1:
                        handicap_result = '赢'
                        handicap_wins += 1
                    elif hr == -1:
                        handicap_result = '输'
                        handicap_losses += 1
                    elif hr == 0:
                        handicap_result = '走'
                        handicap_draws += 1
                except (ValueError, TypeError):
                    pass
            
            # 对手名
            opponent = away_name if is_home else home_name
            
            entries.append({
                'date': date_str,
                'opponent': opponent,
                'is_home': is_home,
                'score': f"{team_score}-{opp_score}",
                'half_score': half,
                'result': result,
                'handicap_result': handicap_result,
                'goals_for': team_score,
                'goals_against': opp_score,
            })
        except (ValueError, IndexError):
            continue
    
    stats['recent_results'] = entries
    stats['form_summary'] = {
        'wins': wins, 'draws': draws, 'losses': losses,
        'goals_for': goals_for, 'goals_against': goals_against,
        'goals_per_match': round(goals_for / max(wins+draws+losses, 1), 2),
        'concede_per_match': round(goals_against / max(wins+draws+losses, 1), 2),
        'win_rate': round(wins / max(wins+draws+losses, 1) * 100, 1),
        'home_goals': home_goals, 'away_goals': away_goals,
    }
    stats['handicap_trend'] = {
        'wins': handicap_wins, 'draws': handicap_draws, 'losses': handicap_losses,
        'win_rate': round(handicap_wins / max(handicap_wins+handicap_draws+handicap_losses, 1) * 100, 1),
    }
    stats['goal_distribution'] = goal_dist
    stats['half_goal_dist'] = half_goal_dist
    stats['ht_ft'] = ht_ft_matrix
    stats['ht_summary'] = {'wins': ht_win, 'draws': ht_draw, 'losses': ht_loss}
    stats['over_under'] = over_under
    stats['odd_even'] = odd_even
    
    return stats


def compute_h2h_stats(v_data, home_team, away_team):
    """从交锋记录计算 H2H 统计"""
    if not v_data:
        return {}
    
    h2h = {'home_wins': 0, 'draws': 0, 'away_wins': 0, 'total': 0,
           'home_goals': 0, 'away_goals': 0}
    
    for entry in v_data:
        if not isinstance(entry, list) or len(entry) < 10:
            continue
        try:
            h_name = str(entry[5])
            a_name = str(entry[7])
            hs = int(entry[8]) if entry[8] is not None else 0
            as_ = int(entry[9]) if entry[9] is not None else 0
        except (ValueError, IndexError):
            continue
        
        # 判断方向：entry[5] 永远是左侧队伍
        is_home_side = (home_team in h_name)
        ht_hs = hs if is_home_side else as_
        ht_as = as_ if is_home_side else hs
        
        h2h['total'] += 1
        h2h['home_goals'] += ht_hs
        h2h['away_goals'] += ht_as
        
        if ht_hs > ht_as:
            h2h['home_wins'] += 1
        elif ht_hs == ht_as:
            h2h['draws'] += 1
        else:
            h2h['away_wins'] += 1
    
    if h2h['total'] > 0:
        h2h['home_win_rate'] = round(h2h['home_wins'] / h2h['total'] * 100, 1)
        h2h['away_win_rate'] = round(h2h['away_wins'] / h2h['total'] * 100, 1)
        h2h['draw_rate'] = round(h2h['draws'] / h2h['total'] * 100, 1)
        h2h['avg_total_goals'] = round((h2h['home_goals'] + h2h['away_goals']) / h2h['total'], 2)
    
    return h2h


# ─── DB 层 ──────────────────────────────────────────

def ensure_db():
    """创建表结构"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS match_analysis (
            sid INTEGER PRIMARY KEY,
            home_team TEXT,
            away_team TEXT,
            league TEXT,
            match_time TEXT,
            home_form TEXT,
            away_form TEXT,
            h2h TEXT,
            home_tech_stats TEXT,
            away_tech_stats TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS team_stats_cache (
            team_name TEXT,
            league TEXT,
            stat_type TEXT,
            total_matches INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            goals_for INTEGER DEFAULT 0,
            goals_against INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            handicap_win_rate REAL DEFAULT 0,
            over_rate REAL DEFAULT 0,
            stats_json TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (team_name, league, stat_type)
        );
    """)
    conn.commit()
    conn.close()


def save_match_analysis(sid, data):
    """保存比赛分析数据"""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO match_analysis 
        (sid, home_team, away_team, league, match_time,
         home_form, away_form, h2h,
         home_tech_stats, away_tech_stats, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        sid,
        data.get('home_team', ''),
        data.get('away_team', ''),
        data.get('league', ''),
        data.get('match_time', ''),
        json.dumps(data.get('home_stats', {}), ensure_ascii=False),
        json.dumps(data.get('away_stats', {}), ensure_ascii=False),
        json.dumps(data.get('h2h_stats', {}), ensure_ascii=False),
        json.dumps(data.get('home_tech', {}), ensure_ascii=False),
        json.dumps(data.get('away_tech', {}), ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


def cache_team_stats(team_name, league, stat_type, stats):
    """缓存球队聚合统计"""
    if not stats or not team_name:
        return
    
    conn = get_db()
    summary = stats.get('form_summary', {})
    handicap = stats.get('handicap_trend', {})
    ou = stats.get('over_under', {})
    total = summary.get('wins', 0) + summary.get('draws', 0) + summary.get('losses', 0)
    
    conn.execute("""
        INSERT OR REPLACE INTO team_stats_cache
        (team_name, league, stat_type, total_matches, wins, draws, losses,
         goals_for, goals_against, win_rate, handicap_win_rate, over_rate,
         stats_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        team_name, league, stat_type,
        total,
        summary.get('wins', 0),
        summary.get('draws', 0),
        summary.get('losses', 0),
        summary.get('goals_for', 0),
        summary.get('goals_against', 0),
        summary.get('win_rate', 0),
        handicap.get('win_rate', 0),
        round(ou.get('over', 0) / max(ou.get('over', 0) + ou.get('under', 0), 1) * 100, 1),
        json.dumps(stats, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


# ─── 主流程 ──────────────────────────────────────────

def process_match(sid):
    """处理单场比赛"""
    print(f"\n{'='*50}")
    print(f"处理 SID: {sid}")
    print(f"{'='*50}")
    
    # 1. 获取分析页
    analysis = fetch_analysis_page(sid)
    if not analysis:
        print(f"  ✗ 分析页获取失败")
        return False
    
    home_team = analysis.get('home_team', '')
    away_team = analysis.get('away_team', '')
    league = analysis.get('league_name', '')
    match_time = analysis.get('match_time', '')
    
    print(f"  {home_team} vs {away_team} ({league})")
    print(f"  h_data: {len(analysis.get('h_data', []))}条")
    print(f"  a_data: {len(analysis.get('a_data', []))}条")
    print(f"  v_data: {len(analysis.get('v_data', []))}条")
    
    # 2. 计算统计
    home_stats = compute_form_stats(analysis.get('h_data', []), home_team)
    away_stats = compute_form_stats(analysis.get('a_data', []), away_team)
    h2h_stats = compute_h2h_stats(analysis.get('v_data', []), home_team, away_team)
    
    print(f"  主队: {home_stats.get('form_summary', {}).get('wins', 0)}胜 "
          f"{home_stats.get('form_summary', {}).get('draws', 0)}平 "
          f"{home_stats.get('form_summary', {}).get('losses', 0)}负")
    print(f"  客队: {away_stats.get('form_summary', {}).get('wins', 0)}胜 "
          f"{away_stats.get('form_summary', {}).get('draws', 0)}平 "
          f"{away_stats.get('form_summary', {}).get('losses', 0)}负")
    print(f"  H2H: {h2h_stats.get('total', 0)}场交锋 "
          f"{h2h_stats.get('home_wins', 0)}-{h2h_stats.get('draws', 0)}-{h2h_stats.get('away_wins', 0)}")
    
    # 3. 获取技术统计 (detail 页)
    time.sleep(REQUEST_DELAY)  # 防止请求过快
    tech = fetch_tech_stats(sid)
    home_tech = {}
    away_tech = {}
    if tech and 'teamTvStats' in tech:
        tvs = tech['teamTvStats']
        home_tech = {k: v['home'] for k, v in tvs.items() if isinstance(v, dict) and 'home' in v}
        away_tech = {k: v['away'] for k, v in tvs.items() if isinstance(v, dict) and 'away' in v}
        print(f"  ✓ 技术统计: {', '.join(f'{k}={v}' for k, v in list(home_tech.items())[:5])}")
    else:
        print(f"  - 技术统计: 无数据")
    
    # 4. 组装数据
    match_data = {
        'sid': sid,
        'home_team': home_team,
        'away_team': away_team,
        'league': league,
        'match_time': match_time,
        'home_stats': home_stats,
        'away_stats': away_stats,
        'h2h_stats': h2h_stats,
        'home_tech': home_tech,
        'away_tech': away_tech,
    }
    
    # 5. 写入 DB
    save_match_analysis(sid, match_data)
    cache_team_stats(home_team, league, 'home_all', home_stats)
    cache_team_stats(away_team, league, 'away_all', away_stats)
    
    print(f"  ✓ 已保存")
    return True


def load_sids_from_pipeline():
    """从现有流水线加载待处理 SID"""
    sids = set()
    
    # 1. 从 poisson_predictions 表读
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT match_id FROM poisson_predictions WHERE CAST(match_id AS INTEGER) > 0"
        ).fetchall()
        for r in rows:
            sids.add(r[0])
        conn.close()
    except Exception as e:
        print(f"  DB读取失败: {e}")
    
    # 2. 从 results.json 读（先主路径，后退 docs/data/）
    found = False
    for rpath in [RESULTS_PATH, RESULTS_PATH_ALT]:
        if os.path.exists(rpath):
            try:
                with open(rpath) as f:
                    r = json.load(f)
                for m in r.get('matches', []):
                    if not m:
                        continue
                    sid = str(m.get('sid') or m.get('sId') or m.get('fid') or m.get('match_id') or '')
                    if sid.isdigit():
                        sids.add(sid)
                found = True
                break
            except Exception as e:
                print(f"  {rpath} 读取失败: {e}")
    if not found:
        print(f"  results.json 未找到（检查了 {RESULTS_PATH} 和 {RESULTS_PATH_ALT}）")
    
    return sorted(sids)


def is_sid_active(sid):
    """
    判断 SID 是否属于当前赛季/近期比赛。
    旧版 SID 在 144xxxx~150xxxx 范围（约2024年），新版在 2xxxxxx~3xxxxxx+（2025+）。
    """
    try:
        n = int(sid)
        return n >= 2000000
    except ValueError:
        return False


def main():
    ensure_db()
    
    # SID 来源
    if len(sys.argv) > 1:
        sids = [a for a in sys.argv[1:] if not a.startswith('--')]
        recent_mode = '--recent' in sys.argv
    else:
        recent_mode = True
        sids = []
    
    if not sids:
        all_sids = load_sids_from_pipeline()
        # 过滤：仅当前赛季 + 跳过已处理的
        conn = get_db()
        done = set(str(r[0]) for r in conn.execute("SELECT sid FROM match_analysis").fetchall())
        if '--backfill-training' in sys.argv:
            # 补训练集缺失的analysis数据
            train_sids = set(str(r[0]) for r in conn.execute("""
                SELECT match_id FROM poisson_predictions
                WHERE pinnacle_close_w > 1.01
                  AND reference_score IS NOT NULL AND reference_score != ''
                  AND match_id != ''
            """).fetchall())
            conn.close()
            sids = sorted(s for s in all_sids if s in train_sids and s not in done)
            print(f"训练集SID: {len(train_sids)}, 已处理: {len(done & train_sids)}, 待补: {len(sids)}")
        else:
            conn.close()
            if recent_mode:
                # 仅最近比赛 (SID >= 2950000, 约最近1~2周)
                sids = sorted(s for s in all_sids if is_sid_active(s) and s not in done and int(s) >= 2950000)
            else:
                sids = sorted(s for s in all_sids if is_sid_active(s) and s not in done)
            print(f"流水线加载: {len(all_sids)} 个SID")
            print(f"  → 活跃过滤后: {sum(1 for s in all_sids if is_sid_active(s))}")
        print(f"  → {'训练集补全' if '--backfill-training' in sys.argv else '最近模式' if recent_mode else '全量'}: {len(sids)} 个待处理")
    
    if not sids:
        print("没有找到要处理的 SID。")
        print("用法: python3 scripts/fetch_analysis_data.py [sid1 sid2 ...]")
        return
    
    print(f"待处理: {len(sids)} 场比赛\n")
    
    success = 0
    failed = 0
    for i, sid in enumerate(sids):
        try:
            if process_match(sid):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ 异常: {e}")
            failed += 1
        
        # 请求间隔 + 进度
        if i < len(sids) - 1:
            time.sleep(REQUEST_DELAY)
    
    print(f"\n{'='*50}")
    print(f"完成: {success} 成功, {failed} 失败 / 共 {len(sids)} 场")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
