#!/usr/bin/env python3
"""
ai_analysis.py — 足彩智能分析系统 v3.0

功能：
1. 读取data/matches_*.json（从500.com zqdc页面抓取）
2. 读取data/football.db（历史数据）
3. 计算：隐含概率 → 泊松λ → 泊松1X2 → 融合概率 → EV
4. 生成：docs/data/results.json + docs/index.html + 前端文件

用法：
  python3 scripts/ai_analysis.py
  python3 scripts/ai_analysis.py --db data/football.db
  python3 scripts/ai_analysis.py --fetch-only   # 只抓取，不分析
"""
import re, json, os, sys, math, sqlite3, glob, logging, hashlib, time
import numpy as np
from datetime import datetime, date, timezone, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Optional

# ─── 路径 ──────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_DIR, "data")
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DB_PATH = os.path.join(DATA_DIR, "football.db")

sys.path.insert(0, SCRIPT_DIR)
from fetch_stats import fetch_match_stats

STATS_CACHE_PATH = os.path.join(DATA_DIR, 'cache', 'stats_cache.json')

def _load_stats_cache() -> dict:
    """加载统计缓存（H2H+近期战绩）"""
    if not os.path.exists(STATS_CACHE_PATH):
        return {}
    try:
        with open(STATS_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载统计缓存失败: {e}")
        return {}

def _format_stats(raw: dict) -> dict:
    """将缓存原始格式转为前端需要的 {home_recent, away_recent, h2h}

    旧缓存中 result 是盘路结果（赢/输），用比分重新计算比赛结果（胜/负/平）。
    """
    def _result(hs, vs):
        if hs > vs: return '胜'
        if hs < vs: return '负'
        return '平'

    out = {}
    for key_in, key_out in [('home_form', 'home_recent'), ('away_form', 'away_recent')]:
        items = raw.get(key_in, [])
        cleaned = []
        for m in items[:10]:   # 最多10场
            hs = int(m.get('home_score', 0) or 0)
            vs = int(m.get('away_score', 0) or 0)
            cleaned.append({
                'result': _result(hs, vs),
                'league': m.get('league', ''),
                'date': m.get('date', ''),
                'home': m.get('home', ''),
                'away': m.get('away', ''),
                'home_score': hs,
                'away_score': vs,
            })
        out[key_out] = cleaned

    h2h = raw.get('h2h', [])
    out['h2h'] = [{
        'result': _result(int(m.get('home_score',0) or 0), int(m.get('away_score',0) or 0)),
        'league': m.get('league', ''),
        'date': m.get('date', ''),
        'home': m.get('home', ''),
        'away': m.get('away', ''),
        'home_score': int(m.get('home_score',0) or 0),
        'away_score': int(m.get('away_score',0) or 0),
    } for m in h2h[:10]]

    return out

# ─── 算法常量 ──────────────────────────────────────
SMOOTH_ALPHA_BASE = 0.02      # 贝叶斯平滑基值（均衡比赛用）
SMOOTH_ALPHA_SKEW = 0.35      # 方差自适应系数：偏离均衡每0.1加权0.035
HOME_ADJ = 0.01               # 主场调整量：加到模型主胜，从平/负各扣0.003
LEAGUE_PRIOR_LAMBDA = 0.15    # 联赛基准率混合权重（0=不使用，0.15=15%基准+85%市场）
LEAGUE_PRIOR_MIN_MATCHES = 10  # 联赛基准最小样本量
MOVEMENT_STRENGTH = 0.5       # 初盘→即时变化调整强度：分歧10% → 概率加权±5%


# ─── 日志 ──────────────────────────────────────────
logger = logging.getLogger("ai_analysis")
def setup_logging():
    log_dir = os.path.join(REPO_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"analysis_{datetime.now():%Y%m%d}.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
    return logging.getLogger("ai_analysis")

logger = setup_logging()

# ─── 核心数学 ──────────────────────────────────────

def implied_from_odds(odds_w: float, odds_d: float, odds_l: float) -> Tuple[Optional[float], ...]:
    """从赔率反推隐含概率（去抽水归一化）"""
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None, 0.0
    iw, id_, il = 1.0 / odds_w, 1.0 / odds_d, 1.0 / odds_l
    total = iw + id_ + il
    margin = total - 1.0
    return iw / total, id_ / total, il / total, margin

def middle_direction(mw: float, md: float, ml: float) -> Tuple[str, str, float]:
    """取中间概率方向"""
    items = [('主胜', mw, 'home'), ('平局', md, 'draw'), ('客胜', ml, 'away')]
    sorted_items = sorted(items, key=lambda x: x[1])
    dir_cn, prob, dir_en = sorted_items[1]
    return dir_cn, dir_en, round(prob, 4)

def max_direction(mw: float, md: float, ml: float) -> Tuple[str, str, float]:
    """取最大概率方向（供LGBM使用）"""
    items = [('主胜', mw, 'home'), ('平局', md, 'draw'), ('客胜', ml, 'away')]
    dir_cn, prob, dir_en = max(items, key=lambda x: x[1])
    return dir_cn, dir_en, round(prob, 4)

# ─── 数据加载 ──────────────────────────────────────

def load_raw_matches() -> List[Dict]:
    """从 data/matches_*.json 加载原始比赛数据"""
    all_ms = []
    files = sorted(glob.glob(os.path.join(DATA_DIR, "matches_*.json")))
    if not files:
        logger.warning("未找到 data/matches_*.json 文件")
        return []
    for fp in files:
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            ms = data.get('matches', data if isinstance(data, list) else [])
            if isinstance(ms, list):
                all_ms.extend(ms)
                logger.debug(f"  {os.path.basename(fp)}: {len(ms)} 场")
        except Exception as e:
            logger.warning(f"读取 {fp} 失败: {e}")
    # 去重 (按 fid 优先, 保留数据更全的)
    seen = {}
    deduped = []
    for m in all_ms:
        fid = m.get('fid') or m.get('id')
        if fid:
            key = str(fid)
        else:
            key = f"{m.get('home_team','')}|{m.get('away_team','')}|{m.get('date','')}"
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(m)
        else:
            # 保留字段更多的那个
            existing = deduped[seen[key]]
            if len(m) > len(existing):
                deduped[seen[key]] = m
    logger.info(f"原始数据 {len(all_ms)} 场 → 去重后 {len(deduped)} 场")

    # 第二道去重: 按(主队,客队)去重 — 同一场比赛在不同日期文件里出现(不同fid)
    # 只对尚未有比分的比赛做此去重(有比分的是已完场的, 可能真的是两场)
    seen_pair = {}
    pair_deduped = []
    for m in deduped:
        home = m.get('home_team', '').strip()
        away = m.get('away_team', '').strip()
        score = (m.get('score') or '').strip()
        if not score:
            key = f'{home}|{away}'
            if key in seen_pair:
                # 保留时间更合理的那条(非00:00优先)
                idx = seen_pair[key]
                existing = pair_deduped[idx]
                old_mt = existing.get('match_time', '')
                new_mt = m.get('match_time', '')
                # 如果现有条目时间是00:00且新条目有合理时间, 替换
                if ('00:00' in old_mt or not old_mt) and '00:00' not in new_mt and new_mt:
                    pair_deduped[idx] = m
                continue
            seen_pair[key] = len(pair_deduped)
        pair_deduped.append(m)

    if len(pair_deduped) < len(deduped):
        logger.info(f"交叉文件去重: {len(deduped)} → {len(pair_deduped)} 场")
    return pair_deduped

def load_db_matches(db_path: str = DB_PATH, days: int = 14) -> List[Dict]:
    """从数据库加载已有预测（用于合并）"""
    if not os.path.exists(db_path):
        logger.warning(f"数据库不存在: {db_path}")
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT date, home_team, away_team, kickoff_time, league,
                   odds_win, odds_draw, odds_loss,
                   poisson_win, poisson_draw, poisson_loss,
                   final_win, final_draw, final_loss,
                   ev_win, ev_draw, ev_loss,
                   prediction, prediction_prob,
                   home_lambda, away_lambda,
                   source, match_id
            FROM poisson_predictions
            WHERE date >= date('now', ?)
            ORDER BY date, kickoff_time
        """, (f'-{days} days',))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        logger.info(f"数据库加载 {len(rows)} 条历史预测")
        return rows
    except Exception as e:
        logger.warning(f"数据库读取失败: {e}")
        return []

# ─── 联赛基准率 ────────────────────────────────────
GLOBAL_PRIOR = (0.45, 0.24, 0.31)  # 全局默认（主/平/客）

def load_league_priors(db_path: str = DB_PATH) -> Dict[str, Tuple[float, float, float]]:
    """从历史数据库统计各联赛胜平负率作为贝叶斯先验"""
    priors = {}
    if not os.path.exists(db_path):
        logger.warning(f"联赛基准：DB不存在 {db_path}，使用全局默认")
        return priors
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("""
            SELECT league,
                   COUNT(*) as total,
                   SUM(CASE WHEN CAST(SUBSTR(reference_score, 1, INSTR(reference_score, '-')-1) AS INTEGER) >
                                  CAST(SUBSTR(reference_score, INSTR(reference_score, '-')+1) AS INTEGER)
                             THEN 1 ELSE 0 END) as home_wins,
                   SUM(CASE WHEN CAST(SUBSTR(reference_score, 1, INSTR(reference_score, '-')-1) AS INTEGER) =
                                  CAST(SUBSTR(reference_score, INSTR(reference_score, '-')+1) AS INTEGER)
                             THEN 1 ELSE 0 END) as draws,
                   SUM(CASE WHEN CAST(SUBSTR(reference_score, 1, INSTR(reference_score, '-')-1) AS INTEGER) <
                                  CAST(SUBSTR(reference_score, INSTR(reference_score, '-')+1) AS INTEGER)
                             THEN 1 ELSE 0 END) as away_wins
            FROM poisson_predictions
            WHERE reference_score IS NOT NULL AND reference_score != ''
            GROUP BY league
        """)
        for row in cur.fetchall():
            league, total, hw, d, aw = row
            if total < LEAGUE_PRIOR_MIN_MATCHES:
                continue
            priors[league] = (round(hw/total, 4), round(d/total, 4), round(aw/total, 4))
        conn.close()
        logger.info(f"联赛基准率: 加载 {len(priors)} 个联赛 (最少{LEAGUE_PRIOR_MIN_MATCHES}场)")
        if priors:
            for k, v in sorted(priors.items(), key=lambda x: -x[1][0]):
                logger.debug(f"  {k}: W{v[0]:.1%} D{v[1]:.1%} L{v[2]:.1%}")
        return priors
    except Exception as e:
        logger.warning(f"联赛基准率加载失败: {e}")
        return priors

# ─── LGBM 方案C ─────────────────────────────────────
LGBM_MODEL_PATH = os.path.join(DATA_DIR, 'cache', 'lgbm_model.json')
_lgbm_model = None  # 全局缓存

FEATURE_NAMES = [
    'poisson_w','poisson_d','poisson_l',
    'final_w','final_d','final_l',
    'implied_w','implied_d','implied_l',
    'pin_open_w','pin_open_d','pin_open_l',
    'pin_close_w','pin_close_d','pin_close_l',
    'pin_move_w','pin_move_d','pin_move_l',
    'pin_diff_w','pin_diff_d','pin_diff_l',
    'pin_margin',
    'disagree_w','disagree_d','disagree_l',
    'poisson_market_margin','poisson_market_draw_diff',
    'odds_level','draw_premium',
    'lambda_h','lambda_a',
]

def _implied_from_odds(ow, od, ol):
    """赔率 → 去水隐含概率"""
    if ow <= 0 or od <= 0 or ol <= 0:
        return (0,)*4
    inv = (1.0/ow, 1.0/od, 1.0/ol)
    t = sum(inv)
    return (inv[0]/t, inv[1]/t, inv[2]/t, t-1.0)

def extract_lgbm_features(ow, od, ol, model_w, model_d, model_l, margin,
                           open_w=None, open_d=None, open_l=None,
                           poisson_w=None, poisson_d=None, poisson_l=None,
                           implied_w=None, implied_d=None, implied_l=None,
                           home_rank=None, away_rank=None,
                           home_pts=None, away_pts=None):
    """从当前比赛数据提取27维特征（与 train_lgbm.py 一致）"""
    cw, cd, cl, _ = _implied_from_odds(ow, od, ol)

    # 开盘隐含概率
    if open_w and open_w > 1 and open_d and open_d > 1 and open_l and open_l > 1:
        pw, pd_, pl, pin_margin = _implied_from_odds(open_w, open_d, open_l)
    else:
        pw = pd_ = pl = pin_margin = 0.0

    # 赔率变动幅度 = 收盘 - 开盘 (pin_diff)
    diff_w = cw - pw if pw > 0 else 0.0
    diff_d = cd - pd_ if pd_ > 0 else 0.0
    diff_l = cl - pl if pl > 0 else 0.0

    # 泊松概率
    p_w = float(poisson_w) if poisson_w and float(poisson_w) > 0 else 0.0
    p_d = float(poisson_d) if poisson_d and float(poisson_d) > 0 else 0.0
    p_l = float(poisson_l) if poisson_l and float(poisson_l) > 0 else 0.0

    # 隐含概率
    iw = float(implied_w) if implied_w and float(implied_w) > 0 else cw
    id_ = float(implied_d) if implied_d and float(implied_d) > 0 else cd
    il = float(implied_l) if implied_l and float(implied_l) > 0 else cl

    # 泊松 vs 市场差异
    poisson_market_margin = abs(p_w - iw) + abs(p_d - id_) + abs(p_l - il) if p_w > 0 else 0.0
    poisson_market_draw_diff = p_d - id_ if p_d > 0 else 0.0

    # 赔率级别
    odds_level = 1.0 / max(ow, 1.01)

    # 平局溢价
    draw_premium = ((od - (ow + ol)/2) / max((ow + ol)/2, 0.01)) if ow > 1.01 and ol > 1.01 else 0.0

    # 排名/积分
    hr = float(home_rank) if home_rank and float(home_rank) > 0 else 0.0
    ar = float(away_rank) if away_rank and float(away_rank) > 0 else 0.0
    hp = float(home_pts) if home_pts and float(home_pts) > 0 else 0.0
    ap = float(away_pts) if away_pts and float(away_pts) > 0 else 0.0

    return [
        p_w, p_d, p_l,                    # poisson_w/d/l
        model_w, model_d, model_l,         # final_w/d/l
        iw, id_, il,                       # implied_w/d/l
        pw, pd_, pl,                       # pin_open_w/d/l
        cw, cd, cl,                        # pin_close_w/d/l
        diff_w, diff_d, diff_l,            # pin_diff_w/d/l
        pin_margin,                        # pin_margin
        poisson_market_margin, poisson_market_draw_diff,
        odds_level, draw_premium,
        hr, ar, hp, ap,                    # home/away_rank, home/away_pts
    ]

def load_lgbm_model():
    """加载LGBM模型（线程安全缓存）"""
    global _lgbm_model
    if _lgbm_model is not None:
        return _lgbm_model
    if not os.path.exists(LGBM_MODEL_PATH):
        logger.warning(f"LGBM模型不存在: {LGBM_MODEL_PATH}，方案C不可用")
        return None
    try:
        with open(LGBM_MODEL_PATH) as f:
            d = json.load(f)
        model = object.__new__(object)  # 简化反序列化
        # 直接用dict
        logger.info(f"LGBM模型已加载: {d.get('n_estimators',0)}棵树, "
                     f"测试准确率 {d.get('test_accuracy',0):.1%}")
        _lgbm_model = d
        return d
    except Exception as e:
        logger.warning(f"LGBM模型加载失败: {e}")
        return None

def predict_lgbm(model_dict, features):
    """用已训练的SimpleLGBM做预测（纯Python推理）"""
    if not model_dict:
        return None
    trees = model_dict.get('trees', [])
    init_pred = model_dict.get('init_pred', [1/3]*3)
    lr = model_dict.get('learning_rate', 0.1)
    if not trees:
        return None
    
    x = np.array(features, dtype=float)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    
    raw = np.array([float(init_pred[0]), float(init_pred[1]), float(init_pred[2])])
    
    for cls in range(3):
        if cls >= len(trees):
            continue
        for tree_dict in trees[cls]:
            val = _predict_tree(x, tree_dict)
            raw[cls] += lr * val
    
    # Softmax
    exp_raw = np.exp(raw - np.max(raw))
    proba = exp_raw / np.sum(exp_raw)
    return proba.tolist()

def _predict_tree(x, node):
    """递归树预测"""
    if not isinstance(node, dict) or 'feature' not in node:
        return float(node) if not isinstance(node, dict) else float(node.get('value', 0.0))
    feat = node['feature']
    if feat < len(x) and x[feat] <= node['threshold']:
        return _predict_tree(x, node.get('left', 0.0))
    else:
        return _predict_tree(x, node.get('right', 0.0))


# ─── 实时排名获取 ──────────────────────────────────

def fetch_live_rankings(matches: List[Dict]) -> None:
    """获取联赛排名（500.com已封，此项已废弃）
    
    排名特征在模型训练中已固定为0，不影响预测结果。
    保留函数签名以兼容调用代码。
    """
    from_data = sum(1 for m in matches if m.get('home_rank') and m.get('away_rank'))
    if from_data:
        logger.info(f"排名: {from_data}/{len(matches)} 场已有排名（来自历史数据）")


# ─── 分析引擎 ──────────────────────────────────────

def analyze_matches(matches: List[Dict], league_priors: Dict[str, Tuple[float, float, float]] = None) -> List[Dict]:
    """对比赛列表执行EV/泊松分析"""
    results = []
    skipped = 0
    if league_priors is None:
        league_priors = {}
    lambda_ = LEAGUE_PRIOR_LAMBDA
    stats_cache = _load_stats_cache()
    stats_hit = [0, 0]  # [total, cache_hit]

    # 预取排名（GA上跳过）
    in_gha = os.environ.get('GITHUB_ACTIONS') == 'true'
    if not in_gha:
        try:
            fetch_live_rankings(matches)
            logger.debug(f"排名预取完成 ({len(matches)}场)")
        except Exception as e:
            logger.warning(f"排名预取失败: {e}，将使用默认值")

    for m in matches:
        # 赔率源：优先平博，fallback到马会
        ow = float(m.get('odds_pinnacle_win', 0) or 0)
        od = float(m.get('odds_pinnacle_draw', 0) or 0)
        ol = float(m.get('odds_pinnacle_loss', 0) or 0)
        odds_source = 'pinnacle'

        if not (ow > 1 and od > 1 and ol > 1):
            # 尝试用马会赔率
            ow = float(m.get('odds_hkjc_win', 0) or 0)
            od = float(m.get('odds_hkjc_draw', 0) or 0)
            ol = float(m.get('odds_hkjc_loss', 0) or 0)
            if ow > 1 and od > 1 and ol > 1:
                odds_source = 'hkjc'
            else:
                skipped += 1
                continue

        # 1. 隐含概率（去抽水）
        imp_w, imp_d, imp_l, margin = implied_from_odds(ow, od, ol)
        if imp_w is None:
            skipped += 1
            continue

        # 2. 方差自适应贝叶斯平滑 + 主场修正
        #    偏离均衡（1/3）越多，平滑越强，极端赔率自然减弱
        skew = max(imp_w, imp_d, imp_l) - 1/3
        alpha = min(SMOOTH_ALPHA_BASE + skew * SMOOTH_ALPHA_SKEW, 0.40)
        sw = imp_w * (1 - alpha) + alpha / 3 + HOME_ADJ
        sd = imp_d * (1 - alpha) + alpha / 3 - HOME_ADJ * 0.3
        sl = imp_l * (1 - alpha) + alpha / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        model_w, model_d, model_l = sw/st, sd/st, sl/st

        # 2b. 联赛基准率混合（贝叶斯先验校正）
        #     联赛历史结果分布与市场概率按权重混合
        league = m.get('event', '') or m.get('league', '')
        league_baseline = None
        if lambda_ > 0 and league:
            prior = league_priors.get(league, GLOBAL_PRIOR)
            pw, pd, pl = prior
            # 混合：最终 = (1-λ) × 平滑模型 + λ × 联赛基准
            fw = model_w * (1 - lambda_) + pw * lambda_
            fd = model_d * (1 - lambda_) + pd * lambda_
            fl = model_l * (1 - lambda_) + pl * lambda_
            ft = fw + fd + fl
            model_w, model_d, model_l = ft and (fw/ft, fd/ft, fl/ft) or (1/3, 1/3, 1/3)
            league_baseline = prior

        # 2c. 初盘→即时变化直接调整
        #     ⚠️ 存一份调整前的概率给LGBM用（LGBM训练时没吃过调整特征）
        lgbm_feat_w, lgbm_feat_d, lgbm_feat_l = model_w, model_d, model_l

        # 3. LGBM 方案C预测（先于分歧调整，以便获取分歧信号）
        lgbm_model = load_lgbm_model()
        lgbm_w, lgbm_d, lgbm_l = lgbm_feat_w, lgbm_feat_d, lgbm_feat_l  # 默认fallback
        if lgbm_model:
            if odds_source == 'hkjc':
                open_w_lgbm = m.get('odds_hkjc_open_win') or m.get('odds_hkjc_win')
                open_d_lgbm = m.get('odds_hkjc_open_draw') or m.get('odds_hkjc_draw')
                open_l_lgbm = m.get('odds_hkjc_open_loss') or m.get('odds_hkjc_loss')
            else:
                open_w_lgbm = m.get('odds_pinnacle_open_win') or m.get('odds_pinnacle_win')
                open_d_lgbm = m.get('odds_pinnacle_open_draw') or m.get('odds_pinnacle_draw')
                open_l_lgbm = m.get('odds_pinnacle_open_loss') or m.get('odds_pinnacle_loss')
            feat = extract_lgbm_features(ow, od, ol, lgbm_feat_w, lgbm_feat_d, lgbm_feat_l, margin,
                                          open_w=open_w_lgbm, open_d=open_d_lgbm, open_l=open_l_lgbm,
                                          poisson_w=m.get('poisson_win'), poisson_d=m.get('poisson_draw'), poisson_l=m.get('poisson_loss'),
                                          implied_w=m.get('implied_prob_w'), implied_d=m.get('implied_prob_d'), implied_l=m.get('implied_prob_l'),
                                          home_rank=m.get('home_rank'), away_rank=m.get('away_rank'),
                                          home_pts=m.get('home_points'), away_pts=m.get('away_points'))
            proba = predict_lgbm(lgbm_model, feat)
            if proba:
                    lgbm_w, lgbm_d, lgbm_l = proba

        # LGBM主推方向（分歧信号用）
        lgbm_final = [lgbm_w, lgbm_d, lgbm_l]
        lgbm_dir_idx = lgbm_final.index(max(lgbm_final))
        dir_names = ['home', 'draw', 'away']
        lgbm_max_dir = dir_names[lgbm_dir_idx]

        # 模型最大值方向（分歧信号用，用调整前的原始概率）
        raw_model_probs = [lgbm_feat_w, lgbm_feat_d, lgbm_feat_l]
        model_max_dir = dir_names[raw_model_probs.index(max(raw_model_probs))]
        lgbm_disagree = (lgbm_max_dir != model_max_dir)

        #     用户经验（优化后v2）：
        #       · 升大必死99%（大幅回升 → 市场失真）
        #       · 小幅降水必死80%（公众噪音，不真实）
        #       · 大降水 → 市场真方向，不惩罚
        #       · 7倍以上 → 升降都很难开出
        #     核心：赔率大幅波动（尤其回升）说明市场不可信
        if odds_source == 'hkjc':
            open_w = float(m.get('odds_hkjc_open_win', 0) or 0)
            open_d = float(m.get('odds_hkjc_open_draw', 0) or 0)
            open_l = float(m.get('odds_hkjc_open_loss', 0) or 0)
        else:
            open_w = float(m.get('odds_pinnacle_open_win', 0) or 0)
            open_d = float(m.get('odds_pinnacle_open_draw', 0) or 0)
            open_l = float(m.get('odds_pinnacle_open_loss', 0) or 0)

        if open_w > 1 and open_d > 1 and open_l > 1:
            div_w = (ow - open_w) / open_w  # 正=回升，负=下降
            div_d = (od - open_d) / open_d
            div_l = (ol - open_l) / open_l
            cur_odds = [ow, od, ol]
            probs = [model_w, model_d, model_l]
            divs = [div_w, div_d, div_l]
            for i in range(3):
                # Rule 1: 7倍以上 → 升降都很难开出，直接85%不信任
                if cur_odds[i] >= 7.0:
                    distrust = 0.85
                else:
                    d = max(-1, min(1, divs[i]))
                    if d > 0.15:                     # 大幅回升 >15%
                        distrust = 0.60               # 高度不信任
                    elif d > 0.08:                   # 中幅回升 8~15%
                        distrust = 0.30               # 中度不信任
                    elif d > 0.04:                   # 小幅回升 4~8%
                        distrust = 0.10               # 轻度不信任
                    elif d < -0.10:                  # 大幅降水 ≥10% → 市场真方向，不惩罚
                        distrust = 0
                    elif d < 0:                      # 小幅降水 0~-10%
                        distrust = abs(d) / 0.10 * 0.50  # 最大50%惩罚
                    else:                            # 无变化
                        distrust = 0
                # 分歧惩罚：LGBM主推≠模型最大值方向，额外+20%
                if distrust > 0 and lgbm_disagree and d > 0:
                    distrust = min(0.95, distrust + 0.20)
                probs[i] *= max(0.05, 1 - distrust)
            # 重归一化
            t = sum(probs)
            model_w, model_d, model_l = probs[0]/t, probs[1]/t, probs[2]/t

        # ─── 硬编码概率封顶分配 ──────────────────────────
        # 统计结论: 将模型最大值封顶40%，按LGB平局区间比例分给剩余两方
        # 分配比例基于DB历史数据的实际赛果统计
        draw_boosted = False
        prob_list = [model_w, model_d, model_l]  # [H=0, D=1, A=2]
        max_val = max(prob_list)
        max_idx = prob_list.index(max_val)
        max_side = ['home', 'draw', 'away'][max_idx]
        excess = max_val - 0.40

        if excess > 0 and lgbm_d >= 0.05:
            draw_boosted = True
            prob_list[max_idx] = 0.40  # 封顶

            if lgbm_d >= 0.40:
                # LGB平≥40%: 多余量全给平局
                prob_list[1] += excess
            elif lgbm_d >= 0.30:
                # LGB平30-39%: 多余量主要给平局
                if max_side == 'home':
                    prob_list[1] += excess * 0.60   # D得60%
                    prob_list[2] += excess * 0.40   # A得40%
                elif max_side == 'away':
                    prob_list[1] += excess * 0.50   # D得50%
                    prob_list[0] += excess * 0.50   # H得50%
                else:  # draw
                    prob_list[0] += excess * 0.70   # H得70%
                    prob_list[2] += excess * 0.30   # A得30%
            else:
                # LGB平24-29%: 多余量主要给对面（最小值方）
                if max_side == 'home':
                    prob_list[1] += excess * 0.20   # D得20%
                    prob_list[2] += excess * 0.80   # A得80%
                elif max_side == 'away':
                    prob_list[1] += excess * 0.02   # D得2%
                    prob_list[0] += excess * 0.98   # H得98%
                else:  # draw
                    prob_list[0] += excess * 0.80   # H得80%
                    prob_list[2] += excess * 0.20   # A得20%

            # 各自不超过39%检查
            for i in range(3):
                if i != max_idx and prob_list[i] > 0.39:
                    overflow = prob_list[i] - 0.39
                    prob_list[i] = 0.39
                    other = [j for j in range(3) if j != max_idx and j != i][0]
                    prob_list[other] += overflow

            t = sum(prob_list)
            model_w, model_d, model_l = prob_list[0]/t, prob_list[1]/t, prob_list[2]/t

        # ─── 硬编码平局衡量（Draw Confidence）───────────────
        # 基于1512场训练数据统计规律:
        #   · LGB draw≥34% → 71%平局率 / ≥36% → 84%
        #   · LGB-Poisson分歧≥6% → 50%平局率 / ≥10% → 57%
        #   · LGB推平(draw为最大值) → 70%平局率
        #   · 组合: d≥32%+分歧≥6% → 63% | d≥34%+分歧≥4% → 73%
        #   · Pin draw↓ + d≥32%+分歧≥6% → 66%
        draw_conf = {'score': 0, 'signal': 'none', 'diff_lgb_poisson': 0}

        # 1) LGB draw概率评分（最强单一信号）
        if lgbm_d >= 0.36:
            draw_conf['score'] += 30
        elif lgbm_d >= 0.34:
            draw_conf['score'] += 25
        elif lgbm_d >= 0.32:
            draw_conf['score'] += 20
        elif lgbm_d >= 0.30:
            draw_conf['score'] += 10
        elif lgbm_d < 0.28:
            draw_conf['score'] -= 5

        # 2) LGB-Poisson分歧评分（次强信号）
        diff_lgb_poisson = lgbm_d - model_d
        draw_conf['diff_lgb_poisson'] = diff_lgb_poisson
        if diff_lgb_poisson >= 0.10:
            draw_conf['score'] += 25
        elif diff_lgb_poisson >= 0.08:
            draw_conf['score'] += 18
        elif diff_lgb_poisson >= 0.06:
            draw_conf['score'] += 12
        elif diff_lgb_poisson >= 0.04:
            draw_conf['score'] += 8
        elif diff_lgb_poisson >= 0.02:
            draw_conf['score'] += 3

        # 3) Pinnacle draw赔率变化
        if open_d > 1 and od > 1:
            draw_chg = (od - open_d) / open_d
            if draw_chg < 0:           # 赔率下降→市场看好平局
                draw_conf['score'] += 10
            elif draw_chg > 0.08:      # 大幅回升→市场不看好
                draw_conf['score'] -= 5

        # ─── LGB推平时三向再分配 ────────────────────
        # 当LGB认为平局最可能时，模型三向趋于均值分布
        # 将优势方的多余置信度转移给弱势方（和少量draw）
        if lgbm_max_dir == 'draw':
            h_vs_a_diff = abs(model_w - model_l)
            if h_vs_a_diff >= 0.06:  # 主客明显不平衡
                transfer = min(0.04, h_vs_a_diff * 0.3)
                to_underdog = transfer * 0.75  # 75%→弱势方
                to_draw = transfer * 0.25      # 25%→draw
                if model_w > model_l:
                    model_w -= transfer
                    model_l += to_underdog
                else:
                    model_l -= transfer
                    model_w += to_underdog
                model_d += to_draw

        # 4. LGBM 推荐方向（主推，取最大值）
        # 只有当LGB认为平局≥模型时（分歧≥0）才向LGB靠拢
        # 如果分歧为负，说明模型已给出更高平局概率，维持原值
        if draw_conf['score'] >= 50 and diff_lgb_poisson > 0:
            draw_conf['signal'] = 'strong'
            blend = 0.50  # 强信号：大幅向LGB靠拢
            boosted_d = model_d * (1 - blend) + lgbm_d * blend
            # 不设上限，让draw有机会从第三→第二
            remaining = 1 - boosted_d
            other_total = model_w + model_l
            if other_total > 0:
                model_w = model_w / other_total * remaining
                model_l = model_l / other_total * remaining
            model_d = boosted_d
        elif draw_conf['score'] >= 35 and diff_lgb_poisson > 0:
            draw_conf['signal'] = 'moderate'
            blend = 0.35
            boosted_d = model_d * (1 - blend) + lgbm_d * blend
            remaining = 1 - boosted_d
            other_total = model_w + model_l
            if other_total > 0:
                model_w = model_w / other_total * remaining
                model_l = model_l / other_total * remaining
            model_d = boosted_d
        elif draw_conf['score'] >= 50:
            # 高分但负分歧：不调整概率，仅标记strong信号
            draw_conf['signal'] = 'strong'
        elif draw_conf['score'] >= 35:
            draw_conf['signal'] = 'moderate'

        # 4. LGBM 推荐方向（主推，取最大值）
        lgbm_dir_cn, lgbm_dir_en, lgbm_dir_prob = max_direction(lgbm_w, lgbm_d, lgbm_l)
        # 模型概率方向（中间值，备选）
        model_dir_cn, model_dir_en, model_dir_prob = middle_direction(model_w, model_d, model_l)

        # 4. 最大概率值
        max_prob_val = max(model_w, model_d, model_l)

        # 比分 → 实际结果方向
        score_raw = m.get('score', '')
        score_parts = score_raw.split('-')
        if len(score_parts) == 2 and score_parts[0].strip().isdigit() and score_parts[1].strip().isdigit():
            sh, sa = int(score_parts[0]), int(score_parts[1])
            actual = 'home' if sh > sa else ('draw' if sh == sa else 'away')
        else:
            actual = ''
        hit_which = ''
        if actual and actual == model_dir_en and actual == lgbm_dir_en:
            hit_which = 'LM'
        elif actual and actual == model_dir_en:
            hit_which = 'M'
        elif actual and actual == lgbm_dir_en:
            hit_which = 'L'
        actual_cn = ''
        if actual == 'home': actual_cn = '胜'
        elif actual == 'draw': actual_cn = '和'
        elif actual == 'away': actual_cn = '负'
        hit = f'{hit_which}·{actual_cn}✓' if hit_which else ('✘' if actual else '')

        # 8. 赔率对比数据（开盘 vs 即时）
        comparison = {}
        if ow > 1 and od > 1 and ol > 1:
            if odds_source == 'hkjc':
                op_w = float(m.get('odds_hkjc_open_win', 0) or 0)
                op_d = float(m.get('odds_hkjc_open_draw', 0) or 0)
                op_l = float(m.get('odds_hkjc_open_loss', 0) or 0)
            else:
                op_w = float(m.get('odds_pinnacle_open_win', 0) or 0)
                op_d = float(m.get('odds_pinnacle_open_draw', 0) or 0)
                op_l = float(m.get('odds_pinnacle_open_loss', 0) or 0)
            if op_w > 1 and op_d > 1 and op_l > 1:
                pct_w = (ow - op_w) / op_w * 100
                pct_d = (od - op_d) / op_d * 100
                pct_l = (ol - op_l) / ol * 100
                comparison = {
                    'open': [round(op_w,2), round(op_d,2), round(op_l,2)],
                    'current': [round(ow,2), round(od,2), round(ol,2)],
                    'div_pct': [round(pct_w,1), round(pct_d,1), round(pct_l,1)],
                    'source': odds_source,
                }

        # 8b. 备查列：读另一家公司的赔率（双向对比）
        pin_comparison = {}
        if odds_source == 'pinnacle':
            # 主源平博，备查阅马会
            pw = float(m.get('odds_hkjc_win', 0) or 0)
            pd = float(m.get('odds_hkjc_draw', 0) or 0)
            pl = float(m.get('odds_hkjc_loss', 0) or 0)
            p_label = 'hkjc'
        else:
            # 主源马会，备查阅平博
            pw = float(m.get('odds_pinnacle_win', 0) or 0)
            pd = float(m.get('odds_pinnacle_draw', 0) or 0)
            pl = float(m.get('odds_pinnacle_loss', 0) or 0)
            p_label = 'pinnacle'
        if pw > 1 and pd > 1 and pl > 1:
            if p_label == 'hkjc':
                hop_w = float(m.get('odds_hkjc_open_win', 0) or 0)
                hop_d = float(m.get('odds_hkjc_open_draw', 0) or 0)
                hop_l = float(m.get('odds_hkjc_open_loss', 0) or 0)
            else:
                hop_w = float(m.get('odds_pinnacle_open_win', 0) or 0)
                hop_d = float(m.get('odds_pinnacle_open_draw', 0) or 0)
                hop_l = float(m.get('odds_pinnacle_open_loss', 0) or 0)
            if hop_w > 1 and hop_d > 1 and hop_l > 1:
                pct_w = (pw - hop_w) / hop_w * 100
                pct_d = (pd - hop_d) / hop_d * 100
                pct_l = (pl - hop_l) / pl * 100
                pin_comparison = {
                    'open': [round(hop_w,2), round(hop_d,2), round(hop_l,2)],
                    'current': [round(pw,2), round(pd,2), round(pl,2)],
                    'div_pct': [round(pct_w,1), round(pct_d,1), round(pct_l,1)],
                    'source': p_label,
                }

        # ─── 风险标记 ──────────────────────────────────
        # 🚩分歧陷阱: LGBM最大值≥53% 且 模型同方向≥40%
        # ⚠️模型犹豫: 模型top1-top2差距<10% 或 LGBM主推概率<45%
        warning = ''
        try:
            lmax = max(lgbm_w, lgbm_d, lgbm_l)
            ldir_idx = [lgbm_w, lgbm_d, lgbm_l].index(lmax)
            raw_vals = sorted([lgbm_feat_w, lgbm_feat_d, lgbm_feat_l], reverse=True)
            gap = raw_vals[0] - raw_vals[1]

            # 🚩分歧陷阱
            model_val = [model_w, model_d, model_l][ldir_idx]
            trap = (lmax >= 0.53 and model_val >= 0.40)
            # ⚠️模型犹豫
            uncer = (gap < 0.10 or lmax < 0.45)

            parts = []
            if trap: parts.append('🚩')
            if uncer: parts.append('⚠️')
            warning = ''.join(parts)
        except Exception:
            pass

        # 统一时间格式: 北单 "07-15 02:45" → "2026-07-15 02:45"
        raw_date = m.get('date', '')
        raw_time = m.get('match_time', '')
        if raw_time and re.match(r'^\d{4}-\d{2}-\d{2}\s', raw_time):
            # HKJC: "2026-07-15 03:00" 直接用
            norm_time = raw_time
        elif raw_time and raw_date and len(raw_date) >= 4:
            # 北单: "07-15 02:45" → prepend year from date
            parts = raw_time.split(' ', 1)
            norm_time = raw_date[:4] + '-' + raw_time if len(parts) == 2 else raw_time
        else:
            norm_time = raw_time or raw_date

        results.append({
            'fid': m.get('fid', ''),
            'date': raw_date,
            'match_time': norm_time,
            'event': m.get('event', ''),
            'home_team': m.get('home_team', ''),
            'away_team': m.get('away_team', ''),
            'score': score_raw,
            'hit': hit,
            'source': m.get('source', 'beidan'),
            'odds_source': odds_source,
            'odds_win': round(ow, 2),
            'odds_draw': round(od, 2),
            'odds_loss': round(ol, 2),
            'margin': round(margin, 4),
            'model_win': round(model_w, 4),
            'model_draw': round(model_d, 4),
            'model_loss': round(model_l, 4),
            'raw_model_win': round(lgbm_feat_w, 4),
            'raw_model_draw': round(lgbm_feat_d, 4),
            'raw_model_loss': round(lgbm_feat_l, 4),
            'open_win_pin': round(open_w, 2) if open_w > 1 else 0,
            'open_draw_pin': round(open_d, 2) if open_d > 1 else 0,
            'open_loss_pin': round(open_l, 2) if open_l > 1 else 0,
            'lgbm_prediction': lgbm_dir_en,      # LGBM方向（主推）
            'lgbm_prediction_cn': lgbm_dir_cn,
            'lgbm_prediction_prob': round(lgbm_dir_prob, 4),
            'prediction': lgbm_dir_en,           # 兼容别名（同lgbm_prediction）
            'prediction_cn': lgbm_dir_cn,
            'prediction_prob': round(lgbm_dir_prob, 4),
            'model_prediction': model_dir_en,    # 模型中值方向（备选）
            'model_prediction_cn': model_dir_cn,
            'model_prediction_prob': round(model_dir_prob, 4),
            'draw_boosted': draw_boosted,
            'draw_confidence': round(draw_conf['score']),
            'draw_signal': draw_conf['signal'],
            'diff_lgb_poisson': round(draw_conf['diff_lgb_poisson'], 4),
            'lgbm_win': round(lgbm_w, 4),
            'lgbm_draw': round(lgbm_d, 4),
            'lgbm_loss': round(lgbm_l, 4),
            'comparison': comparison,
            'pin_comparison': pin_comparison,
            'league_baseline': league_baseline,
            'league': m.get('league', ''),
            'home_rank': m.get('home_rank', 0),
            'away_rank': m.get('away_rank', 0),
            'home_pts': m.get('home_pts', 0),
            'away_pts': m.get('away_pts', 0),
            'warning': warning,
            # 近期战绩
            'stats': None,
        })

    logger.info(f"分析完成: {len(results)} 场 (跳过 {skipped} 场无赔率)")

    # ─── 赛后统计获取（并行） ──────────────────────
    if results and not os.environ.get('GITHUB_ACTIONS'):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        miss_fids = []
        for r in results:
            fid_str = str(r.get('fid', ''))
            raw = stats_cache.get(fid_str)
            if raw:
                r['stats'] = _format_stats(raw)
                stats_hit[0] += 1
                stats_hit[1] += 1
            else:
                stats_hit[0] += 1
                miss_fids.append((r, fid_str))
        
        if miss_fids:
            logger.info(f"  即时抓取 {len(miss_fids)}/{stats_hit[0]} 场H2H/战绩...")
            def _fetch_one(r, fid_str):
                try:
                    stats = fetch_match_stats(fid_str)
                    if stats and (stats.get('h2h') or stats.get('home_form') or stats.get('away_form')):
                        r['stats'] = _format_stats(stats)
                        stats_cache[fid_str] = stats
                        return True
                except:
                    pass
                r['stats'] = None
                stats_cache[fid_str] = None
                return False
            
            with ThreadPoolExecutor(max_workers=5) as pool:
                futs = {pool.submit(_fetch_one, r, fid_str): fid_str for r, fid_str in miss_fids}
                done_ok = 0
                for fut in as_completed(futs):
                    if fut.result():
                        done_ok += 1
                logger.info(f"  即时抓取完成: {done_ok}/{len(miss_fids)} 场成功, 缓存已更新")
            
            # 保存回缓存文件
            try:
                os.makedirs(os.path.dirname(STATS_CACHE_PATH), exist_ok=True)
                with open(STATS_CACHE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(stats_cache, f)
            except Exception as e:
                logger.warning(f"保存统计缓存失败: {e}")
        
        if stats_hit[0] > 0:
            logger.info(f"近期战绩: {stats_hit[1]}/{stats_hit[0]} 场来自缓存 ({stats_hit[1]*100//stats_hit[0]}%)")
    else:
        # GA上跳过统计抓取，但保留旧 results.json 中已有的 stats
        try:
            old_rp = os.path.join(DOCS_DIR, 'data', 'results.json')
            if os.path.exists(old_rp):
                with open(old_rp, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
                # 兼容旧格式：旧文件用 match_stats，新文件用 stats
                old_map = {}
                for m in old_data.get('matches', []):
                    fid = str(m.get('fid', ''))
                    s = m.get('stats') or m.get('match_stats')
                    if s is not None:
                        old_map[fid] = s
                preserved = 0
                for r in results:
                    fid_str = str(r.get('fid', ''))
                    old_stats = old_map.get(fid_str)
                    if old_stats is not None:
                        r['stats'] = old_stats
                        preserved += 1
                    else:
                        r['stats'] = None
                if preserved:
                    logger.info(f"  保留 {preserved} 场旧 stats（GA 跳过统计抓取）")
            else:
                for r in results:
                    r['stats'] = None
        except Exception as e:
            logger.warning(f"读取旧 results.json 失败: {e}")
            for r in results:
                r['stats'] = None
    
    return results

# ─── 前端生成 ──────────────────────────────────────

def generate_frontend(results: List[Dict]):
    """生成docs/目录下的前端文件"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, 'data'), exist_ok=True)

    # 按日期分组
    by_date = defaultdict(list)
    for r in results:
        by_date[r['date']].append(r)

    dates = sorted(by_date.keys(), reverse=True)
    all_counts = sum(len(v) for v in by_date.values())

    # 构建输出JSON
    # 统计命中率
    hit_count = sum(1 for r in results if r.get('hit', '').find('✓') > -1)
    total_scored = hit_count + sum(1 for r in results if r.get('hit') == '✘')
    hit_rate = round(hit_count / total_scored, 3) if total_scored > 0 else 0.0

    output = {
        'generated_at': datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': all_counts,
        'date_range': f"{min(dates)} ~ {max(dates)}" if dates else '无数据',
        'hit_count': hit_count,
        'total_scored': total_scored,
        'hit_rate': hit_rate,
        'daily_stats': [],
        'matches': results,
    }
    for d in dates:
        ms = by_date[d]
        output['daily_stats'].append({
            'date': d,
            'count': len(ms),
        })

    # 写入 results.json
    rp = os.path.join(DOCS_DIR, 'data', 'results.json')
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"结果 → {rp} ({all_counts} 场)")

    # ─── index.html ──────────────────────────
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>足彩价值投注看板</title>
<link rel="stylesheet" href="style.css?v=20260716v8">
<script src="script.js?v=20260717v1"></script>
</head>
<body>
<div class="container">
  <header>
    <h1>⚽ 足彩价值投注看板</h1>
    <div class="meta">
      <span id="updateTime">加载中...</span>
      <span id="matchCount">—</span>
      <span id="dateRange">—</span>
    </div>
    <div class="controls">
      <select id="dateFilter" onchange="applyFilters()">
        <option value="all">全部日期</option>
      </select>
      <select id="sourceFilter" onchange="applyFilters()">
        <option value="all">全部来源</option>

        <option value="jingcai">竞彩</option>
      </select>
      <select id="sortBy" onchange="applyFilters()">
        <option value="time">按时间</option>
        <option value="odds">按赔率</option>
      </select>
      <button id="refreshBtn" onclick="location.reload()">🔄 刷新</button>
      <span id="warnToggle" class="warn-filter-btn" onclick="toggleWarnFilter()" title="仅显示有风险标记的比赛">⚠️ 全部</span>
      <span id="hitRate" class="meta-hit"></span>
    </div>
  </header>
  <div id="stats-bar"></div>
  <div id="loading">加载中...</div>
  <div id="table-wrap" style="display:none">
    <table id="matchTable">
      <thead>
        <tr>
          <th data-sort="time">时间</th>
          <th data-sort="league">赛事</th>
          <th>主队</th>
          <th>比分</th>
          <th>客队</th>
          <th>方向</th>
          <th>命中</th>
          <th>赔率(初/即/分歧)</th>
          <th>模型/LGBM</th>
        </tr>
      </thead>
      <tbody id="matchBody"></tbody>
    </table>
  </div>
</div>
</body>
</html>'''
    with open(os.path.join(DOCS_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)

    # ─── style.css ──────────────────────────
    css = '''*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f0f2f5;color:#1a2332;min-height:100vh}
.container{max-width:1400px;margin:0 auto;padding:16px}
header{background:linear-gradient(135deg,#2563eb,#1d4ed8);border-radius:12px;padding:20px 24px;margin-bottom:16px;color:#fff}
h1{font-size:22px;color:#fff;margin-bottom:8px}
.meta{display:flex;gap:20px;font-size:13px;color:#d0d8ff;flex-wrap:wrap}
.meta span{background:rgba(255,255,255,0.15);padding:4px 12px;border-radius:6px;color:#fff}
.controls{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.controls select,.controls button{background:#fff;color:#1a2332;border:1px solid #d0d4dc;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
.controls button{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
.controls button:hover{background:#1d4ed8}
.meta-hit{background:#fff;border:1px solid #d0d4dc;border-radius:6px;padding:6px 12px;font-size:13px;color:#2563eb;font-weight:600}
#stats-bar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.stat-card{background:#fff;border:1px solid #d0d4dc;border-radius:8px;padding:8px 14px;text-align:center;min-width:80px}
.stat-card .stat-val{font-size:18px;font-weight:700;color:#2563eb}
.stat-card .stat-label{font-size:11px;color:#667788;margin-top:2px}
#loading{text-align:center;padding:40px;color:#667788;font-size:16px}
#table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
#table-wrap::before{content:'← 左右滑动查看更多 →';display:block;text-align:center;font-size:11px;color:#8899aa;padding:4px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8f9fb;padding:8px 10px;text-align:left;font-weight:600;color:#556677;border-bottom:2px solid #d0d4dc;cursor:pointer;white-space:nowrap;user-select:none}
th:hover{color:#2563eb}
td{padding:8px 10px;border-bottom:1px solid #e8ecf0;white-space:nowrap}
tr:hover{background:#f0f6ff}
.team-name{font-weight:600;color:#1a2332}
.vs{color:#8899aa;padding:0 4px}
.odds-cell{font-variant-numeric:tabular-nums}
.lgbm-cell{font-variant-numeric:tabular-nums}
.lgbm-prob{margin-top:4px;padding-top:3px;border-top:1px solid #e0e4e8;font-size:11px;color:#2563eb;text-align:center}
.lgbm-sub{margin-top:4px;padding-top:3px;border-top:1px solid #e0e4e8;font-size:11px;color:#d97706;text-align:center}
.odds-val{display:inline-block;min-width:48px;text-align:center;padding:1px 4px;border-radius:3px;font-size:12px}
.odds-w{color:#16a34a}
.odds-d{color:#d97706}
.odds-l{color:#dc2626}
.dir-home{color:#16a34a;font-weight:700}
.dir-draw{color:#d97706;font-weight:700}
.dir-away{color:#dc2626;font-weight:700}
.dir-wait{color:#8899aa}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;background:#e8ecf0;color:#556677}
.tag-beidan{background:#dbeafe;color:#2563eb}
.tag-jingcai{background:#fef3c7;color:#d97706}
.hit-yes{color:#16a34a;font-weight:700;font-size:1.1em;text-align:center}
.hit-no{color:#dc2626;font-weight:700;font-size:1.1em;text-align:center}
.score-cell{text-align:center}
.score-cell span{display:inline-block;padding:1px 8px;border-radius:3px;font-size:12px;background:#dbeafe;color:#2563eb;font-weight:600}
.form-cell{font-size:0;padding:8px 6px;text-align:center;line-height:1.2}
.form-icons{font-size:13px;letter-spacing:1px;white-space:nowrap}
/* 合并赔率列 */
.odds-combined{font-size:11px;line-height:1.7;white-space:nowrap}
.odds-combined .oc-line{display:flex;gap:4px;align-items:center}
.odds-combined .oc-label{display:inline-block;width:14px;color:#8899aa;font-size:10px;text-align:right}
.odds-combined .oc-open{color:#8899aa}
.odds-combined .oc-cur{color:#1a2332;font-weight:500}
.odds-combined .oc-div{font-size:10px}
.odds-combined .oc-pct-down{color:#dc2626;font-weight:600}
.odds-combined .oc-pct-up{color:#16a34a;font-weight:600}
.odds-combined .oc-pct-flat{color:#8899aa}
.odds-combined .oc-sep{color:#c0c4cc;margin:0 1px}
.oc-source{font-size:10px;color:#667788;margin:2px 0 1px;font-weight:600}
.oc-source-hkjc{color:#92400e}
.oc-sep-line{height:1px;background:#e0e4e8;margin:4px 0}
.warn-badge{display:inline-block;margin-left:3px;vertical-align:middle}
.warn-badge .warn-trap{cursor:help;font-size:12px}
.warn-badge .warn-uncert{cursor:help;font-size:12px;margin-left:1px}
.warn-filter-btn{cursor:pointer;padding:3px 8px;border-radius:4px;color:#666;font-size:12px}
.warn-filter-btn.active{background:#fff3cd;color:#856404;font-weight:bold}
@media(max-width:768px){
  .container{padding:8px}
  table{font-size:11px}
  th,td{padding:5px 4px}
  .meta{flex-direction:column;gap:6px}
}'''
    with open(os.path.join(DOCS_DIR, 'style.css'), 'w', encoding='utf-8') as f:
        f.write(css)

    # ─── script.js ──────────────────────────
    js = '''// script.js — 足彩价值投注看板 v4
var allData = null;
var allMatches = [];

function fmtOdds(v){return v>0?v.toFixed(2):'-'}
function fmtPct(v){return (v*100).toFixed(1)+'%'}
function fmtPctSign(v){return v===0?'0%':(v>0?'+':'')+v.toFixed(1)+'%'}
function dirClass(d){return d==='home'?'dir-home':d==='draw'?'dir-draw':d==='away'?'dir-away':'dir-wait'}
function dirText(d){return d==='home'?'主胜':d==='draw'?'平局':d==='away'?'客胜':'观望'}
function renderWarning(w){
  if(!w)return'';
  var h='<span class="warn-badge">';
  if(w.indexOf('🚩')>-1) h+='<span class="warn-trap" title="热门降水+冷门大涨: 可能分歧陷阱">🚩</span>';
  if(w.indexOf('⚠️')>-1) h+='<span class="warn-uncert" title="模型犹豫或LGBM低置信">⚠️</span>';
  return h+'</span>';
}
function renderForm(s){
  if(!s||!s.home_recent||s.home_recent.length===0)return'';
  var hf=s.home_recent, af=s.away_recent;
  function icons(arr){
    return arr.map(function(r){
      var c=r.result;
      return c==='胜'?'🟢':c==='负'?'🔴':c==='平'?'🟡':'⚪';
    }).join('');
  }
  return '<div class=\"form-icons\"><span class=\"fi-label\">主</span>'+icons(hf)+'</div><div class=\"form-icons\"><span class=\"fi-label\">客</span>'+icons(af)+'</div>';
}
var showWarnedOnly = false;
function toggleWarnFilter(){
  showWarnedOnly = !showWarnedOnly;
  document.getElementById('warnToggle').textContent = showWarnedOnly?'⚠️ 仅标记':'⚠️ 全部';
  document.getElementById('warnToggle').className = 'warn-filter-btn'+(showWarnedOnly?' active':'');
  applyFilters();
}
function renderOdds(c, p){
  var html = '';
  // 主赔率（动态标源）
  if(c && c.current){
    var o=c.open, cur=c.current, d=c.div_pct;
    var srcLabel = c.source === 'pinnacle' ? '平博' : '马会';
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-source oc-source-hkjc">'+srcLabel+'</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  } else {
    html += '<span class="odds-val odds-w">--</span>';
  }
  // 备查列（另一家公司）
  if(p && p.current){
    var o=p.open, cur=p.current, d=p.div_pct;
    var srcLabel = p.source === 'pinnacle' ? '平博' : '马会';
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-sep-line"></div>'+
      '<div class="oc-source">'+srcLabel+'</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';

  }
  return '<div class="odds-combined">'+html+'</div>';
}
function fmtTime(t){if(!t)return'';var m=t.match(/^(?:\\d{4}-)?(\\d{2})-(\\d{2})\\s+(\\S+)$/);return m?m[1]+'/'+m[2]+' '+m[3]:t;}

function applyFilters(){
  var dateVal = document.getElementById('dateFilter').value;
  var srcVal = document.getElementById('sourceFilter').value;
  var sortVal = document.getElementById('sortBy').value;
  var filtered = allMatches.filter(function(m){
    if(dateVal!=='all' && m.date!==dateVal) return false;
    if(srcVal!=='all' && m.source!==srcVal) return false;
    if(showWarnedOnly && !m.warning) return false;
    return true;
  });
  if(sortVal==='time') filtered.sort(function(a,b){return a.match_time.localeCompare(b.match_time)});
  else if(sortVal==='odds') filtered.sort(function(a,b){return b.odds_win-a.odds_win});
  renderTable(filtered);
}

function renderTable(matches){
  var tbody = document.getElementById('matchBody');
  tbody.innerHTML = '';
  matches.forEach(function(m){
    var tr = document.createElement('tr');
    var hc=m.hit&&m.hit.indexOf('✓')>-1?'hit-yes':m.hit==='✘'?'hit-no':'';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell"><span>'+(m.score||'-')+'</span></td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span> <span style="font-size:11px;color:#999">'+dirText(m.model_prediction)+'</span>'+renderWarning(m.warning)+'</td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.pin_comparison)+'</td>'+
      '<td class="odds-cell"><div>模型: <span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></div><div style="margin-top:3px">LGBM: <span class="odds-val odds-w">'+fmtPct(m.lgbm_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.lgbm_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.lgbm_loss)+'</span><div style=\"margin-top:2px;font-size:11px\"><span class=\"oc-label\" style=\"margin-right:3px\">分</span><span class=\"'+((m.model_win-m.lgbm_win)<-0.003?'oc-pct-down':(m.model_win-m.lgbm_win)>0.003?'oc-pct-up':'oc-pct-flat')+'\">'+fmtPctSign((m.model_win-m.lgbm_win)*100)+'</span> <span class=\"'+((m.model_draw-m.lgbm_draw)<-0.003?'oc-pct-down':(m.model_draw-m.lgbm_draw)>0.003?'oc-pct-up':'oc-pct-flat')+'\">'+fmtPctSign((m.model_draw-m.lgbm_draw)*100)+'</span> <span class=\"'+((m.model_loss-m.lgbm_loss)<-0.003?'oc-pct-down':(m.model_loss-m.lgbm_loss)>0.003?'oc-pct-up':'oc-pct-flat')+'\">'+fmtPctSign((m.model_loss-m.lgbm_loss)*100)+'</span></div></div></td>';
    tbody.appendChild(tr);  });
  document.getElementById('matchCount').textContent = matches.length+' 场';
}

function renderStats(data){
  var bar = document.getElementById('stats-bar');
  bar.innerHTML = '';
  if(!data.daily_stats) return;
  data.daily_stats.forEach(function(ds){
    var card = document.createElement('div');
    card.className = 'stat-card';
    card.innerHTML = '<div class="stat-val">'+ds.count+'</div><div class="stat-label">'+ds.date+'</div>';
    bar.appendChild(card);
  });
}

// 加载
fetch('data/results.json?v='+Date.now())
  .then(function(r){return r.json()})
  .then(function(data){
    allData = data;
    allMatches = data.matches || [];
    document.getElementById('loading').style.display='none';
    document.getElementById('table-wrap').style.display='block';
    document.getElementById('updateTime').textContent = '🕐 '+data.generated_at;
    document.getElementById('dateRange').textContent = data.date_range;
    document.getElementById('matchCount').textContent = data.total_matches+' 场';
    document.getElementById('hitRate').textContent = '🎯 '+data.hit_count+'/'+data.total_scored+' ('+fmtPct(data.hit_rate)+')';
    // 填充日期过滤
    var sel = document.getElementById('dateFilter');
    (data.daily_stats||[]).forEach(function(ds){
      var opt = document.createElement('option');
      opt.value = ds.date; opt.textContent = ds.date+' ('+ds.count+')';
      sel.appendChild(opt);
    });
    // 默认选中今天
    var today = new Date().toISOString().slice(0,10);
    if (sel.querySelector('option[value="'+today+'"]')) {
      sel.value = today;
    }
    renderStats(data);
    applyFilters();
  })
  .catch(function(err){
    document.getElementById('loading').innerHTML = '❌ 数据加载失败，请刷新重试<br><small>'+err.message+'</small>';
  });
'''
    with open(os.path.join(DOCS_DIR, 'script.js'), 'w', encoding='utf-8') as f:
        f.write(js)

    logger.info(f"前端文件 → {DOCS_DIR}/")


# ─── 主流程 ──────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("🏟️  足彩智能分析系统 v3.0")
    logger.info(f"⏰  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 1. 加载原始数据
    logger.info("\n📥 [1/3] 加载原始数据...")
    matches = load_raw_matches()
    if not matches:
        logger.error("无原始数据，退出")
        return

    # 2. 分析
    logger.info(f"\n🔬 [2/3] 分析 ({len(matches)} 场)...")
    league_priors = load_league_priors()
    results = analyze_matches(matches, league_priors)

    if not results:
        logger.warning("分析后无有效结果!")
        return

    # 3. 生成前端
    logger.info(f"\n📄 [3/3] 生成前端 ({len(results)} 场)...")
    generate_frontend(results)

    # 统计
    logger.info(f"\n✅ 完成! {len(results)} 场")
    logger.info(f"   结果: docs/data/results.json")
    logger.info(f"   看板: docs/index.html")

if __name__ == '__main__':
    main()
