#!/usr/bin/env python3
"""utils.py — 公共工具函数

队名匹配、别名映射、赔率计算等
"""

import re
from difflib import SequenceMatcher

# ─── 队名别名 ───
TEAM_ALIAS = {
    '皇马': '皇家马德里', '曼城': '曼彻斯特城', '巴萨': '巴塞罗那',
    '奥维耶多': '皇家奥维耶多', '阿德莱德': '阿德莱德联',
    '中央海岸': '中央海岸水手', '惠灵顿': '惠灵顿凤凰',
    '纽卡素': '纽卡斯尔联', '热刺': '托特纳姆热刺',
    '狼队': '伍尔弗汉普顿', '狐狸城': '莱斯特城',
    '枪手': '阿森纳', '红军': '利物浦',
    '蓝军': '切尔西', '喜鹊': '纽卡斯尔联',
    '圣徒': '南安普顿', '铁锤帮': '西汉姆联',
    '老鹰': '水晶宫', '太妃糖': '埃弗顿',
    '维拉': '阿斯顿维拉', '森林': '诺丁汉森林',
    '黄潜': '比利亚雷亚尔', '床单军团': '马德里竞技',
    '药厂': '勒沃库森', '大黄蜂': '多特蒙德',
    '拜仁': '拜仁慕尼黑', '大巴黎': '巴黎圣日耳曼',
    '波尔图': 'FC波尔图', '本菲卡': 'SL本菲卡',
    '体育CP': '里斯本竞技', '里斯本': '里斯本竞技',
}


def normalize_team(name: str) -> str:
    """队名归一化"""
    name = name.strip()
    return TEAM_ALIAS.get(name, name)


def team_match(a: str, b: str, threshold: float = 0.5) -> bool:
    """队名模糊匹配

    优先别名映射，再子串包含，最后相似度
    """
    na, nb = normalize_team(a), normalize_team(b)
    if na == nb:
        return True
    # 子串包含
    if na in nb or nb in na:
        return True
    # 相似度
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def safe_float(s, default=0.0) -> float:
    if not s:
        return default
    try:
        v = float(str(s).strip())
        return v if 1.0 < v < 50.0 else default
    except (ValueError, TypeError):
        return default


def calc_implied_prob(w: float, d: float, l: float):
    """隐含概率（去抽水）"""
    if w <= 0 or d <= 0 or l <= 0:
        return 0, 0, 0
    total = 1/w + 1/d + 1/l
    return round(1/w/total, 4), round(1/d/total, 4), round(1/l/total, 4)


def calc_ev(prob: float, odds: float) -> float:
    """期望值 EV = prob * odds - 1"""
    if odds <= 0 or prob <= 0:
        return 0
    return round(prob * odds - 1, 4)


def calc_kelly(prob: float, odds: float) -> float:
    """凯利指数 = (prob * odds - 1) / (odds - 1)"""
    if odds <= 1 or prob <= 0:
        return 0
    return round((prob * odds - 1) / (odds - 1), 4)


def parse_score(outcome_str: str) -> tuple:
    """从 '主胜 3-1' 提取 (result_label, score_str, home_score, away_score)"""
    if not outcome_str:
        return ('', '', None, None)
    label = ''
    if '主胜' in outcome_str:
        label = '主胜'
    elif '客胜' in outcome_str:
        label = '客胜'
    elif '平局' in outcome_str:
        label = '平局'
    m = re.search(r'(\d+)\s*[-:]\s*(\d+)', outcome_str)
    if m:
        return (label, f"{m.group(1)}-{m.group(2)}", int(m.group(1)), int(m.group(2)))
    return (label, '', None, None)
