#!/usr/bin/env python3
"""
球队相似度匹配 (Week 2)
=======================
从 poisson_predictions 数据库读取历史球队数据，为每场比赛构建球队特征向量，
通过余弦相似度找出最相似的 Top 5 历史对局，写入 results.json 的 `similar_matches` 字段。

特征维度: [lambda, avg_goals_scored, avg_goals_conceded, lambda_vs_league, tier]
使用 sklearn StandardScaler 标准化。
"""

import json
import os
import sys
import sqlite3
import logging
import re
from math import sqrt, exp
from collections import defaultdict
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


def load_team_features(db_path: str = DB_PATH) -> dict:
    """
    从 poisson_predictions 表读取所有球队特征。
    返回 dict: {team_name: {...features...}, ...}
    """
    if not os.path.exists(db_path):
        logger.warning("DB不存在: %s", db_path)
        return {}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT home_team, away_team, league,
               home_lambda, away_lambda,
               home_avg_goals, away_avg_goals,
               home_avg_conceded, away_avg_conceded,
               reference_score, actual_outcome
        FROM poisson_predictions
        WHERE home_team IS NOT NULL AND home_team != ''
          AND away_team IS NOT NULL AND away_team != ''
    """).fetchall()
    conn.close()

    # 聚合数据
    team_data = defaultdict(lambda: {
        'lambdas': [], 'goals_scored': [], 'goals_conceded': [],
        'tiers': [], 'league': None,
    })

    for row in rows:
        ht, at, league, hl, al, hg, ag, hc, ac, score, outcome = row
        tier = _get_league_tier(league)

        # 主队
        td_h = team_data[ht]
        td_h['lambdas'].append(hl if hl and hl > 0 else None)
        td_h['tiers'].append(tier)
        td_h['league'] = league or td_h['league']
        # 从参考比分解析主队进球
        if score:
            m = SCORE_RE.match(score)
            if m:
                g_h, g_a = int(m.group(1)), int(m.group(2))
                td_h['goals_scored'].append(g_h)
                td_h['goals_conceded'].append(g_a)

        # 客队
        td_a = team_data[at]
        td_a['lambdas'].append(al if al and al > 0 else None)
        td_a['tiers'].append(tier)
        td_a['league'] = league or td_a['league']
        if score:
            m = SCORE_RE.match(score)
            if m:
                g_h, g_a = int(m.group(1)), int(m.group(2))
                td_a['goals_scored'].append(g_a)
                td_a['goals_conceded'].append(g_h)

    # 联赛平均 lambda
    league_lambdas = _compute_league_avg_lambda(db_path)

    # 合成特征向量
    features = {}
    for team, d in team_data.items():
        lam_vals = [v for v in d['lambdas'] if v is not None]
        gs_vals = d['goals_scored']
        gc_vals = d['goals_conceded']
        t_vals = d['tiers']

        avg_lam = sum(lam_vals) / len(lam_vals) if lam_vals else 0.5
        avg_gs = sum(gs_vals) / len(gs_vals) if gs_vals else 0.0
        avg_gc = sum(gc_vals) / len(gc_vals) if gc_vals else 0.0

        # 联赛平均 lambda (该队主要所属联赛)
        team_league = d['league'] or ''
        league_avg_lam = league_lambdas.get(team_league, avg_lam)
        lam_vs_league = avg_lam - league_avg_lam

        tier_val = sum(t_vals) / len(t_vals) if t_vals else DEFAULT_TIER
        appearances = len(lam_vals) + len(gs_vals)

        features[team] = {
            'lambda': avg_lam,
            'avg_goals_scored': avg_gs,
            'avg_goals_conceded': avg_gc,
            'lambda_vs_league': lam_vs_league,
            'tier': tier_val,
            'appearances': appearances,
        }
    logger.info("球队特征: %d 队", len(features))
    return features


def _compute_league_avg_lambda(db_path: str) -> dict:
    """计算每个联赛的 lambda 平均值"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT league, AVG(home_lambda) as avg_l
        FROM poisson_predictions
        WHERE home_lambda > 0 AND league IS NOT NULL AND league != ''
        GROUP BY league
    """).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


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


def standardize_features(features: dict, cols: list) -> dict:
    """
    对指定特征列做 StandardScaler (均值0, 方差1) 标准化。
    原地修改 features 并返回。
    """
    vals = {team: [f.get(c, 0) for c in cols] for team, f in features.items()}
    n = len(vals)
    if n == 0:
        return features
    means, stds = [], []
    for i, col in enumerate(cols):
        col_vals = [v[i] for v in vals.values()]
        mu = sum(col_vals) / n
        var = sum((x - mu) ** 2 for x in col_vals) / n
        means.append(mu)
        stds.append(sqrt(var) if var > 1e-10 else 1.0)

    for team, f in features.items():
        vec = [f.get(c, 0) for c in cols]
        normed = [(vec[i] - means[i]) / stds[i] for i in range(len(cols))]
        f['_vec'] = normed
    logger.info("标准化完成: 列=%s", cols)
    return features


def load_historical_matches(db_path: str = DB_PATH, limit: int = 2000) -> list:
    """加载历史对局（默认取最近2000场）"""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(f"""        SELECT home_team, away_team, league, date,
               reference_score, poisson_win, poisson_draw, poisson_loss,
               actual_outcome, home_lambda, away_lambda
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
        WHERE rn = 1
        ORDER BY date DESC
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
        })
    logger.info("历史对局: %d 场", len(matches))
    return matches


def find_similar_matches(
    home_team: str, away_team: str, league: str,
    team_features: dict, historical_matches: list,
    top_k: int = 5,
) -> list:
    """
    为一场比赛找最相似的历史对局。
    策略：
    1. 主队特征向量 vs 历史主队 余弦相似度
    2. 客队特征向量 vs 历史客队 余弦相似度
    3. 综合 = sqrt(sim_h * sim_a) → 保证两边都匹配
    4. 同联赛加成 x1.15
    5. 时间衰减：90天内无衰减，之后指数衰减
    """
    ht_vec = team_features.get(home_team, {}).get('_vec')
    at_vec = team_features.get(away_team, {}).get('_vec')
    if not ht_vec or not at_vec:
        return []

    scored = []
    for hm in historical_matches:
        if hm['home_team'] == home_team and hm['away_team'] == away_team:
            continue

        h_vec = team_features.get(hm['home_team'], {}).get('_vec')
        a_vec = team_features.get(hm['away_team'], {}).get('_vec')
        if not h_vec or not a_vec:
            continue

        sim_h = max(_cosine_sim(ht_vec, h_vec), 0.0)
        sim_a = max(_cosine_sim(at_vec, a_vec), 0.0)
        combined = sqrt(sim_h * sim_a)

        # 同联赛加成（大幅提高权重，确保同联赛比赛优先）
        if hm['league'] and league and \
           (hm['league'] == league or league in hm['league'] or hm['league'] in league):
            combined = min(combined * 1.5, 1.0)

        # 时间衰减：近期比赛权重高
        if hm['date']:
            try:
                match_date = datetime.strptime(hm['date'], '%Y-%m-%d')
                days_ago = (datetime.now() - match_date).days
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
    return deduped[:top_k]


def run(results_path: str = RESULTS_PATH, db_path: str = DB_PATH,
        force: bool = False) -> int:
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

    team_features = load_team_features(db_path)
    if not team_features:
        logger.warning("无球队特征数据")
        return 0

    # 标准化
    feature_cols = ['lambda', 'avg_goals_scored', 'avg_goals_conceded',
                    'lambda_vs_league', 'tier']
    team_features = standardize_features(team_features, feature_cols)
    known_teams = set(team_features.keys())

    historical_matches = load_historical_matches(db_path)
    if not historical_matches:
        logger.warning("无历史对局数据")
        return 0

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
                                       team_features, historical_matches)
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
    run(force=force)
