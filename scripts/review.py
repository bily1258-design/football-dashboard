#!/usr/bin/env python3
"""
足球泊松模型 - 统一复盘脚本 V1
合并竞彩 + 北单复盘逻辑

【数据源说明】：
- 赛果：500.com (fetch_results_cache.py)
- 赔率：中国足彩网 zgzcw.com (fetch_pinnacle_odds.py)

功能：赛果回填、命中分析、EV偏差分析、自动调参、生成统一复盘报告

变化：
- v1: 合并jingcai_review + beidan_review，统一使用football.db
  - 去掉让球盘计算（北单特色玩法已撤销）
  - 去掉CSV依赖，复盘直接查DB
  - 保留联赛调参机制
"""

import os
import re
import sys
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Tuple

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构
_REPO_DIR = os.path.dirname(WORK_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    DATA_BASE_DIR = _REPO_DIR
else:
    DATA_BASE_DIR = WORK_DIR

DB_PATH = os.path.join(DATA_BASE_DIR, "data/football.db")
REPORT_DIR = os.path.join(DATA_BASE_DIR, "outputs/复盘报告")
CACHE_DIR = os.path.join(DATA_BASE_DIR, "data/cache")
PARAMS_FILE = os.path.join(DATA_BASE_DIR, "data/xg_params.json")
HISTORY_FILE = os.path.join(DATA_BASE_DIR, "data/review_history.json")
LOG_FILE = os.path.join(REPORT_DIR, "调参日志.md")


# ========== 队名别名表（合并竞彩+北单） ==========
_TEAM_ALIAS = {
    # 西甲
    '巴萨': '巴塞罗那', '皇马': '皇家马德里', '贝蒂斯': '皇家贝蒂斯',
    '塞维': '塞维利亚', '塞尔塔': '维戈塞尔塔', '瓦伦': '巴伦西亚',
    '马竞': '马德里竞技', '社会': '皇家社会', '奥维': '皇家奥维耶多',
    # 意甲
    '国米': '国际米兰', '米兰': 'AC米兰', '罗马': 'AS罗马',
    # 英超
    '曼城': '曼彻斯特城', '热刺': '托特纳姆热刺', '纽卡': '纽卡斯尔联',
    '利兹': '利兹联', '维拉': '阿斯顿维拉', '狼队': '伍尔弗汉普顿流浪',
    # 德甲
    '拜仁': '拜仁慕尼黑',
    # 美职
    '盐湖城': '皇家盐湖城', '波特兰': '波特兰伐木工', '圣何塞': '圣何塞地震',
    '明尼苏达': '明尼苏达联', '夏洛特': '夏洛特FC', '蒙特利尔': '蒙特利尔CF',
    '纽约城': '纽约城FC', '华盛顿': '华盛顿联', '纳什维尔': '纳什维尔SC',
    '新英格兰': '新英格兰革命', '费城': '费城联合', '堪萨斯': '堪萨斯城竞技',
    # 荷甲
    '阿贾': '阿贾克斯',
    # 瑞超
    '韦斯特罗': '瓦斯特拉斯',
    'IFK瓦纳默': '韦纳穆',
    # 北单常用
    '赫罗纳': '吉罗纳',
    # 北单队名
    '町田泽维': '町田泽维亚', '东京绿茵': 'FC东京', '神户胜利': '神户胜利船',
    '济州SK': '济州联', '金泉尚武': '尚武', '富川FC': '富川1995',
    '全北现代': '全北现代', '沃罗斯NF': '沃罗斯纳夫', '阿里斯': '阿里斯塞萨',
    '莱瓦贾科斯': '莱瓦贾克斯', '奥林匹亚': '奥林匹亚科斯',
    '帕纳辛纳': '帕纳辛奈科斯', '塞萨洛': '塞萨洛尼基', '雅典AEK': 'AEK雅典',
    '布拉格': '斯拉维亚', '斯拉维亚': '斯拉维亚',
    '比亚韦': '比亚韦斯托克', '北雪平': 'IFK北雪平',
    # === 500.com → DB 映射 ===
    # 芬兰
    'MP米凯利': 'MP米克力', 'SJK阿卡泰米阿': 'SJK学院',
    '塞那乔恩': '塞纳乔琪', '塞那乔其': '塞纳乔琪',
    '库普斯': '库奥皮奥', '古比斯': '库奥皮奥',
    '国际图尔库': '国际图尔', '图尔库国际': '国际图尔',
    'TPS土尔库': 'TPS图尔', '图尔库': '图尔库国际',
    '查路': '坦山猫', '查普斯': '查普斯', '哈卡': '哈卡',
    'EIF埃克纳斯': 'EIF埃克纳斯', '雅罗': '雅罗',
    '格尼斯坦': '格尼斯坦', '瓦萨': '瓦萨',
    '桑德维肯斯': '桑德维根斯',
    '赫尔辛基': '赫尔火花',
    '埃尔维斯': '坦山猫',
    '塞伊奈约基': '塞伊奈',
    # 波兰
    '华沙莱吉亚': '华沙军团', '摩托鲁宾': '莫托路宾',
    '波兹南莱赫': '波兹莱赫', '莱克普斯纳': '波兹莱赫',
    '乔治罗尼亚': '比亚韦', '什切青波贡': '什切青',
    '施切钦波贡': '什切青',
    'GKS卡托威斯': '卡托威斯', '卡杜华斯': '卡托威斯',
    '扎布热': '扎布热矿工',
    '卢宾扎格列比': '卢宾',
    '维德祖罗兹': '维德祖罗兹',
    '格里维治': '格里维治',
    '华沙普洛克': '华沙普洛克',
    '拉多麦科': '拉多麦科',
    # 日本
    '名古屋鲸八': '名古屋鲸鱼', '长崎航海': '长崎成功丸',
    '清水鼓动': '清水心跳', '町田泽维亚': '町田泽维亚',
    # 韩国
    '坡州市民': '坡州前线', '清州FC': '忠北清州',
    # 巴西
    '克里西乌马': '克里丘马', '塞阿拉': '塞阿拉',
    '福塔雷萨': '福塔雷萨', '隆德里纳': '隆德里纳',
    '尤文图德': '尤文图德',
    '博塔弗戈': '博塔弗戈',
    '格雷米奥': '格雷米奥', '科林蒂安': '科林蒂安',
    '巴伊亚': '巴伊亚', 'EC巴伊亚': '巴伊亚',
    '蓬塔格罗萨铁路': '铁路工人',
    # 挪威
    '兰赫姆': '兰黑姆', '桑内斯': '桑德尼斯',
    '斯特罗姆加斯特': '斯托姆加斯特',
    '诺霍斯': '诺霍斯',
    '康斯文格': '康斯文格',
    # 瑞典
    '永斯基': '卢恩斯基尔', '厄勒布鲁': '奥雷布洛',
    '奥迪沃德': '奥迪沃特', '厄斯特松德': '厄斯特松德',
    '法尔肯堡': '法尔肯堡', '布莱格': '布莱格',
    '埃尔夫斯堡': '埃夫斯堡', '兰斯科罗纳': '兰斯科罗纳',
    '北欧联合': '北欧联FC', '阿西里斯卡': '北欧联FC',
    '厄格里特': '厄斯特松德',
    # 冰岛
    'IBV韦斯文尼查': 'IBV韦斯特曼纳',
    '斯塔尔南': '斯塔尔南', '托尔阿克雷里': '托尔阿克雷里',
    '维京古': '维京古尔',
    'IA阿克拉内斯': '阿克拉内斯',
    '雷克雅未克': '雷克雅',
    '贝雷达比历克': '贝雷达比', 'KA阿克雷里': 'KA阿古雷',
    # 爱尔兰
    '谢尔伯恩': '舒尔本', '沃特福德联合': '沃特联队',
    '沃特福德': '沃特联队',
    '科布漫步者': '科布漫步', '科布多西部': '科布漫步',
    '德罗赫达联': '德罗赫达',
    '斯莱戈流浪者': '斯莱戈流浪',
    '波希米亚人': '波希米亚',
    '圣帕特里克竞技': '圣帕特里',
    '布雷流浪者': '布雷',
    '韦克斯福德': '韦克青年',
    '条约联': '特瑞特联',
    '费恩夏普': '费恩哈普',
    # 奥地利
    '维也纳快速': '维快速', '里德': '里德',
    # 罗马尼亚
    '克卢日大学': '克卢日', '阿格斯': '阿格斯',
    '卡萨皮亚': '卡萨皮亚', '托伦斯': '托林斯',
    # 阿根廷
    '圣菲联': '圣菲联合', '独立队': '阿独立',
    '独立FBC': '阿独立', '飓风': '飓风队',
    '巴拉卡斯中央': '巴拉卡斯中央',
    # 以色列
    '贝尔谢巴夏普尔': '加尔达贝尔', '弗拉姆': '弗拉姆',
    # 英格兰
    '米德尔斯堡': '米堡', '赫尔城': '赫尔城',
    # 丹麦
    '阿晓斯费马': '奥胡斯费马', '奥尔堡': '奥尔堡',
    # 国际赛
    '格鲁吉亚': '格鲁吉亚', '罗马尼亚': '罗马尼亚',
    '威尔士': '威尔士', '加纳': '加纳',
    '瑞典': '瑞典', '希腊': '希腊',
    '西班牙': '西班牙', '伊拉克': '伊拉克',
    '法国': '法国', '科特迪瓦': '科特迪瓦',
    '塞浦路斯': '塞浦路斯', '斯洛伐克': '斯洛文尼',
    '沙特阿拉伯': '沙特',
}


def team_match(a: str, b: str) -> bool:
    """模糊队名匹配：别名表 + substring + 长度比阈值"""
    if not a or not b:
        return False
    if a == b:
        return True
    a2 = _TEAM_ALIAS.get(a, a)
    b2 = _TEAM_ALIAS.get(b, b)
    if a2 == b2 or a2 == b or a == b2:
        return True
    if a2 in b2 or b2 in a2:
        return True
    ratio = min(len(a2), len(b2)) / max(len(a2), len(b2))
    if ratio >= 0.5:
        short, long = (a2, b2) if len(a2) <= len(b2) else (b2, a2)
        if all(c in long for c in short if c not in ' vs-'):
            return True
    return False


# ========== 解析函数 ==========

def parse_actual_outcome(actual_outcome: str) -> Tuple[str, str]:
    """解析actual_outcome字段，返回(赛果方向, 比分)"""
    if not actual_outcome:
        return None, ""
    actual_outcome = actual_outcome.strip()
    score_match = re.search(r'(\d+-\d+)', actual_outcome)
    if score_match:
        score = score_match.group(1)
        result_part = actual_outcome.replace(score, '').strip()
    else:
        score = ""
        result_part = actual_outcome
    if "平局" in result_part or result_part == "平":
        return "平局", score
    if "主胜" in result_part or result_part == "胜":
        return "主胜", score
    if "客胜" in result_part or result_part == "负":
        return "客胜", score
    return result_part, score


def is_prediction_correct(prediction: str, actual_result: str) -> bool:
    """判断预测方向是否命中"""
    pred = prediction.strip()
    if pred in ["主胜", "胜", "主"]:
        return actual_result == "主胜"
    elif pred in ["客胜", "负", "客"]:
        return actual_result == "客胜"
    elif pred in ["平局", "平"]:
        return actual_result == "平局"
    return False


def get_top_direction(final_win, final_draw, final_loss) -> str:
    """获取概率最高方向"""
    for v in [final_win, final_draw, final_loss]:
        if v is None:
            return '-'
    if final_win > 1:
        final_win /= 100
    if final_draw > 1:
        final_draw /= 100
    if final_loss > 1:
        final_loss /= 100
    probs = [('主', final_win), ('平', final_draw), ('客', final_loss)]
    probs.sort(key=lambda x: x[1], reverse=True)
    return probs[0][0] if probs[0][1] > 0 else '-'


# ========== 赛果回填 ==========


def backfill_from_500com(target_date: str) -> int:
    """从500.com缓存文件回填赛果到football.db

    加载目标日期+次日缓存（欧洲23:00晚场次日凌晨才进wanchang）
    """
    base = target_date.replace('-', '')
    cache_today = os.path.join(CACHE_DIR, f"500com_results_{base}.json")

    all_results = []
    jc_homes = set()

    import json as _json

    if os.path.exists(cache_today):
        with open(cache_today, 'r', encoding='utf-8') as f:
            data = _json.loads(f.read())
        for r in data.get('jingcai', []):
            all_results.append({
                'home': r['home'], 'away': r['away'], 'score': r['score'],
                'outcome': f"{'主胜' if r['home_score'] > r['away_score'] else '平局' if r['home_score'] == r['away_score'] else '客胜'} {r['score']}",
            })
        jc_homes = {r['home'] for r in data.get('jingcai', [])}
        for r in data.get('wanchang', []):
            if r['home'] not in jc_homes:
                all_results.append({
                    'home': r['home'], 'away': r['away'], 'score': r['score'],
                    'outcome': f"{r['outcome']} {r['score']}",
                })

    # 加载次日缓存补欧洲晚场
    next_date = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    next_base = next_date.replace('-', '')
    cache_next = os.path.join(CACHE_DIR, f"500com_results_{next_base}.json")
    if os.path.exists(cache_next):
        with open(cache_next, 'r', encoding='utf-8') as f:
            data = _json.loads(f.read())
        td = target_date[5:].replace('-', '-')
        for r in data.get('wanchang', []):
            ko = r.get('kickoff', '')
            if ko.startswith(td) and r['home'] not in jc_homes:
                all_results.append({
                    'home': r['home'], 'away': r['away'], 'score': r['score'],
                    'outcome': f"{r['outcome']} {r['score']}",
                })

    if not all_results:
        print(f"  500.com无赛果数据")
        return 0

    print(f"  500.com: {len(all_results)} 条赛果")
    return _backfill_results(all_results)



def _backfill_results(results: List[Dict]) -> int:
    """用赛果列表回填数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, home_team, away_team, actual_outcome, kickoff_time
        FROM poisson_predictions
    """)
    db_records = [dict(r) for r in cursor.fetchall()]
    updated = 0
    for rec in db_records:
        if rec['actual_outcome'] and re.search(r'\d+-\d+', rec['actual_outcome']):
            continue
        for res in results:
            if team_match(rec['home_team'], res['home']) and team_match(rec['away_team'], res['away']):
                cursor.execute(
                    "UPDATE poisson_predictions SET actual_outcome = ? WHERE id = ?",
                    (res['outcome'], rec['id'])
                )
                updated += 1
                break
    conn.commit()
    conn.close()
    return updated


# ========== 参数管理（来自beidan_review，联赛级别调参） ==========

def load_params() -> dict:
    """加载xG参数"""
    if not os.path.exists(PARAMS_FILE):
        return _default_params()
    try:
        with open(PARAMS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return _default_params()


def _default_params() -> dict:
    return {
        "base_home_xg": 1.2,
        "base_away_xg": 0.8,
        "home_weight": 2.0,
        "away_penalty": 0.8,
        "league_adjustments": {},
        "min_days_for_adjustment": 3,
        "max_adjustment_per_run": 0.1,
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }


def save_params(params: dict) -> None:
    params['last_updated'] = datetime.now().strftime("%Y-%m-%d")
    with open(PARAMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(params, f, ensure_ascii=False, indent=2)


def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {"records": []}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"records": []}


def save_history(history: dict) -> None:
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ========== 自动调参（联赛级别，竞彩泊松模型用） ==========

def auto_adjust_params(history: dict, params: dict) -> Tuple[dict, List[str]]:
    """
    基于历史复盘数据自动调整联赛参数
    条件：命中率 < 35% 且样本 ≥ 3 时微调 ±0.1
    """
    adjustments = []
    min_days = params.get('min_days_for_adjustment', 3)
    max_adj = params.get('max_adjustment_per_run', 0.1)
    param_floor, param_ceil = -0.20, 0.20
    hit_rate_low, hit_rate_high = 0.35, 0.55

    all_records = history.get('records', [])
    if len(all_records) < min_days:
        adjustments.append(f"数据不足（{len(all_records)}/{min_days}天），暂不调参")
        return params, adjustments

    league_stats = defaultdict(lambda: {'total': 0, 'hits': defaultdict(int)})
    for record in all_records:
        league = record.get('league', '')
        direction = record.get('actual_direction', '')
        is_hit = record.get('is_hit', False)
        if not league:
            continue
        league_stats[league]['total'] += 1
        if is_hit:
            league_stats[league]['hits'][direction] += 1

    league_adj = params.get('league_adjustments', {})
    for league, stats in league_stats.items():
        if stats['total'] < 3:
            continue
        for direction, label in [('主胜', 'home_weight_mod'), ('客胜', 'away_penalty_mod')]:
            hit_rate = stats['hits'].get(direction, 0) / stats['total']
            current = league_adj.get(league, {}).get(label, 0.0)
            if hit_rate < hit_rate_low:
                new_val = round(max(param_floor, current - max_adj), 2)
            elif hit_rate > hit_rate_high:
                new_val = round(min(param_ceil, current + max_adj), 2)
            else:
                continue
            if new_val == round(current, 2):
                continue
            if league not in league_adj:
                league_adj[league] = {'home_weight_mod': 0.0, 'away_penalty_mod': 0.0}
            league_adj[league][label] = new_val
            direction_word = "降低" if new_val < current else "升高"
            adjustments.append(
                f"{league} {direction}: {hit_rate:.1%}({stats['hits'].get(direction,0)}/{stats['total']}), "
                f"{direction_word} {label} {current:.2f}→{new_val:.2f}"
            )

    params['league_adjustments'] = league_adj
    return params, adjustments


def record_adjustment(logs: List[str]) -> None:
    if not logs:
        return
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_entry = f"\n## {today}\n\n" + "\n".join(f"- {log}" for log in logs) + "\n"
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry)


# ========== 统一复盘核心 ==========

def run_review(target_date: str = None, skip_fetch: bool = False) -> str:
    """
    执行统一复盘
    target_date: 复盘日期 YYYY-MM-DD，默认查目标日期全天赛果
    """
    if not target_date:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"统一复盘: {target_date}")

    # 0. 赛果回填
    if skip_fetch:
        # GA环境：不抓网络，从500.com缓存文件读
        print("回填赛果（从500.com缓存）...")
        backfill_from_500com(target_date)
    else:
        # 本地环境：从500.com抓取并缓存
        print("回填赛果（500.com）...")
        from fetch_results_cache import fetch_500com_results, save_cache
        from datetime import datetime as _dt, timedelta as _td
        all_results = []
        seen_keys = set()
        for offset in [-1, 0, 1]:
            d = (_dt.strptime(target_date, '%Y-%m-%d') + _td(days=offset)).strftime('%Y-%m-%d')
            results = fetch_500com_results(d)
            for r in results:
                key = (r.get('home', ''), r.get('away', ''))
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_results.append(r)
        if all_results:
            # 保存缓存供GA环境使用
            save_cache(target_date, {
                'date': target_date, 'jingcai': all_results, 'wanchang': [],
                'fetch_time': _dt.now().isoformat(), 'source': '500com',
            })
            print(f"  500.com: {len(all_results)} 条赛果（已缓存）")
            _backfill_results([{
                'home': r['home'], 'away': r['away'], 'score': r['score'],
                'outcome': f"{r['outcome']} {r['score']}",
            } for r in all_results])
        else:
            # 缓存也没数据，尝试读旧缓存
            backfill_from_500com(target_date)

    # 1. 查目标日期全天有赛果的记录（固定日历日，不用相对24小时）
    date_start = f"{target_date} 00:00"
    date_end = f"{target_date} 23:59"
    print(f"  复盘窗口: {date_start} ~ {date_end}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source, match_id, league, home_team, away_team, kickoff_time,
               prediction, actual_outcome,
               final_win, final_draw, final_loss,
               ev_win, ev_draw, ev_loss,
               best_direction_cn,
               odds_win, odds_draw, odds_loss
        FROM poisson_predictions
        WHERE actual_outcome IS NOT NULL AND actual_outcome != ''
              AND kickoff_time >= ? AND kickoff_time <= ?
        ORDER BY source, kickoff_time, id
    """, (date_start, date_end))
    records = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not records:
        return f"# 统一复盘 {target_date}\n\n无已开奖记录"

    # 2. 逐条分析
    total = len(records)
    dir_hit, top_hit, any_hit = 0, 0, 0
    details = []
    league_stats = defaultdict(lambda: {'total': 0, 'dir_hit': 0, 'any_hit': 0})
    source_stats = defaultdict(lambda: {'total': 0, 'hit': 0})

    for rec in records:
        actual_result, score = parse_actual_outcome(rec['actual_outcome'])
        prediction = rec['prediction'] or ''

        # EV推荐方向
        ev_dir = rec.get('best_direction_cn') or prediction
        # 概率最高方向
        prob_dir = get_top_direction(rec['final_win'], rec['final_draw'], rec['final_loss'])

        ev_hit = is_prediction_correct(ev_dir, actual_result)
        prob_hit = is_prediction_correct(prob_dir, actual_result)

        if ev_hit:
            dir_hit += 1
        if prob_hit:
            top_hit += 1
        if ev_hit or prob_hit:
            any_hit += 1

        # 联赛统计
        league = rec.get('league', '') or '未知'
        league_stats[league]['total'] += 1
        if ev_hit:
            league_stats[league]['dir_hit'] += 1
        if ev_hit or prob_hit:
            league_stats[league]['any_hit'] += 1

        # 来源统计
        source = rec.get('source', 'unknown')
        source_stats[source]['total'] += 1
        if ev_hit:
            source_stats[source]['hit'] += 1

        # 抽水
        ow, od, ol = rec.get('odds_win') or 0, rec.get('odds_draw') or 0, rec.get('odds_loss') or 0
        margin = 0
        if ow > 1 and od > 1 and ol > 1:
            margin = (1/ow + 1/od + 1/ol - 1)

        # EV值
        ev_win = rec.get('ev_win') or 0
        ev_draw = rec.get('ev_draw') or 0
        ev_loss = rec.get('ev_loss') or 0

        mark = "✅" if (ev_hit or prob_hit) else "❌"
        details.append({
            'source': source,
            'league': league,
            'kickoff': (rec.get('kickoff_time') or '')[11:16],
            'home': rec['home_team'],
            'away': rec['away_team'],
            'ev_dir': ev_dir,
            'prob_dir': prob_dir,
            'ev_win': f"{ev_win:.3f}" if ev_win else '0',
            'ev_draw': f"{ev_draw:.3f}" if ev_draw else '0',
            'ev_loss': f"{ev_loss:.3f}" if ev_loss else '0',
            'margin': f"{margin:.1%}" if margin else '-',
            'actual': actual_result,
            'score': score or actual_result,
            'dir_mark': "✔" if ev_hit else "✖",
            'top_mark': "✔" if prob_hit else "✖",
            'mark': mark,
        })

    # 3. 生成报告
    dir_rate = f"{dir_hit/total*100:.1f}%"
    top_rate = f"{top_hit/total*100:.1f}%"
    any_rate = f"{any_hit/total*100:.1f}%"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = f"""# 足球复盘报告（统一版）
**日期**: {target_date}
**执行时间**: {now_str}

---

## 一、总体统计

| 指标 | 数值 |
|------|------|
| 总比赛数 | {total}场 |
| EV方向命中 | {dir_hit}场 ({dir_rate}) |
| 概率最高命中 | {top_hit}场 ({top_rate}) |
| 任一命中 | {any_hit}场 ({any_rate}) |

---

## 二、来源分布

| 来源 | 场次 | 命中 | 命中率 |
|------|------|------|--------|
"""
    for src, st in sorted(source_stats.items()):
        t = st['total']
        h = st['hit']
        r = f"{h/t*100:.1f}%" if t else "0%"
        report += f"| {src} | {t} | {h} | {r} |\n"

    report += f"""
---

## 三、详细复盘

| 来源 | 联赛 | 时间 | 主队 | 客队 | 方向 | 最优 | EV主 | EV平 | EV客 | 抽水 | 比分 | 结果 |
|------|------|------|------|------|------|------|------|------|------|------|------|------|
"""
    for d in details:
        report += f"| {d['source']} | {d['league']} | {d['kickoff']} | {d['home']} | {d['away']} | {d['ev_dir']}{d['dir_mark']} | {d['prob_dir']}{d['top_mark']} | {d['ev_win']} | {d['ev_draw']} | {d['ev_loss']} | {d['margin']} | {d['score']} | {d['mark']} |\n"

    # 联赛维度
    if league_stats:
        report += f"\n---\n\n## 四、联赛维度\n\n"
        report += "| 联赛 | 场次 | 方向命中 | 任一命中 | 方向率 | 任一率 |\n"
        report += "|------|------|---------|---------|--------|--------|\n"
        for lg, st in sorted(league_stats.items(), key=lambda x: x[1]['total'], reverse=True):
            t = st['total']
            dr = f"{st['dir_hit']/t*100:.0f}%" if t else "0%"
            ar = f"{st['any_hit']/t*100:.0f}%" if t else "0%"
            report += f"| {lg} | {t} | {st['dir_hit']} | {st['any_hit']} | {dr} | {ar} |\n"

    # 保存报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_file = os.path.join(REPORT_DIR, f"统一复盘_{target_date.replace('-','')}.md")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ 报告: {report_file}")

    return report


# ========== 入口 ==========

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="统一足球复盘")
    parser.add_argument('--date', help='复盘日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--db', help='数据库路径（覆盖默认DB_PATH）')
    parser.add_argument('--skip-fetch', action='store_true', help='跳过赛果抓取（DB已有赛果时用，如GA环境）')
    args = parser.parse_args()

    # 覆盖DB_PATH（pipeline传入时）
    if args.db:
        DB_PATH = args.db

    if args.skip_fetch:
        print("⏭️ 跳过赛果抓取 (--skip-fetch)")

    target = args.date or datetime.now().strftime("%Y-%m-%d")
    report = run_review(target, skip_fetch=args.skip_fetch)
    print(f"\n完成: {target}")
    print(report[:500])

    # 看板推送由 fetch_data.py 的 step_align/step_build/step_push/step_push_db 统一负责
    # 此处不再重复执行
