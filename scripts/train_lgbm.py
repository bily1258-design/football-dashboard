#!/usr/bin/env python3
"""
LGBM 模型训练脚本
从 football.db 提取27维特征，训练 SimpleLGBM，保存模型JSON
"""
import os
import sys
import json
import sqlite3
import numpy as np
from collections import defaultdict
from datetime import datetime

# 同目录 import ai_analysis（共享特征提取/模型概率计算，保证训练与推理同源）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_analysis

FORM_WINDOW = 5  # 近几场算近期战绩

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
MODEL_PATH = os.path.join(CACHE_DIR, 'lgbm_model.json')

# ─── SimpleLGBM 定义（与旧仓库一致）───────────────
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


class DecisionTree:
    def __init__(self, max_depth=5, min_samples=10):
        self.max_depth = max_depth
        self.min_samples = min_samples
        self.tree = None

    def predict(self, X):
        return np.array([self._predict_one(x, self.tree) for x in X])

    def _predict_one(self, x, node):
        if node is None:
            return 0.0
        if not isinstance(node, dict) or 'feature' not in node:
            return node if not isinstance(node, dict) else node.get('value', 0.0)
        if x[node['feature']] <= node['threshold']:
            return self._predict_one(x, node.get('left', 0.0))
        else:
            return self._predict_one(x, node.get('right', 0.0))


class SimpleLGBM:
    """纯Python LGBM，支持多分类"""
    def __init__(self, n_estimators=30, max_depth=4, learning_rate=0.1):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.trees = []  # list of list of trees, one list per class
        self.init_pred = np.zeros(3)
        self.n_features = 0
        self.feature_importance = None

    def _build_tree(self, X, residual, depth=0):
        if depth >= self.max_depth or len(X) < self.min_samples:
            return float(np.mean(residual)) if len(residual) > 0 else 0.0
        
        best_gain = -1
        best_feat = None
        best_thresh = None
        
        # Feature subsampling: only try 60% of features
        n_try = max(1, int(X.shape[1] * 0.6))
        feat_idx = np.random.choice(X.shape[1], n_try, replace=False)
        
        for f in feat_idx:
            vals = X[:, f]
            thresholds = [np.percentile(vals, p) for p in [25, 50, 75]]
            thresholds = sorted(set(thresholds))
            for th in thresholds:
                left_y = residual[vals <= th]
                right_y = residual[vals > th]
                if len(left_y) < self.min_samples or len(right_y) < self.min_samples:
                    continue
                # Variance reduction (最大化gain = 最小化残差)
                imp = np.var(residual) - (len(left_y)/len(residual)*np.var(left_y) + len(right_y)/len(residual)*np.var(right_y))
                if imp > best_gain:
                    best_gain = imp
                    best_feat = f
                    best_thresh = th
        
        if best_feat is None:
            return float(np.mean(residual)) if len(residual) > 0 else 0.0
        
        left_idx = X[:, best_feat] <= best_thresh
        right_idx = X[:, best_feat] > best_thresh
        
        return {
            'feature': int(best_feat),
            'threshold': float(best_thresh),
            'left': self._build_tree(X[left_idx], residual[left_idx], depth+1),
            'right': self._build_tree(X[right_idx], residual[right_idx], depth+1),
        }

    def fit(self, X, y):
        self.n_features = X.shape[1]
        n_classes = len(np.unique(y))
        
        # One-vs-rest for multi-class
        self.trees = []
        for cls in range(3):
            binary_y = (y == cls).astype(float)
            init_pred = np.mean(binary_y)
            self.init_pred[cls] = init_pred
            residual = binary_y - init_pred
            
            cls_trees = []
            for i in range(self.n_estimators):
                tree = DecisionTree(max_depth=self.max_depth, min_samples=self.min_samples)
                tree.tree = self._build_tree(X, residual)
                if isinstance(tree.tree, dict):
                    pred = tree.predict(X)
                    residual -= self.learning_rate * pred
                    cls_trees.append(tree)
                # If tree is just a leaf, skip it
            self.trees.append(cls_trees)
    
    def predict_proba(self, X):
        if not self.trees:
            return np.full((len(X), 3), 1.0 / 3)
        n = len(X)
        raw = np.zeros((n, 3))
        for cls in range(3):
            pred = np.full(n, self.init_pred[cls])
            for tree in self.trees[cls]:
                pred += self.learning_rate * tree.predict(X)
            raw[:, cls] = pred
        exp_raw = np.exp(raw - np.max(raw, axis=1, keepdims=True))
        return exp_raw / np.sum(exp_raw, axis=1, keepdims=True)

    def to_dict(self):
        trees_data = []
        for cls_trees in self.trees:
            cls_data = []
            for t in cls_trees:
                cls_data.append(t.tree)
            trees_data.append(cls_data)
        return {
            'n_estimators': self.n_estimators,
            'max_depth': self.max_depth,
            'learning_rate': self.learning_rate,
            'min_samples': self.min_samples,
            'init_pred': self.init_pred.tolist(),
            'n_features': self.n_features,
            'trees': trees_data,
        }

    @classmethod
    def from_dict(cls, d):
        model = cls(d.get('n_estimators', 80), d.get('max_depth', 5), d.get('learning_rate', 0.08))
        model.min_samples = d.get('min_samples', 10)
        model.init_pred = np.array(d['init_pred'])
        model.n_features = d.get('n_features', 0)
        model.trees = []
        for cls_trees_data in d.get('trees', []):
            cls_trees = []
            for tree_data in cls_trees_data:
                t = DecisionTree()
                t.tree = tree_data
                cls_trees.append(t)
            model.trees.append(cls_trees)
        return model


# ─── 特征提取 ──────────────────────────────────────

def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _implied(odds_w, odds_d, odds_l):
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return (0,)*4
    inv = (1.0/odds_w, 1.0/odds_d, 1.0/odds_l)
    t = sum(inv)
    return (inv[0]/t, inv[1]/t, inv[2]/t, t-1.0)

# 特征名与 ai_analysis 共享（避免双份定义漂移）
FEATURE_NAMES = ai_analysis.FEATURE_NAMES

def extract_features(row, stats=None, form_data=None, conn=None,
                     league_priors=None, lambda_=None):
    """从DB行提取39维LGBM特征。

    与 ai_analysis.extract_lgbm_features / calc_model_probs 同源：
    - 赔率源: pinnacle_close 优先（推理端 ow 同源），fallback odds_win
    - final_* 特征 = calc_model_probs（推理端同款平滑+联赛混合）
    - poisson_* 恒0（推理端JSON无poisson字段，保持两侧一致）
    """
    if lambda_ is None:
        lambda_ = ai_analysis.LEAGUE_PRIOR_LAMBDA

    # 赔率源
    ow = _safe_float(row.get('pinnacle_close_w'))
    od = _safe_float(row.get('pinnacle_close_d'))
    ol = _safe_float(row.get('pinnacle_close_l'))
    if not (ow > 1.01 and od > 1.01 and ol > 1.01):
        ow = _safe_float(row.get('odds_win'))
        od = _safe_float(row.get('odds_draw'))
        ol = _safe_float(row.get('odds_loss'))

    # final_* 特征（与推理端 analyze_matches 同款逻辑）
    league = row.get('league', '') or ''
    mw, md, ml, _ = ai_analysis.calc_model_probs(ow, od, ol, league_priors, lambda_, league)

    # 开盘价（缺失传 None → 特征0，与推理端一致）
    pin_ow = _safe_float(row.get('pinnacle_open_w'))
    pin_od = _safe_float(row.get('pinnacle_open_d'))
    pin_ol = _safe_float(row.get('pinnacle_open_l'))
    open_w = pin_ow if pin_ow > 1.01 else None
    open_d = pin_od if pin_od > 1.01 else None
    open_l = pin_ol if pin_ol > 1.01 else None

    # 近期战绩
    ft_home = form_data.get(row.get('home_team', ''), {}) if form_data else {}
    ft_away = form_data.get(row.get('away_team', ''), {}) if form_data else {}

    # xG特征 (xg_features 表)
    h_g3 = a_g3 = h_c3 = a_c3 = 0.0
    xg_h3 = xg_a3 = xg_h10 = xg_a10 = 0.0
    sid_raw = row.get('sid') or row.get('match_id') or 0
    try:
        sid_int = int(sid_raw) if sid_raw else 0
    except (ValueError, TypeError):
        sid_int = 0
    if conn and sid_int:
        cur = conn.execute(
            'SELECT home_goals_3, away_goals_3, home_conceded_3, away_conceded_3, '
            'xg_home_3, xg_away_3, xg_home_10, xg_away_10 FROM xg_features WHERE sid = ?',
            (sid_int,)
        )
        r = cur.fetchone()
        if r:
            h_g3 = _safe_float(r[0]); a_g3 = _safe_float(r[1])
            h_c3 = _safe_float(r[2]); a_c3 = _safe_float(r[3])
            xg_h3 = _safe_float(r[4]); xg_a3 = _safe_float(r[5])
            xg_h10 = _safe_float(r[6]); xg_a10 = _safe_float(r[7])

    return ai_analysis.extract_lgbm_features(
        ow, od, ol, mw, md, ml, 0.0,
        open_w=open_w, open_d=open_d, open_l=open_l,
        # poisson_*: 推理端JSON无此字段→恒0，训练端同源（DB有值也不喂，避免skew）
        poisson_w=None, poisson_d=None, poisson_l=None,
        # 排名（DB home_ranking 列，纯数字字符串）
        home_rank=row.get('home_ranking'), away_rank=row.get('away_ranking'),
        # 积分: DB无points列，推理端JSON也无 → 恒0 两侧一致
        home_pts=None, away_pts=None,
        home_form_pts=ft_home.get('form_pts'), away_form_pts=ft_away.get('form_pts'),
        home_form_gd=ft_home.get('form_gd'), away_form_gd=ft_away.get('form_gd'),
        home_goals_3=h_g3 or None, away_goals_3=a_g3 or None,
        home_conceded_3=h_c3 or None, away_conceded_3=a_c3 or None,
        xg_home_3=xg_h3 or None, xg_away_3=xg_a3 or None,
        xg_home_10=xg_h10 or None, xg_away_10=xg_a10 or None,
    )


def get_result_label(reference_score):
    """从比分提取结果标签: 0=主胜, 1=平局, 2=客胜"""
    if not reference_score or '-' not in reference_score:
        return None
    parts = reference_score.split('-')
    try:
        h, a = int(parts[0]), int(parts[1])
    except:
        return None
    if h > a: return 0
    if h == a: return 1
    return 2


def build_team_form_map(rows):
    """从DB记录构建各队近期战绩时间线
    返回: {team: [(date, gf, ga, is_home), ...]} 按日期排序
    """
    timeline = defaultdict(list)
    for row in rows:
        r = dict(row)
        score = r.get('reference_score', '')
        if '-' not in score:
            continue
        parts = score.split('-')
        try:
            gh, ga = int(parts[0]), int(parts[1])
        except:
            continue
        date = r.get('date', '')
        ht = r.get('home_team', '')
        at = r.get('away_team', '')
        timeline[ht].append((date, gh, ga, True))
        timeline[at].append((date, ga, gh, False))
    for team in timeline:
        timeline[team].sort(key=lambda x: x[0])
    return timeline


def get_team_form(timeline, team, date, window=FORM_WINDOW):
    """查某个队在某日期前的近期战绩"""
    matches = timeline.get(team, [])
    recent = [m for m in matches if m[0] < date][-window:]
    if not recent:
        return {'form_pts': 0.0, 'form_gd': 0.0}
    pts = 0
    gd = 0
    for m in recent:
        gf, ga = m[1], m[2]
        gd += gf - ga
        if gf > ga:
            pts += 3
        elif gf == ga:
            pts += 1
    return {
        'form_pts': round(pts / len(recent), 2),
        'form_gd': round(gd / len(recent), 2),
    }


# ─── 主流程 ──────────────────────────────────────

def main():
    print("=" * 60)
    print("LGBM 模型训练")
    print("=" * 60)
    
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] DB不存在: {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 联赛基准率（与 ai_analysis 推理端同源，用于 calc_model_probs）
    league_priors = ai_analysis.load_league_priors(DB_PATH)
    print(f"联赛基准率: {len(league_priors)} 个联赛")
    
    # 加载数据（全部带比分，用于构建近期战绩时间线）
    cur = conn.execute("""
        SELECT * FROM poisson_predictions
        WHERE reference_score IS NOT NULL AND reference_score != ''
        ORDER BY date
    """)
    all_scored_rows = cur.fetchall()
    
    # 构建各队近期战绩时间线
    print(f"构建近期战绩时间线 ({len(all_scored_rows)} 场有比分)...")
    form_timeline = build_team_form_map(all_scored_rows)
    print(f"  覆盖 {len(form_timeline)} 支队伍")
    
    # 加载训练数据（有赔率的）
    cur = conn.execute("""
        SELECT * FROM poisson_predictions
        WHERE pinnacle_close_w > 1.01 
          AND reference_score IS NOT NULL AND reference_score != ''
        ORDER BY date
    """)
    rows = cur.fetchall()
    
    print(f"\n总记录: {len(rows)}")
    
    # 提取特征 + 标签
    X_list = []
    y_list = []
    skip_reasons = defaultdict(int)
    
    for row in rows:
        r = dict(row)
        label = get_result_label(r.get('reference_score', ''))
        if label is None:
            skip_reasons['无有效比分'] += 1
            continue
        # 计算该场赛事的近期战绩（仅使用该赛事之前的比赛）
        date = r.get('date', '')
        home_team = r.get('home_team', '')
        away_team = r.get('away_team', '')
        form_data = {
            home_team: get_team_form(form_timeline, home_team, date),
            away_team: get_team_form(form_timeline, away_team, date),
        }
        feats = extract_features(r, form_data=form_data, conn=conn, league_priors=league_priors)
        X_list.append(feats)
        y_list.append(label)
    conn.close()
    
    X = np.array(X_list, dtype=float)
    y = np.array(y_list)
    
    # 处理NaN
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    
    print(f"有效样本: {len(X)}")
    for reason, cnt in sorted(skip_reasons.items()):
        print(f"  跳过: {reason} x{cnt}")
    
    print(f"\n赛果分布: 主胜={sum(y==0)}, 平局={sum(y==1)}, 客胜={sum(y==2)}")
    
    # 时间分割: 前80%训练, 后20%测试
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    
    print(f"\n训练集: {len(X_train)} 场 (前80%)")
    print(f"测试集: {len(X_test)} 场 (后20%)")
    
    # 训练
    # 允许命令行指定树数
    n_trees = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 120
    print(f"\n训练 SimpleLGBM ({n_trees}棵树, 深度5, lr0.05)...")
    np.random.seed(42)  # 固定随机种子，保证可复现（2026-08-19 修复）
    model = SimpleLGBM(n_estimators=n_trees, max_depth=5, learning_rate=0.05)
    model.min_samples = 10
    model.fit(X_train, y_train)
    
    # 评估
    train_pred = model.predict_proba(X_train)
    test_pred = model.predict_proba(X_test)
    
    train_acc = np.mean(np.argmax(train_pred, axis=1) == y_train)
    test_acc = np.mean(np.argmax(test_pred, axis=1) == y_test)
    
    print(f"\n训练准确率: {train_acc:.1%}")
    print(f"测试准确率: {test_acc:.1%}")
    
    # 计算命中率（vs 默认方法：直接用Pinnacle隐含概率猜）
    # Pinnacle close implied as baseline
    baseline_correct = 0
    for i, row in enumerate(rows[split:]):
        r = dict(row)
        pin_w = _safe_float(r.get('pinnacle_close_w'), 1.0)
        pin_d = _safe_float(r.get('pinnacle_close_d'), 1.0)
        pin_l = _safe_float(r.get('pinnacle_close_l'), 1.0)
        imp = _implied(pin_w, pin_d, pin_l)
        if imp[0] > 0:
            pred = np.argmax(imp[:3])
            if pred == y[split + i]:
                baseline_correct += 1
    
    baseline_acc = baseline_correct / len(X_test) if len(X_test) > 0 else 0
    print(f"\n基准准确率 (Pinnacle隐含): {baseline_acc:.1%}")
    print(f"LGBM提升: {(test_acc - baseline_acc)*100:+.1f}%")
    
    # 混淆矩阵
    y_pred = np.argmax(test_pred, axis=1)
    print("\n混淆矩阵 (测试集):")
    print(f"{'':>12} {'主胜':>6} {'平局':>6} {'客胜':>6}")
    labels_map = {0: '主胜', 1: '平局', 2: '客胜'}
    for true_label in range(3):
        row = y_pred[y_test == true_label]
        counts = [np.sum(row == p) for p in range(3)]
        print(f"{labels_map[true_label]:>8}  {counts[0]:>6} {counts[1]:>6} {counts[2]:>6}")
    
    # 保存模型
    os.makedirs(CACHE_DIR, exist_ok=True)
    model_dict = model.to_dict()
    model_dict['feature_names'] = FEATURE_NAMES
    model_dict['version'] = 11
    model_dict['feature_sync'] = 'shared extract_lgbm_features + calc_model_probs (2026-08-19)'
    model_dict['seed'] = 42
    model_dict['train_date'] = datetime.now().strftime('%Y-%m-%d')
    model_dict['train_samples'] = len(X_train)
    model_dict['test_accuracy'] = round(test_acc, 4)
    model_dict['baseline_accuracy'] = round(baseline_acc, 4)
    model_dict['n_trees'] = n_trees
    
    with open(MODEL_PATH, 'w') as f:
        json.dump(model_dict, f, cls=NumpyEncoder)
    
    print(f"\n✅ 模型已保存: {MODEL_PATH}")
    print(f"   特征数: {model.n_features}")
    print(f"   树数: {sum(len(t) for t in model.trees)}")

if __name__ == '__main__':
    main()
