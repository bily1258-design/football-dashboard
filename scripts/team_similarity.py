#!/usr/bin/env python3
"""
球队相似度匹配 v3 — 46维LGBM特征（含xG历史趋势 + 技术统计）
==============================
从 results.json 每场比赛提取46维特征向量（模型概率、赔率结构、球队近况、
技术统计、xG历史趋势），按球队聚合后余弦相似度匹配历史对局。

改动要点:
- 特征从5维 [lambda, gs, gc, lam_vs_league, tier] 升级为46维LGBM特征
- 删减12维尾部（盘路胜率/大球率/角球因覆盖过低已移除）
- 同联赛加成从×1.5降为×1.2（46维特征已含联赛信息）
- 对手互换场次自动跳过（避免"卡拉巴赫vs维斯特里"匹配"维斯特里vs卡拉巴赫"100%）
"""

import json
import os
import sys
import sqlite3
import logging
import re
from math import sqrt, exp
from collections import defaultdict
from functools import lru_cache
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------- paths ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'football.db')
RESULTS_PATH = os.path.join(PROJECT_DIR, 'docs', 'data', 'results.json')

# ---------- league tier mapping ----------
LEAGUE_TIER: dict = {
    '西甲': 1.0, '英超': 1.0, '意甲': 1.0, '德甲': 1.0, '法甲': 1.0,
    '欧冠杯': 1.0, '欧联杯': 1.0, '欧协联': 1.0,
    '解放者杯': 1.0, '南美杯': 1.0,
    '巴甲': 0.95, '阿甲': 0.95,
    '美职联': 0.90, '日职联': 0.90, '韩K联': 0.90, 'K1联赛': 0.90,
    '世界杯': 0.90, '国际赛': 0.85,
    '西乙': 0.85, '英冠': 0.85, '德乙': 0.85, '意乙': 0.85, '法乙': 0.85,
    'K2联赛': 0.80, '韩K2联': 0.80, 'J2联赛': 0.80,
    '美乙2': 0.75,
    '挪超': 0.80, '瑞典超': 0.80, '芬超': 0.80, '冰岛超': 0.80,
    '丹超': 0.80, '比甲': 0.80, '荷甲': 0.80, '葡超': 0.80,
    '罗甲': 0.80, '土超': 0.80, '俄超': 0.80, '乌超': 0.80, '捷甲': 0.80,
    '挪甲': 0.70, '瑞典甲': 0.70, '芬甲': 0.70, '瑞典超甲': 0.70,
    '苏联杯': 0.75, '爱甲': 0.70, '爱超': 0.75,
    '墨西联秋': 0.80, '智利甲': 0.80, '哥伦甲': 0.80,
    '巴西乙': 0.75, '智利乙': 0.70,
    '球会友谊': 0.55, '世俱杯': 0.70, '俱乐部赛': 0.70,
    '欧罗巴': 0.90, '欧罗巴资格': 0.80, '欧联资格': 0.80,
    '欧冠资格': 0.90, '欧冠预选': 0.85,
    '国际友谊': 0.65,
}

DEFAULT_TIER = 0.70
MIN_CHAR_MATCH = 3  # 中文模糊匹配最小字符数

SCORE_RE = re.compile(r'(\d+)\s*[-:]\s*(\d+)')


def _get_league_tier(league: str) -> float:
    if not league:
        return DEFAULT_TIER
    tier = LEAGUE_TIER.get(league)
    if tier is not None:
        return tier
    for key, val in LEAGUE_TIER.items():
        if key in league or league in key:
            return val
    return DEFAULT_TIER


def _extract_40d_vector(m: dict) -> list:
    """从单场比赛dict提取40维特征向量（v4: 32维基础 + 8维xG历史趋势），保证全数值
    组成：前32维(模型/赔率/排名/形态/近况) + 8维xG"""
    def _n(v, default=0.0):
        try: return float(v) if v is not None and v != '' else default
        except: return default
    return [
        # 0-2: 模型概率
        _n(m.get('model_win')), _n(m.get('model_draw')), _n(m.get('model_loss')),
        # 3-5: LGBM概率
        _n(m.get('lgbm_win')), _n(m.get('lgbm_draw')), _n(m.get('lgbm_loss')),
        # 6-8: 泊松开赔
        _n(m.get('poisson_open_w')), _n(m.get('poisson_open_d')), _n(m.get('poisson_open_l')),
        # 9-11: Pinnacle开盘
        _n(m.get('open_win_pin')), _n(m.get('open_draw_pin')), _n(m.get('open_loss_pin')),
        # 12-14: Pinnacle封盘
        _n(m.get('pin_close_w')), _n(m.get('pin_close_d')), _n(m.get('pin_close_l')),
        # 15-17: 模型波动（模型 - 泊松开赔）
        _n(m.get('model_win'))-_n(m.get('poisson_win')),
        _n(m.get('model_draw'))-_n(m.get('poisson_draw')),
        _n(m.get('model_loss'))-_n(m.get('poisson_loss')),
        # 18-23: 保留位（原赛事级别/节奏指标，暂时为0）
        0, 0, 0, 0, 0, 0,
        # 24-25: 联赛排名
        _n(m.get('home_rank')), _n(m.get('away_rank')),
        # 26-27: 联赛积分
        _n(m.get('home_pts')), _n(m.get('away_pts')),
        # 28-31: 近况
        _n(m.get('home_form_pts')), _n(m.get('away_form_pts')),
        _n(m.get('home_form_gd')), _n(m.get('away_form_gd')),
        # 32-39: xG历史趋势特征 8维 (v3)
        _n(m.get('home_goals_3')), _n(m.get('away_goals_3')),
        _n(m.get('home_conceded_3')), _n(m.get('away_conceded_3')),
        _n(m.get('xg_home_3')), _n(m.get('xg_away_3')),
        _n(m.get('xg_home_10')), _n(m.get('xg_away_10')),
    ]


def load_team_features(results_path: str = RESULTS_PATH) -> dict:
    """从 results.json 每场比赛提取40维特征（含xG），按球队聚合后做均值。
    返回 dict: {team_name: {'_vec': [40维标准化向量], 'league': str}, ...}"""
    if not os.path.exists(results_path):
        logger.warning("results.json 不存在: %s", results_path)
        return {}

    with open(results_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    all_matches = data.get('matches', [])
    if not all_matches:
        logger.warning("results.json 无 matches")
        return {}

    # 加载 xg_features 表，补填xG特征
    xg_by_sid = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""
            SELECT sid, home_goals_3, away_goals_3, home_conceded_3, away_conceded_3,
                   xg_home_3, xg_away_3, xg_home_10, xg_away_10
            FROM xg_features
        """)
        for r in cur.fetchall():
            xg_by_sid[r[0]] = {
                'home_goals_3': r[1], 'away_goals_3': r[2],
                'home_conceded_3': r[3], 'away_conceded_3': r[4],
                'xg_home_3': r[5], 'xg_away_3': r[6],
                'xg_home_10': r[7], 'xg_away_10': r[8],
            }
        conn.close()
    except Exception as e:
        logger.warning("加载xg_features异常: %s", e)

    # 每场比赛提取46维向量，按队伍聚合。
    # 只使用有比分(已完赛)的比赛，排除预览比赛的大量零值噪音
    team_vectors = defaultdict(list)
    team_league = {}
    used_count = 0
    for m in all_matches:
        ht, at = m.get('home_team', ''), m.get('away_team', '')
        if not ht or not at:
            continue
        score = m.get('score', '').strip()
        if not score or score == '-':
            continue  # 跳过预览/未完成比赛
        # 注入xG特征
        sid = m.get('sid') or m.get('fid') or 0
        try:
            sid_int = int(sid)
        except (ValueError, TypeError):
            sid_int = 0
        if sid_int in xg_by_sid:
            xg = xg_by_sid[sid_int]
            m['home_goals_3'] = xg['home_goals_3']
            m['away_goals_3'] = xg['away_goals_3']
            m['home_conceded_3'] = xg['home_conceded_3']
            m['away_conceded_3'] = xg['away_conceded_3']
            m['xg_home_3'] = xg['xg_home_3']
            m['xg_away_3'] = xg['xg_away_3']
            m['xg_home_10'] = xg['xg_home_10']
            m['xg_away_10'] = xg['xg_away_10']
        vec = _extract_40d_vector(m)
        team_vectors[ht].append(vec)
        team_vectors[at].append(vec)
        league = m.get('league', '') or m.get('event', '')
        if league:
            team_league[ht] = league
            team_league[at] = league
        used_count += 1

    logger.info("球队特征: 用 %d/%d 场(有比分的已完赛)", used_count, len(all_matches))

    # 求均值
    team_avg = {}
    for team, vecs in team_vectors.items():
        n = len(vecs)
        team_avg[team] = [sum(v[i] for v in vecs) / n for i in range(40)]

    # 标准化（Z-score）
    teams = list(team_avg.keys())
    n = len(teams)
    if n == 0:
        return {}
    means = [sum(team_avg[t][i] for t in teams) / n for i in range(40)]
    stds = [sqrt(sum((team_avg[t][i] - means[i]) ** 2 for t in teams) / n) or 1.0 for i in range(40)]

    # 标准化后存入 _vec
    features = {}
    for team in teams:
        vec = team_avg[team]
        normed = [(vec[i] - means[i]) / stds[i] for i in range(40)]
        features[team] = {
            '_vec': normed,
            'league': team_league.get(team, ''),
        }

    logger.info("球队特征(46维): %d 队 (来自 %d 场比赛)", len(features), len(all_matches))
    return features


def load_team_rolling_stats(db_path: str = DB_PATH, last_n: int = 10) -> dict:
    """从 match_tech_stats 计算球队近 N 场滚动均值（控球率/射门/射正/角球）。
    返回 dict: {team_name: {'rolling': [8维向量], 'match_count': int}}
    8维 = [自身控球率, 自身射门, 自身射正, 自身角球, 对手控球率, 对手射门, 对手射正, 对手角球]
    """
    import sqlite3, os
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 获取所有球队的技统数据，按日期排序
    all_teams = {}
    rows = cur.execute("""
        SELECT home_team, away_team, date,
               home_possession, away_possession,
               home_shots, away_shots,
               home_shots_on_target, away_shots_on_target,
               home_corners, away_corners
        FROM match_tech_stats
        ORDER BY date
    """).fetchall()
    conn.close()

    for r in rows:
        ht, at, d = r[0], r[1], r[2]
        hp, ap = r[3], r[4]
        hs, ash = r[5], r[6]
        hst, ast = r[7], r[8]
        hc, ac = r[9], r[10]

        # 主队各项统计
        if ht not in all_teams:
            all_teams[ht] = []
        all_teams[ht].append((d, hp, hs, hst, hc, ap, ash, ast, ac))

        # 客队各项统计（注意：客队在客场的指标是对手的表现，但我们关心的是球队自身表现）
        if at not in all_teams:
            all_teams[at] = []
        all_teams[at].append((d, ap, ash, ast, ac, hp, hs, hst, hc))

    result = {}
    for team, games in all_teams.items():
        games.sort(key=lambda x: x[0])  # 按日期排序
        last_n_games = games[-last_n:] if len(games) > last_n else games

        # 球队自身场均：控球率/射门/射正/角球
        self_poss = sum(g[1] for g in last_n_games) / max(len(last_n_games), 1)
        self_shots = sum(g[2] for g in last_n_games) / max(len(last_n_games), 1)
        self_sot = sum(g[3] for g in last_n_games) / max(len(last_n_games), 1)
        self_cor = sum(g[4] for g in last_n_games) / max(len(last_n_games), 1)

        # 对手在球队参加比赛中场均（对手控球率/射门等——反映球队防守水平）
        opp_poss = sum(g[5] for g in last_n_games) / max(len(last_n_games), 1)
        opp_shots = sum(g[6] for g in last_n_games) / max(len(last_n_games), 1)
        opp_sot = sum(g[7] for g in last_n_games) / max(len(last_n_games), 1)
        opp_cor = sum(g[8] for g in last_n_games) / max(len(last_n_games), 1)

        result[team] = {
            'rolling': [self_poss, self_shots, self_sot, self_cor,
                        opp_poss, opp_shots, opp_sot, opp_cor],
            'match_count': len(games),
        }

    logger.info("球队滚动技统(近%d场): %d 队", last_n, len(result))
    return result


def _pre_match_rolling(team: str, cutoff: str, team_home: dict, team_away: dict, last_n: int = 10):
    """计算某支球队在 cutoff 日期前的赛前滚动均值（严格截断，无前视偏差）"""
    # 球队主场比赛
    home_stats = team_home.get(team, [])
    prev_home = [s for d, s in home_stats if d < cutoff]
    home_self = [sum(c)/len(c) for c in zip(*prev_home)] if prev_home else [0, 0, 0, 0]

    # 球队客场比赛
    away_stats = team_away.get(team, [])
    prev_away = [s for d, s in away_stats if d < cutoff]
    away_self = [sum(c)/len(c) for c in zip(*prev_away)] if prev_away else [0, 0, 0, 0]

    # 限 last_n 场
    if len(prev_home) > last_n:
        recent_h = [s for _, s in prev_home[-last_n:]]
        home_self = [sum(c)/len(c) for c in zip(*recent_h)]
    if len(prev_away) > last_n:
        recent_a = [s for _, s in prev_away[-last_n:]]
        away_self = [sum(c)/len(c) for c in zip(*recent_a)]

    # 球队自身统计 4维 + 对手统计 4维（球队在主场面对对手的表现 = 对手的客场表现）
    # 对于主场比赛，对手统计从 away_stats 拿（因为对手是客队）
    # 对于客场比赛，对手统计从 home_stats 拿（因为对手是主队）
    # 简化处理：用对手"反向"数据
    # 自身: home_self 是对手在球队主场时的数据，away_self 是对手在球队客场时的数据
    # 但 team_home/team_away 存的是"球队自己的"主/客数据，不是对手的
    #
    # 重新想: team_home[team] = [(d, [控球率, 射门, 射正, 角球])] 这是在球队主场比赛时球队自己的数据
    # team_away[team] = [(d, [控球率, 射门, 射正, 角球])] 这是在球队客场比赛时球队自己的数据
    #
    # 所以 8 维 = [home_自身4, away_自身4]

    return home_self + away_self


def enrich_matches_with_rolling(matches: list, db_path: str, last_n: int = 10) -> list:
    """对历史池每场比赛，按该场比赛的日期截断，计算主客队赛前滚动均值。
    存入 match dict 的 pre_h_rolling 和 pre_a_rolling（各8维）。
    """
    import sqlite3, os
    if not os.path.exists(db_path):
        return matches

    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT home_team, away_team, date,
               home_possession, home_shots, home_shots_on_target, home_corners,
               away_possession, away_shots, away_shots_on_target, away_corners
        FROM match_tech_stats
        WHERE home_possession IS NOT NULL
        ORDER BY date ASC
    """).fetchall()
    conn.close()

    # 球队主客场比赛时间线
    team_home = {}
    team_away = {}
    for r in rows:
        d = r[2] or ''
        h_stats = [float(r[3] or 0), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)]
        a_stats = [float(r[7] or 0), float(r[8] or 0), float(r[9] or 0), float(r[10] or 0)]
        if r[0] not in team_home:
            team_home[r[0]] = []
            team_away[r[0]] = []
        if r[1] not in team_home:
            team_home[r[1]] = []
            team_away[r[1]] = []
        team_home[r[0]].append((d, h_stats))
        team_away[r[1]].append((d, a_stats))

    for m in matches:
        d = m.get('date', '')[:10]
        if not d:
            continue
        h_team = m.get('home_team', '')
        a_team = m.get('away_team', '')
        m['pre_h_rolling'] = _pre_match_rolling(h_team, d, team_home, team_away, last_n)
        m['pre_a_rolling'] = _pre_match_rolling(a_team, d, team_home, team_away, last_n)

    return matches


def _resolve_team_name(name: str, known_teams: set) -> str:
    """
    队伍名模糊匹配：
    1. 完全匹配优先
    2. 尝试子串匹配（中文 >= 3 字符同时出现）
    3. 尝试去除 FC/SC/IFK 等前缀后匹配
    """
    if name in known_teams:
        return name

    # 去掉常见前缀再试
    prefixes = ['FC ', 'SC ', 'IFK ', 'BK ', 'FK ', 'SK ', 'AS ', 'AC ', 'SS ']
    clean = name
    for p in prefixes:
        if clean.startswith(p):
            clean = clean[len(p):]
            if clean in known_teams:
                return clean
            break

    # 反过来：去掉常见后缀
    suffixes = [' FC', ' SC', ' IFK', ' BK', ' FK', ' SK']
    clean2 = name
    for s in suffixes:
        if clean2.endswith(s):
            clean2 = clean2[:-len(s)]
            if clean2 in known_teams:
                return clean2
            break

    # 中文子串匹配：找最长公共子串包含
    if len(name) >= MIN_CHAR_MATCH:
        candidates = []
        for kt in known_teams:
            # 所有中文字符
            shared = sum(1 for c in name if c in kt)
            if shared >= MIN_CHAR_MATCH:
                # 相似度 = 共享字符数 / 较长名字长度
                ratio = shared / max(len(name), len(kt))
                candidates.append((ratio, kt))
        if candidates:
            best = max(candidates, key=lambda x: x[0])
            # 至少40%字符重叠才算匹配
            if best[0] >= 0.4:
                return best[1]

    return name  # 返回原名（无法解析）


# 常见中文队名别名映射
TEAM_ALIAS: dict = {
    '云达不莱梅': '不来梅',
    '云达不莱梅青年队': '不来梅',
    '毕尔巴鄂竞技': '毕尔包',
    '利物浦蒙特维多': '利物浦蒙特维',
    '中央海岸水手': '中央海岸',
    'IFK哥德堡': '哥德堡',
    '哥登堡': '哥德堡',
    'CF蒙特利尔': '蒙特利尔冲击',
    'FC安养': '安养FC',
    'FC首尔': '首尔FC',
    'FC大邱': '大邱FC',
    'FC江原': '江原FC',
    'FC光州': '光州FC',
    'NY红牛': '纽约红牛',
    '纽约城FC': '纽约城',
    '亚特兰大联': '阿特兰大联',
    '洛杉矶FC': '洛杉矶FC',
    '洛杉矶银河': '洛杉矶银河',
    # 中文译名变体
    '桑讷菲尤尔': '桑德菲杰',
    '年轻人': '年青人',
    '加尔斯': '加尔韦斯',
    '国际图尔库': '图尔库国际',
    '奥达斯': '奥斯达',
}


def _resolve_team_name(name: str, known_teams: set) -> str:
    """
    队伍名模糊匹配：
    1. 别名映射优先
    2. 完全匹配
    3. 尝试去除 FC/SC/IFK 等前缀后匹配
    4. 中文子串匹配
    """
    # 别名映射
    if name in TEAM_ALIAS:
        alias = TEAM_ALIAS[name]
        if alias in known_teams:
            return alias

    if name in known_teams:
        return name

    # 去掉常见前缀再试
    prefixes = ['FC ', 'SC ', 'IFK ', 'BK ', 'FK ', 'SK ', 'AS ', 'AC ', 'SS ']
    for p in prefixes:
        if name.startswith(p):
            clean = name[len(p):]
            if clean in known_teams:
                return clean
            break

    # 反过来：去掉常见后缀
    suffixes = [' FC', ' SC', ' IFK', ' BK', ' FK', ' SK']
    for s in suffixes:
        if name.endswith(s):
            clean = name[:-len(s)]
            if clean in known_teams:
                return clean
            break

    # 中文子串匹配
    if len(name) >= MIN_CHAR_MATCH:
        candidates = []
        for kt in known_teams:
            shared = sum(1 for c in name if c in kt)
            if shared >= MIN_CHAR_MATCH:
                ratio = shared / max(len(name), len(kt))
                candidates.append((ratio, kt))
        if candidates:
            best = max(candidates, key=lambda x: x[0])
            if best[0] >= 0.4:
                return best[1]

    return name  # 无法解析


@lru_cache(maxsize=4096)
def _parse_date(date_str: str):
    """缓存日期解析，避免同一日期被重复 strptime（历史池日期高度重复）"""
    return datetime.strptime(date_str, '%Y-%m-%d')


def _cosine_sim(a: list, b: list) -> float:
    """余弦相似度"""
    if not a or not b:
        return 0.0
    # NaN 安全
    dot = 0.0
    na, nb = 0.0, 0.0
    for x, y in zip(a, b):
        if x is None or y is None or x != x or y != y:
            continue
        dot += x * y
        na += x * x
        nb += y * y
    na = sqrt(na)
    nb = sqrt(nb)
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@lru_cache(maxsize=4096)
def _compute_total_goals_top3(h_lambda: float, a_lambda: float,
                              min_prob: float = 0.15) -> list:
    """从泊松λ值计算总进球概率分布，返回概率 ≥ min_prob (默认15%) 的所有结果"""
    from math import exp as _exp
    # λ=0 时泊松PMF自然处理（一方不进球则卷积结果≈对方分布）
    if (h_lambda is None or a_lambda is None
        or h_lambda <= 0 or a_lambda <= 0):
        if (h_lambda or 0) <= 0 and (a_lambda or 0) <= 0:
            return []
        h_lambda = max(h_lambda or 0, 0.01)
        a_lambda = max(a_lambda or 0, 0.01)
    # 截断范围：λ*3+2 覆盖99.9%概率
    max_g = max(int(max(h_lambda, a_lambda) * 3 + 2), 6)

    # 计算泊松概率（用递推，避免每次从1重新累积）
    def poisson_probs(lam, max_g):
        """返回 [P(0), P(1), ..., P(max_g)]，用递推 poisson(k) = poisson(k-1)*lam/k"""
        probs = [0.0] * (max_g + 1)
        probs[0] = _exp(-lam)
        for k in range(1, max_g + 1):
            probs[k] = probs[k - 1] * lam / k
        return probs

    home_probs = poisson_probs(h_lambda, max_g)
    away_probs = poisson_probs(a_lambda, max_g)

    total_probs = {}
    for h in range(max_g + 1):
        for a in range(max_g + 1):
            t = h + a
            total_probs[t] = total_probs.get(t, 0.0) + home_probs[h] * away_probs[a]

    # 只保留概率 ≥ min_prob 的全部结果（不限数量），按概率降序
    filtered = sorted([(t, p) for t, p in total_probs.items() if p >= min_prob],
                      key=lambda x: -x[1])
    return [{'total_goals': t, 'prob': round(p, 3)} for t, p in filtered]


def load_historical_matches(db_path: str = DB_PATH, limit: int = 3000) -> list:
    """加载历史对局（默认取最近500场）"""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(f"""        SELECT deduped.*,
               COALESCE(mts.home_possession, 0) AS hp,
               COALESCE(mts.away_possession, 0) AS ap,
               COALESCE(mts.home_shots, 0) AS hs,
               COALESCE(mts.away_shots, 0) AS asht,
               COALESCE(mts.home_shots_on_target, 0) AS hst,
               COALESCE(mts.away_shots_on_target, 0) AS ast,
               COALESCE(mts.home_corners, 0) AS hc,
               COALESCE(mts.away_corners, 0) AS ac
        FROM (
            SELECT home_team, away_team, league, date,
                   reference_score, poisson_win, poisson_draw, poisson_loss,
                   actual_outcome, home_lambda, away_lambda,
                   ROW_NUMBER() OVER (
                       PARTITION BY home_team, away_team, date
                       ORDER BY
                           CASE WHEN reference_score IS NOT NULL AND reference_score != '' THEN 1 ELSE 2 END,
                           poisson_win DESC
                   ) AS rn
            FROM poisson_predictions
            WHERE home_team IS NOT NULL AND home_team != ''
              AND away_team IS NOT NULL AND away_team != ''
              AND (reference_score IS NOT NULL AND reference_score != ''
                   OR actual_outcome IS NOT NULL AND actual_outcome != '')
        ) deduped
        LEFT JOIN match_tech_stats mts
            ON mts.home_team = deduped.home_team
            AND mts.away_team = deduped.away_team
            AND mts.date = deduped.date
        WHERE deduped.rn = 1
        ORDER BY deduped.date DESC
        LIMIT {int(limit)}
    """).fetchall()
    conn.close()

    matches = []
    for r in rows:
        # reference_score (r[4]) 或从 actual_outcome (r[8]) 提取比分
        score = r[4] or ''
        if not score and r[8]:
            # actual_outcome 格式: "home (2-0)" or "away (1-3)" or "draw (0-0)"
            import re as _re
            m = _re.search(r'\((\d+)\s*-\s*(\d+)\)', r[8])
            if m:
                score = f'{m.group(1)}-{m.group(2)}'
        matches.append({
            'home_team': r[0], 'away_team': r[1], 'league': r[2] or '',
            'date': r[3] or '', 'score': score,
            'poisson_win': r[5] or 0, 'poisson_draw': r[6] or 0,
            'poisson_loss': r[7] or 0, 'actual': r[8] or '',
            'home_lambda': r[9] or 0, 'away_lambda': r[10] or 0,
            'home_possession': r[11] or 0, 'away_possession': r[12] or 0,
            'home_shots': r[13] or 0, 'away_shots': r[14] or 0,
            'home_shots_on_target': r[15] or 0, 'away_shots_on_target': r[16] or 0,
            'home_corners': r[17] or 0, 'away_corners': r[18] or 0,
        })
    logger.info("历史对局: %d 场", len(matches))
    return matches


def find_similar_matches(
    home_team: str, away_team: str, league: str,
    team_features: dict, historical_matches: list,
    rolling_stats: dict = None,
    top_k: int = 5,
) -> list:
    """
    为一场比赛找最相似的历史对局（46维LGBM特征 + xG + 赛前技统形态版）。
    策略：
    1. 主队46维特征向量 vs 历史主队 余弦相似度
    2. 客队46维特征向量 vs 历史客队 余弦相似度
    3. 若 rolling_stats 可用，加入技统形态余弦相似度（权重0.3）
    4. 综合 = sqrt(sim_h * sim_a) [加上技统加权]
    5. 同联赛加成 x1.2（46维特征已含联赛/实力信息，不过度提权）
    6. 时间衰减：90天内无衰减，之后指数衰减
    7. 对手互换场次自动跳过
    """
    ht_vec = team_features.get(home_team, {}).get('_vec')
    at_vec = team_features.get(away_team, {}).get('_vec')
    if not ht_vec or not at_vec:
        return []

    # 时间衰减用：now 只在函数开头取一次，避免循环内反复调用 datetime.now()
    now = datetime.now()
    scored = []
    for hm in historical_matches:
        # 跳过完全相同的对局
        if hm['home_team'] == home_team and hm['away_team'] == away_team:
            continue
        # 跳过对手互换（主客对调）
        if hm['home_team'] == away_team and hm['away_team'] == home_team:
            continue

        h_vec = team_features.get(hm['home_team'], {}).get('_vec')
        a_vec = team_features.get(hm['away_team'], {}).get('_vec')
        if not h_vec or not a_vec:
            continue

        # 使用全46维进行余弦相似对比
        sim_h = max(_cosine_sim(ht_vec, h_vec), 0.0)
        sim_a = max(_cosine_sim(at_vec, a_vec), 0.0)
        combined = sqrt(sim_h * sim_a)

        # 赛前技统形态相似度（权重0.3，仅当两队都有滚动数据时）
        if rolling_stats:
            ht_roll = rolling_stats.get(home_team, {}).get('rolling', None)
            at_roll = rolling_stats.get(away_team, {}).get('rolling', None)
            # 历史比赛使用当时赛前截断的滚动均值（无前视偏差）
            h_roll = hm.get('pre_h_rolling', None)
            a_roll = hm.get('pre_a_rolling', None)
            if all([ht_roll, at_roll, h_roll, a_roll]):
                roll_sim_h = max(_cosine_sim(ht_roll, h_roll), 0.0)
                roll_sim_a = max(_cosine_sim(at_roll, a_roll), 0.0)
                roll_sim = sqrt(roll_sim_h * roll_sim_a)
                combined = combined * 0.7 + roll_sim * 0.3

        # 同联赛加成（2026-08-07: 1.2→1.8，跨联赛实力相近误配率高，须显著提权）
        same_league = bool(hm['league'] and league and \
           (hm['league'] == league or league in hm['league'] or hm['league'] in league))
        if same_league:
            combined = min(combined * 1.8, 1.0)

        # 时间衰减：近期比赛权重高
        if hm['date']:
            try:
                match_date = _parse_date(hm['date'])
                days_ago = (now - match_date).days
                if days_ago >= 0:
                    # 90天内无衰减，之后指数衰减
                    time_weight = 1.0 if days_ago < 90 else exp(-(days_ago - 90) / 365)
                    combined *= time_weight
            except ValueError:
                pass

        scored.append({
            'home_team': hm['home_team'],
            'away_team': hm['away_team'],
            'league': hm['league'],
            'date': hm['date'],
            'score': hm['score'],
            'actual': hm['actual'],
            'sim_h': round(sim_h, 3),
            'sim_a': round(sim_a, 3),
            'similarity': round(combined, 3),
            'same_league': same_league,
            'total_goals_top3': _compute_total_goals_top3(
                hm.get('home_lambda', 0), hm.get('away_lambda', 0)),
        })

    scored.sort(key=lambda x: -x['similarity'])
    # 按(主队,客队)去重，同两队只保留相似度最高的一场
    seen_pairs = set()
    deduped = []
    for s in scored:
        pair = (s['home_team'], s['away_team'])
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(s)
    # 同联赛优先：先取同联赛场次，不足3场时用跨联赛补足
    same_lg = [s for s in deduped if s.get('same_league')]
    cross_lg = [s for s in deduped if not s.get('same_league')]
    result = same_lg[:3] + cross_lg[:max(0, top_k - 3)]
    if len(result) < top_k:  # 同联赛少于3场时，用跨联赛补足
        result = same_lg + cross_lg[:top_k - len(same_lg)]
    return result[:top_k]


def run(results_path: str = RESULTS_PATH, db_path: str = DB_PATH,
        force: bool = False, pool_size: int = 500,
        no_rolling: bool = False) -> int:
    if not os.path.exists(results_path):
        logger.error("results.json 不存在: %s", results_path)
        return 0

    with open(results_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    matches = data.get('matches', [])
    if not matches:
        logger.warning("results.json 中无 matches")
        return 0

    if not force:
        already = sum(1 for m in matches if m.get('similar_matches'))
        if already == len(matches):
            logger.info("所有 %d 场已有相似数据，跳过", already)
            return already

    team_features = load_team_features(results_path)
    if not team_features:
        logger.warning("无球队特征数据")
        return 0

    known_teams = set(team_features.keys())

    # 预加载所有比赛的λ值（用于计算当前比赛的总进球TOP3）
    lambda_lookup = {}
    # 同时构建球队平均λ（回退用）
    team_home_lambdas = {}   # team → list of home_lambda
    team_away_lambdas = {}   # team → list of away_lambda
    try:
        conn = sqlite3.connect(db_path)
        for row in conn.execute("""
            SELECT home_team, away_team, date, home_lambda, away_lambda
            FROM poisson_predictions
            WHERE home_lambda IS NOT NULL AND away_lambda IS NOT NULL
              AND home_lambda > 0 AND away_lambda > 0
        """):
            key = (row[0], row[1], row[2] or '')
            if key not in lambda_lookup:
                lambda_lookup[key] = (row[3] or 0, row[4] or 0)
            # 累加球队λ均值
            team_home_lambdas.setdefault(row[0], []).append(row[3] or 0)
            team_away_lambdas.setdefault(row[1], []).append(row[4] or 0)
        conn.close()
    except Exception:
        pass
    # 计算球队平均λ（仅保留最近5场，取均值）
    team_avg_home_lambda = {}
    team_avg_away_lambda = {}
    for team, vals in team_home_lambdas.items():
        recent = sorted(vals, reverse=True)[:5]
        if recent:
            team_avg_home_lambda[team] = sum(recent) / len(recent)
    for team, vals in team_away_lambdas.items():
        recent = sorted(vals, reverse=True)[:5]
        if recent:
            team_avg_away_lambda[team] = sum(recent) / len(recent)
    # 额外查询：单方λ>0的球队也收录（不限制对方λ），补全全南天龙等队
    try:
        conn = sqlite3.connect(db_path)
        for row in conn.execute(
            "SELECT home_team, home_lambda FROM poisson_predictions "
            "WHERE home_lambda IS NOT NULL AND home_lambda > 0"
        ):
            team_home_lambdas.setdefault(row[0], []).append(float(row[1]))
        for row in conn.execute(
            "SELECT away_team, away_lambda FROM poisson_predictions "
            "WHERE away_lambda IS NOT NULL AND away_lambda > 0"
        ):
            team_away_lambdas.setdefault(row[0], []).append(float(row[1]))
        conn.close()
    except Exception:
        pass
    # 重新计算平均值（包含补充的球队）
    for team, vals in team_home_lambdas.items():
        recent = sorted(vals, reverse=True)[:5]
        if recent:
            team_avg_home_lambda[team] = sum(recent) / len(recent)
    for team, vals in team_away_lambdas.items():
        recent = sorted(vals, reverse=True)[:5]
        if recent:
            team_avg_away_lambda[team] = sum(recent) / len(recent)
    # 第三补：DB中λ=0的队伍也收录队名（用于回退查找）
    _all_db_teams = set()  # 所有在DB中出现过的队名（含λ=0）
    try:
        conn = sqlite3.connect(db_path)
        for row in conn.execute(
            "SELECT DISTINCT home_team FROM poisson_predictions "
            "WHERE home_lambda IS NOT NULL"
        ):
            _all_db_teams.add(row[0])
        for row in conn.execute(
            "SELECT DISTINCT away_team FROM poisson_predictions "
            "WHERE away_lambda IS NOT NULL"
        ):
            _all_db_teams.add(row[0])
        conn.close()
    except Exception:
        pass
    # 从DB队名构造λ兜底字典（含TEAM_ALIAS映射）
    _all_db_names = set(team_avg_home_lambda) | set(team_avg_away_lambda)
    ahl_fuzzy = {}
    aal_fuzzy = {}
    for name in _all_db_names:
        if team_avg_home_lambda.get(name, 0) > 0:
            ahl_fuzzy[name] = team_avg_home_lambda[name]
        if team_avg_away_lambda.get(name, 0) > 0:
            aal_fuzzy[name] = team_avg_away_lambda[name]
    for alias, db_name in TEAM_ALIAS.items():
        if db_name in _all_db_names:
            if team_avg_home_lambda.get(db_name, 0) > 0:
                ahl_fuzzy[alias] = team_avg_home_lambda[db_name]
            if team_avg_away_lambda.get(db_name, 0) > 0:
                aal_fuzzy[alias] = team_avg_away_lambda[db_name]
    # 子串匹配兜底：对results.json的队名，若DB有子串/超串关系则加λ
    _rs_names = set(m.get('home_team','') for m in matches) | set(m.get('away_team','') for m in matches)
    for rn in _rs_names:
        if rn in _all_db_names or rn in ahl_fuzzy:
            continue
        # 先在含λ>0的DB队名中找子串匹配
        found = False
        for dbn in _all_db_names:
            if rn in dbn or dbn in rn:
                hl = team_avg_home_lambda.get(dbn, 0)
                al = team_avg_away_lambda.get(dbn, 0)
                if hl > 0: ahl_fuzzy.setdefault(rn, hl)
                if al > 0: aal_fuzzy.setdefault(rn, al)
                if hl > 0 or al > 0:
                    found = True
                    break
        # 若在λ>0集中没找到，去λ=0集中查队名是否存在
        if not found and rn in _all_db_teams:
            ahl_fuzzy.setdefault(rn, 0)
            aal_fuzzy.setdefault(rn, 0)
    # 预计算所有比赛的 total_goals_top3（独立于 similar_matches 状态）
    for m in matches:
        match_date = m.get('date', '')[:10]
        home = m.get('home_team', '')
        away = m.get('away_team', '')
        if not home or not away:
            continue
        # 尝试四种队名组合匹配λ
        lam_found = None
        rh = _resolve_team_name(home, known_teams)
        ra = _resolve_team_name(away, known_teams)
        for hh, aa in [(home, away), (rh, ra), (home, ra), (rh, away)]:
            key = (hh, aa, match_date)
            if key in lambda_lookup:
                lam_found = lambda_lookup[key]
                break
        if lam_found:
            hl, al = lam_found
            if hl > 0 or al > 0:
                m['total_goals_top3'] = _compute_total_goals_top3(hl, al)
        else:
            hl = team_avg_home_lambda.get(home) or team_avg_home_lambda.get(rh) \
                  or team_avg_away_lambda.get(home) or team_avg_away_lambda.get(rh) or 0
            al = team_avg_away_lambda.get(away) or team_avg_away_lambda.get(ra) \
                  or team_avg_home_lambda.get(away) or team_avg_home_lambda.get(ra) or 0
            if hl <= 0 and al <= 0:
                # 兜底：Fuzzy匹配DB队名
                hl = ahl_fuzzy.get(home) or ahl_fuzzy.get(rh) or 0
                al = aal_fuzzy.get(away) or aal_fuzzy.get(ra) or 0
            if hl > 0 or al > 0:
                m['total_goals_top3'] = _compute_total_goals_top3(hl, al)
            elif hl == 0 and al == 0 and (home in _all_db_teams or away in _all_db_teams
                   or rh in _all_db_teams or ra in _all_db_teams):
                # λ=0但队伍在DB中存在，用0.01兜底计算
                m['total_goals_top3'] = _compute_total_goals_top3(0.01, 0.01)

    rolling_stats = load_team_rolling_stats(db_path, last_n=10)

    historical_matches = load_historical_matches(db_path, limit=pool_size)
    if not historical_matches:
        logger.warning("无历史对局数据")
        return 0

    if not no_rolling:
        # 对历史池每场比赛计算赛前滚动均值（严格时间截断，无前视偏差）
        logger.info("正在计算历史对局赛前技统滚动均值...")
        enrich_matches_with_rolling(historical_matches, db_path, last_n=10)
    else:
        logger.info("已禁用技统滚动匹配（--no-rolling）")
        rolling_stats = None

    # 提取联赛和队伍名不相匹配的问题：DB用的是标准队伍名
    # results.json的队名可能不同，需要软匹配
    matched_count = 0
    not_found_teams = set()
    for m in matches:
        if not force and m.get('similar_matches'):
            matched_count += 1
            continue

        home = m.get('home_team', '')
        away = m.get('away_team', '')
        league = m.get('league', '') or m.get('event', '')

        if not home or not away:
            continue

        # 模糊匹配队名
        resolved_home = _resolve_team_name(home, known_teams)
        resolved_away = _resolve_team_name(away, known_teams)

        if resolved_home != home or resolved_away != away:
            if resolved_home != home:
                logger.debug("队名映射: '%s' → '%s'", home, resolved_home)
            if resolved_away != away:
                logger.debug("队名映射: '%s' → '%s'", away, resolved_away)

        ht_found = resolved_home in team_features
        at_found = resolved_away in team_features
        if not ht_found:
            not_found_teams.add(home)
        if not at_found:
            not_found_teams.add(away)
        if not ht_found or not at_found:
            continue

        similar = find_similar_matches(resolved_home, resolved_away, league,
                                       team_features, historical_matches,
                                       rolling_stats=rolling_stats)
        # 当前比赛的总进球TOP3（从DB的λ值计算）
        match_date = m.get('date', '')[:10]
        # 尝试多种key组合：先原队名后解析队名
        lam_found = None
        for hh, aa in [(home, away), (resolved_home, resolved_away),
                        (home, resolved_away), (resolved_home, away)]:
            key = (hh, aa, match_date)
            if key in lambda_lookup:
                lam_found = lambda_lookup[key]
                break
        if lam_found:
            hl, al = lam_found
            if hl > 0 or al > 0:
                m['total_goals_top3'] = _compute_total_goals_top3(hl, al)
        else:
            # 回退：用各队最近5场平均λ
            hl = team_avg_home_lambda.get(home) or team_avg_home_lambda.get(resolved_home) \
                  or team_avg_away_lambda.get(home) or team_avg_away_lambda.get(resolved_home) or 0
            al = team_avg_away_lambda.get(away) or team_avg_away_lambda.get(resolved_away) \
                  or team_avg_home_lambda.get(away) or team_avg_home_lambda.get(resolved_away) or 0
            if hl > 0 or al > 0:
                m['total_goals_top3'] = _compute_total_goals_top3(hl, al)
        if similar:
            m['similar_matches'] = similar
            matched_count += 1

    # 写回
    with open(results_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if not_found_teams:
        logger.info("未匹配队伍 (%d): %s", len(not_found_teams),
                     ', '.join(sorted(not_found_teams)[:20]))
    logger.info("相似匹配完成: %d/%d 场", matched_count, len(matches))
    return matched_count


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
    )
    force = '--force' in sys.argv
    pool_size = 500
    no_rolling = '--no-rolling' in sys.argv
    for a in sys.argv:
        if a.startswith('--pool-size='):
            pool_size = int(a.split('=')[1])
    run(force=force, pool_size=pool_size, no_rolling=no_rolling)
