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
import re, json, os, sys, math, sqlite3, glob, logging, hashlib
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

# ─── 算法常量 ──────────────────────────────────────
SMOOTH_ALPHA_BASE = 0.02      # 贝叶斯平滑基值（均衡比赛用）
SMOOTH_ALPHA_SKEW = 0.35      # 方差自适应系数：偏离均衡每0.1加权0.035
HOME_ADJ = 0.01               # 主场调整量：加到模型主胜，从平/负各扣0.003
LEAGUE_PRIOR_LAMBDA = 0.15    # 联赛基准率混合权重（0=不使用，0.15=15%基准+85%市场）
LEAGUE_PRIOR_MIN_MATCHES = 10  # 联赛基准最小样本量


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

def determine_direction(mw: float, md: float, ml: float) -> Tuple[str, str, float]:
    """确定推荐方向（取模型概率最大的方向）"""
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
    return deduped

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
                           lambda_h=None, lambda_a=None):
    """从当前比赛数据提取28维特征（尽可能填充，缺失填0）"""
    # 当前赔率隐含概率
    cw, cd, cl, _ = _implied_from_odds(ow, od, ol)
    
    # 开盘隐含概率
    if open_w and open_w > 1 and open_d and open_d > 1 and open_l and open_l > 1:
        pw, pd, pl, pin_margin = _implied_from_odds(open_w, open_d, open_l)
    else:
        pw, pd, pl, pin_margin = 0.0, 0.0, 0.0, 0.0
    
    # 赔率变动
    move_w = cw - pw if pw > 0 else 0.0
    move_d = cd - pd if pd > 0 else 0.0
    move_l = cl - pl if pl > 0 else 0.0
    diff_w = move_w / pw if pw > 0 else cw  # 变动幅度
    diff_d = move_d / pd if pd > 0 else cd
    diff_l = move_l / pl if pl > 0 else cl
    
    # 泊松概率（训练时有，推理时可能缺失）
    p_w = float(poisson_w) if poisson_w and float(poisson_w) > 0 else 0.0
    p_d = float(poisson_d) if poisson_d and float(poisson_d) > 0 else 0.0
    p_l = float(poisson_l) if poisson_l and float(poisson_l) > 0 else 0.0
    
    # 隐含概率（如有传入，否则用当前赔率隐含）
    iw = float(implied_w) if implied_w and float(implied_w) > 0 else cw
    id_ = float(implied_d) if implied_d and float(implied_d) > 0 else cd
    il = float(implied_l) if implied_l and float(implied_l) > 0 else cl
    
    # 分歧度：泊松 vs 市场
    dw = p_w - iw if p_w > 0 else 0.0
    dd = p_d - id_ if p_d > 0 else 0.0
    dl = p_l - il if p_l > 0 else 0.0
    
    # 泊松 vs 市场差异
    poisson_market_margin = abs(p_w - iw) + abs(p_d - id_) + abs(p_l - il) if p_w > 0 else 0.0
    poisson_market_draw_diff = p_d - id_ if p_d > 0 else 0.0
    
    # 赔率级别
    odds_level = 1.0 / max(ow, 1.01)
    
    # 平局溢价
    draw_premium = ((od - (ow + ol)/2) / max((ow + ol)/2, 0.01)) if ow > 1.01 and ol > 1.01 else 0.0
    
    # λ值
    lh = float(lambda_h) if lambda_h and float(lambda_h) > 0 else 0.0
    la = float(lambda_a) if lambda_a and float(lambda_a) > 0 else 0.0
    
    return [
        p_w, p_d, p_l,                    # poisson_w/d/l
        model_w, model_d, model_l,         # final_w/d/l
        iw, id_, il,                      # implied_w/d/l
        pw, pd, pl,                       # pin_open_w/d/l
        cw, cd, cl,                       # pin_close_w/d/l
        move_w, move_d, move_l,           # pin_move_w/d/l
        diff_w, diff_d, diff_l,           # pin_diff_w/d/l
        pin_margin,                       # pin_margin
        dw, dd, dl,                       # disagree_w/d/l
        poisson_market_margin, poisson_market_draw_diff,
        odds_level, draw_premium,
        lh, la,                           # lambda_h/a
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


# ─── 分析引擎 ──────────────────────────────────────

def analyze_matches(matches: List[Dict], league_priors: Dict[str, Tuple[float, float, float]] = None) -> List[Dict]:
    """对比赛列表执行EV/泊松分析"""
    results = []
    skipped = 0
    if league_priors is None:
        league_priors = {}
    lambda_ = LEAGUE_PRIOR_LAMBDA

    for m in matches:
        # 赔率源：优先平博，fallback到HKJC
        ow = float(m.get('odds_pinnacle_win', 0) or 0)
        od = float(m.get('odds_pinnacle_draw', 0) or 0)
        ol = float(m.get('odds_pinnacle_loss', 0) or 0)
        odds_source = 'pinnacle'

        if not (ow > 1 and od > 1 and ol > 1):
            # 尝试用HKJC赔率
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

        # 3. LGBM 方案C预测
        lgbm_model = load_lgbm_model()
        lgbm_w, lgbm_d, lgbm_l = model_w, model_d, model_l  # 默认fallback
        if lgbm_model:
            # 从比赛数据提取特征（初盘/泊松等如有则用）
            # 按赔率源选择开盘价
            if odds_source == 'hkjc':
                open_w = m.get('odds_hkjc_open_win') or m.get('odds_hkjc_win')
                open_d = m.get('odds_hkjc_open_draw') or m.get('odds_hkjc_draw')
                open_l = m.get('odds_hkjc_open_loss') or m.get('odds_hkjc_loss')
            else:
                open_w = m.get('odds_pinnacle_open_win') or m.get('pinnacle_open_w')
                open_d = m.get('odds_pinnacle_open_draw') or m.get('pinnacle_open_d')
                open_l = m.get('odds_pinnacle_open_loss') or m.get('pinnacle_open_l')
            feat = extract_lgbm_features(ow, od, ol, model_w, model_d, model_l, margin,
                                          open_w=open_w, open_d=open_d, open_l=open_l,
                                          poisson_w=m.get('poisson_win'), poisson_d=m.get('poisson_draw'), poisson_l=m.get('poisson_loss'),
                                          implied_w=m.get('implied_prob_w'), implied_d=m.get('implied_prob_d'), implied_l=m.get('implied_prob_l'),
                                          lambda_h=m.get('home_lambda'), lambda_a=m.get('away_lambda'))
            proba = predict_lgbm(lgbm_model, feat)
            if proba:
                lgbm_w, lgbm_d, lgbm_l = proba

        # 4. 推荐方向（取最大模型概率）
        dir_cn, dir_en, dir_prob = determine_direction(model_w, model_d, model_l)
        # LGBM方向
        lgbm_dir_cn, lgbm_dir_en, lgbm_dir_prob = determine_direction(lgbm_w, lgbm_d, lgbm_l)

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
        hit = '✅' if actual and actual == dir_en else ('❌' if actual else '')

        # 8. 赔率对比数据（平博开盘 vs 平博即时）
        comparison = {}
        if ow > 1 and od > 1 and ol > 1:
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
                }

        # 8b. 香港马会赔率对比
        hkjc_comparison = {}
        hw = float(m.get('odds_hkjc_win', 0) or 0)
        hd = float(m.get('odds_hkjc_draw', 0) or 0)
        hl = float(m.get('odds_hkjc_loss', 0) or 0)
        if hw > 1 and hd > 1 and hl > 1:
            hop_w = float(m.get('odds_hkjc_open_win', 0) or 0)
            hop_d = float(m.get('odds_hkjc_open_draw', 0) or 0)
            hop_l = float(m.get('odds_hkjc_open_loss', 0) or 0)
            if hop_w > 1 and hop_d > 1 and hop_l > 1:
                pct_w = (hw - hop_w) / hop_w * 100
                pct_d = (hd - hop_d) / hop_d * 100
                pct_l = (hl - hop_l) / hop_l * 100
                hkjc_comparison = {
                    'open': [round(hop_w,2), round(hop_d,2), round(hop_l,2)],
                    'current': [round(hw,2), round(hd,2), round(hl,2)],
                    'div_pct': [round(pct_w,1), round(pct_d,1), round(pct_l,1)],
                }

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
            'prediction': dir_en,
            'prediction_cn': dir_cn,
            'prediction_prob': round(max_prob_val, 4),
            'lgbm_prediction': lgbm_dir_en,
            'lgbm_prediction_cn': lgbm_dir_cn,
            'lgbm_prediction_prob': round(lgbm_dir_prob, 4),
            'lgbm_win': round(lgbm_w, 4),
            'lgbm_draw': round(lgbm_d, 4),
            'lgbm_loss': round(lgbm_l, 4),
            'comparison': comparison,
            'hkjc_comparison': hkjc_comparison,
            'league_baseline': league_baseline,
        })

    logger.info(f"分析完成: {len(results)} 场 (跳过 {skipped} 场无赔率)")
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
    hit_count = sum(1 for r in results if r.get('hit') == '✅')
    total_scored = hit_count + sum(1 for r in results if r.get('hit') == '❌')
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
<title>足彩价值投注看板</title>
<link rel="stylesheet" href="style.css?v=20260715v1">
<script src="script.js?v=20260715v1"></script>
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
          <th>模型(W/D/L)</th>
          <th>LGBM</th>
          <th>指数</th>
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
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f1923;color:#e0e6ed;min-height:100vh}
.container{max-width:1400px;margin:0 auto;padding:16px}
header{background:linear-gradient(135deg,#1a2a3a,#0d1b2a);border-radius:12px;padding:20px 24px;margin-bottom:16px;border:1px solid #2a3a4a}
h1{font-size:22px;color:#66b8ff;margin-bottom:8px}
.meta{display:flex;gap:20px;font-size:13px;color:#8899aa;flex-wrap:wrap}
.meta span{background:#1a2a3a;padding:4px 12px;border-radius:6px}
.controls{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.controls select,.controls button{background:#1a2a3a;color:#e0e6ed;border:1px solid #2a3a4a;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
.controls button{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
.controls button:hover{background:#1d4ed8}
.meta-hit{background:#1a2a3a;border:1px solid #2a3a4a;border-radius:6px;padding:6px 12px;font-size:13px;color:#66b8ff;font-weight:600}
#stats-bar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.stat-card{background:#1a2a3a;border:1px solid #2a3a4a;border-radius:8px;padding:8px 14px;text-align:center;min-width:80px}
.stat-card .stat-val{font-size:18px;font-weight:700;color:#66b8ff}
.stat-card .stat-label{font-size:11px;color:#667788;margin-top:2px}
#loading{text-align:center;padding:40px;color:#667788;font-size:16px}
#table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
#table-wrap::before{content:'← 左右滑动查看更多 →';display:block;text-align:center;font-size:11px;color:#556677;padding:4px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#1a2a3a;padding:8px 10px;text-align:left;font-weight:600;color:#8899aa;border-bottom:2px solid #2a3a4a;cursor:pointer;white-space:nowrap;user-select:none}
th:hover{color:#66b8ff}
td{padding:8px 10px;border-bottom:1px solid #1a2a3a;white-space:nowrap}
tr:hover{background:#1a2a3a}
.team-name{font-weight:600;color:#e0e6ed}
.vs{color:#667788;padding:0 4px}
.odds-cell{font-variant-numeric:tabular-nums}
.prob-cell{font-variant-numeric:tabular-nums;font-weight:600;color:#66b8ff;text-align:center;min-width:52px}
.odds-val{display:inline-block;min-width:48px;text-align:center;padding:1px 4px;border-radius:3px;font-size:12px}
.odds-w{color:#4ade80}
.odds-d{color:#fbbf24}
.odds-l{color:#f87171}
.dir-home{color:#4ade80;font-weight:700}
.dir-draw{color:#fbbf24;font-weight:700}
.dir-away{color:#f87171;font-weight:700}
.dir-wait{color:#667788}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;background:#2a3a4a;color:#8899aa}
.tag-beidan{background:#1e3a5f;color:#60a5fa}
.tag-jingcai{background:#3a2a1e;color:#fbbf24}
.hit-yes{color:#4ade80;font-weight:700;font-size:1.1em;text-align:center}
.hit-no{color:#f87171;font-weight:700;font-size:1.1em;text-align:center}
.score-cell{text-align:center;font-weight:500}
/* 合并赔率列 */
.odds-combined{font-size:11px;line-height:1.7;white-space:nowrap}
.odds-combined .oc-line{display:flex;gap:4px;align-items:center}
.odds-combined .oc-label{display:inline-block;width:14px;color:#8899aa;font-size:10px;text-align:right}
.odds-combined .oc-open{color:#667788}
.odds-combined .oc-cur{color:#e0e8f0;font-weight:500}
.odds-combined .oc-div{font-size:10px}
.odds-combined .oc-pct-down{color:#f87171;font-weight:600}
.odds-combined .oc-pct-up{color:#4ade80;font-weight:600}
.odds-combined .oc-pct-flat{color:#556677}
.odds-combined .oc-sep{color:#445566;margin:0 1px}
.oc-source{font-size:10px;color:#7899bb;margin:2px 0 1px;font-weight:600}
.oc-source-hkjc{color:#bb9977}
.oc-sep-line{height:1px;background:#2a3a4a;margin:4px 0}
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
function renderOdds(c, h){
  var html = '';
  // 平博
  if(c && c.current){
    var o=c.open, cur=c.current, d=c.div_pct;
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-source">平博</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  } else {
    html += '<span class="odds-val odds-w">--</span>';
  }
  // 香港马会
  if(h && h.current){
    var o=h.open, cur=h.current, d=h.div_pct;
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-sep-line"></div>'+
      '<div class="oc-source oc-source-hkjc">马会</div>'+
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
    var hc=m.hit==='\\u2705'?'hit-yes':m.hit==='\\u274c'?'hit-no':'';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.hkjc_comparison)+'</td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></td>'+
      '<td class="lgbm-cell"><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span></td>';
    tbody.appendChild(tr);
  });
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
