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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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

FEATURE_NAMES = [
    'poisson_w','poisson_d','poisson_l',
    'final_w','final_d','final_l',
    'implied_w','implied_d','implied_l',
    'pin_open_w','pin_open_d','pin_open_l',
    'pin_close_w','pin_close_d','pin_close_l',
    'pin_diff_w','pin_diff_d','pin_diff_l',
    'pin_margin',
    'poisson_market_margin','poisson_market_draw_diff',
    'odds_level','draw_premium',
    'home_rank','away_rank','home_pts','away_pts',
    'home_form_pts','away_form_pts','home_form_gd','away_form_gd',
    # 盘路/技术统计特征 (v8, 12维)
    'home_handicap_wr','away_handicap_wr',
    'home_over_rate','away_over_rate',
    'home_possession','away_possession',
    'home_shots','away_shots',
    'home_shots_on_target','away_shots_on_target',
    'home_corners','away_corners',
]

def extract_features(row, stats=None, form_data=None, conn=None):
    pw = _safe_float(row.get('poisson_win'))
    pd_ = _safe_float(row.get('poisson_draw'))
    pl = _safe_float(row.get('poisson_loss'))
    
    fw = _safe_float(row.get('fusion_win'))
    fd_ = _safe_float(row.get('fusion_draw'))
    fl = _safe_float(row.get('fusion_loss'))
    
    imp_w = _safe_float(row.get('implied_prob_w'))
    imp_d_ = _safe_float(row.get('implied_prob_d'))
    imp_l = _safe_float(row.get('implied_prob_l'))
    
    pin_ow = _safe_float(row.get('pinnacle_open_w'))
    pin_od = _safe_float(row.get('pinnacle_open_d'))
    pin_ol = _safe_float(row.get('pinnacle_open_l'))
    has_open = pin_ow > 1.01 and pin_od > 1.01 and pin_ol > 1.01
    if has_open:
        open_w, open_d, open_l, _ = _implied(pin_ow, pin_od, pin_ol)
    else:
        open_w = open_d = open_l = 0.0
    
    pin_cw = _safe_float(row.get('pinnacle_close_w'))
    pin_cd = _safe_float(row.get('pinnacle_close_d'))
    pin_cl = _safe_float(row.get('pinnacle_close_l'))
    has_close = pin_cw > 1.01 and pin_cd > 1.01 and pin_cl > 1.01
    if has_close:
        close_w, close_d, close_l, _ = _implied(pin_cw, pin_cd, pin_cl)
    else:
        close_w = close_d = close_l = 0.0
    
    diff_w = (close_w - open_w) if has_open and has_close else 0.0
    diff_d = (close_d - open_d) if has_open and has_close else 0.0
    diff_l = (close_l - open_l) if has_open and has_close else 0.0
    
    pin_margin = _safe_float(row.get('pinnacle_margin'))
    
    poisson_market_margin = (pw - pl) - (imp_w - imp_l) if imp_w > 0 else 0.0
    poisson_market_draw_diff = pd_ - imp_d_ if imp_d_ > 0 else 0.0
    
    odds_w = _safe_float(row.get('odds_win'))
    odds_d = _safe_float(row.get('odds_draw'))
    odds_l = _safe_float(row.get('odds_loss'))
    odds_level = 1.0 / max(odds_w, 1.01) if odds_w > 1.01 else 0.0
    draw_premium = ((odds_d - (odds_w + odds_l)/2) / max((odds_w + odds_l)/2, 0.01)
                    if odds_w > 1.01 and odds_l > 1.01 else 0.0)
    
    # 新增积分特征
    hr = _safe_float(row.get('home_ranking'))
    ar = _safe_float(row.get('away_ranking'))
    hp = _safe_float(row.get('home_points'))
    ap = _safe_float(row.get('away_points'))
    
    # 近期战绩特征
    if form_data:
        ft_home = form_data.get(row.get('home_team', ''), {})
        ft_away = form_data.get(row.get('away_team', ''), {})
        hfp = _safe_float(ft_home.get('form_pts'))
        afp = _safe_float(ft_away.get('form_pts'))
        hfg = _safe_float(ft_home.get('form_gd'))
        afg = _safe_float(ft_away.get('form_gd'))
    else:
        hfp = afp = hfg = afg = 0.0
    
    # ─── 新增盘路/技术统计特征 (v8) ───────────────────
    h_hw = a_hw = h_or = a_or = 0.0
    h_poss = a_poss = h_shots = a_shots = 0.0
    h_sot = a_sot = h_corners = a_corners = 0.0
    
    if conn:
        home_team = row.get('home_team', '')
        away_team = row.get('away_team', '')
        
        # 盘路特征: team_stats_cache (by team_name, stat_type=home_all/away_all)
        cur = conn.execute(
            'SELECT handicap_win_rate, over_rate FROM team_stats_cache '
            'WHERE team_name = ? AND stat_type = ?',
            (home_team, 'home_all')
        )
        r = cur.fetchone()
        if r:
            h_hw = _safe_float(r[0]) / 100.0  # DB存的是0-100，转为0-1
            h_or = _safe_float(r[1]) / 100.0
        
        cur = conn.execute(
            'SELECT handicap_win_rate, over_rate FROM team_stats_cache '
            'WHERE team_name = ? AND stat_type = ?',
            (away_team, 'away_all')
        )
        r = cur.fetchone()
        if r:
            a_hw = _safe_float(r[0]) / 100.0
            a_or = _safe_float(r[1]) / 100.0
        
        # 技术统计: match_analysis (by sid)
        sid = row.get('match_id', '')
        if sid:
            try:
                sid_int = int(sid)
            except (ValueError, TypeError):
                sid_int = None
            if sid_int:
                cur = conn.execute(
                    'SELECT home_tech_stats, away_tech_stats FROM match_analysis WHERE sid = ?',
                    (sid_int,)
                )
                r = cur.fetchone()
                if r and r[0] and r[1]:
                    try:
                        h_tech = json.loads(r[0]) if r[0] not in ('', '{}') else {}
                        a_tech = json.loads(r[1]) if r[1] not in ('', '{}') else {}
                    except (json.JSONDecodeError, TypeError):
                        h_tech = a_tech = {}
                    h_poss = _safe_float(h_tech.get('控球率', 0)) / 100.0
                    a_poss = _safe_float(a_tech.get('控球率', 0)) / 100.0
                    h_shots = _safe_float(h_tech.get('射门', 0))
                    a_shots = _safe_float(a_tech.get('射门', 0))
                    h_sot = _safe_float(h_tech.get('射正', 0))
                    a_sot = _safe_float(a_tech.get('射正', 0))
                    # 角球: 部分表用"角球"，部分在tech_stats里没有独立字段，保持0
                    # 从stats_json中解析（如果有）
                    h_corners = _safe_float(h_tech.get('角球', 0))
                    a_corners = _safe_float(a_tech.get('角球', 0))
    
    return [
        pw, pd_, pl,
        fw, fd_, fl,
        imp_w, imp_d_, imp_l,
        open_w, open_d, open_l,
        close_w, close_d, close_l,
        diff_w, diff_d, diff_l,
        pin_margin,
        poisson_market_margin, poisson_market_draw_diff,
        odds_level, draw_premium,
        hr, ar, hp, ap,
        hfp, afp, hfg, afg,
        # 盘路/技术统计 12维
        h_hw, a_hw, h_or, a_or,
        h_poss, a_poss,
        h_shots, a_shots,
        h_sot, a_sot,
        h_corners, a_corners,
    ]


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
        feats = extract_features(r, form_data=form_data, conn=conn)
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
    print("\n训练 SimpleLGBM (30棵树, 深度4)...")
    model = SimpleLGBM(n_estimators=30, max_depth=4, learning_rate=0.1)
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
    model_dict['version'] = 8
    model_dict['train_date'] = '2026-07-25'
    model_dict['train_samples'] = len(X_train)
    model_dict['test_accuracy'] = round(test_acc, 4)
    model_dict['baseline_accuracy'] = round(baseline_acc, 4)
    
    with open(MODEL_PATH, 'w') as f:
        json.dump(model_dict, f, cls=NumpyEncoder)
    
    print(f"\n✅ 模型已保存: {MODEL_PATH}")
    print(f"   特征数: {model.n_features}")
    print(f"   树数: {sum(len(t) for t in model.trees)}")

if __name__ == '__main__':
    main()
