#!/usr/bin/env python3
"""
融合概率预测模块 V3
升级要点：
1. 特征工程：新增Pinnacle赔率变动、赔率差异、百家vs Pinnacle分歧等14个特征
2. 模型：更深的树(max_depth=5)、更多轮数(80)、早停
3. 交叉验证评估：5折CV给出真实泛化准确率
4. 动态融合权重：根据特征完整度自动调整泊松/LGBM权重
5. 重新训练命令：--retrain 强制重训
"""

import os
import sys
import json
import sqlite3
import numpy as np
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
MODEL_PATH = os.path.join(REPO_DIR, "data", "cache", "lgbm_model.json")
DB_PATH = os.path.join(REPO_DIR, "data", "football.db")


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _parse_movement(movement_str):
    """解析pinnacle_movement JSON字符串，返回(w_dir, d_dir, l_dir)
    down=-1, stable=0, up=1
    """
    if not movement_str or movement_str == '':
        return (0, 0, 0)
    try:
        m = json.loads(movement_str) if isinstance(movement_str, str) else movement_str
        d = {'down': -1, 'stable': 0, 'up': 1}
        return (d.get(m.get('w', 'stable'), 0),
                d.get(m.get('d', 'stable'), 0),
                d.get(m.get('l', 'stable'), 0))
    except:
        return (0, 0, 0)


FEATURE_NAMES = [
    # 泊松概率 (3)
    'poisson_w', 'poisson_d', 'poisson_l',
    # final概率 (3)
    'final_w', 'final_d', 'final_l',
    # 百家隐含概率 (3)
    'implied_w', 'implied_d', 'implied_l',
    # Pinnacle开盘隐含概率 (3)
    'pin_open_w', 'pin_open_d', 'pin_open_l',
    # Pinnacle收盘隐含概率 (3)
    'pin_close_w', 'pin_close_d', 'pin_close_l',
    # Pinnacle赔率变动方向 (3)
    'pin_move_w', 'pin_move_d', 'pin_move_l',
    # Pinnacle开收差异 (3)
    'pin_diff_w', 'pin_diff_d', 'pin_diff_l',
    # Pinnacle margin (1)
    'pin_margin',
    # 百家 vs Pinnacle 分歧度 (3)
    'disagree_w', 'disagree_d', 'disagree_l',
    # 泊松 vs 市场差异 (2)
    'poisson_market_margin', 'poisson_market_draw_diff',
    # 赔率级别特征 (2)
    'odds_level', 'draw_premium',
    # lambda (2)
    'lambda_h', 'lambda_a',
]


def _extract_features(row):
    """从DB行提取LGBM特征 V3（28维）"""
    # 泊松概率
    p_w = _safe_float(row['poisson_win'])
    p_d = _safe_float(row['poisson_draw'])
    p_l = _safe_float(row['poisson_loss'])
    
    # final概率
    f_w = _safe_float(row['final_win'])
    f_d = _safe_float(row['final_draw'])
    f_l = _safe_float(row['final_loss'])
    
    # 百家隐含概率（去抽水）
    imp_w = _safe_float(row['implied_prob_w'])
    imp_d = _safe_float(row['implied_prob_d'])
    imp_l = _safe_float(row['implied_prob_l'])
    
    # Pinnacle开盘
    pin_ow = _safe_float(row['pinnacle_open_w'])
    pin_od = _safe_float(row['pinnacle_open_d'])
    pin_ol = _safe_float(row['pinnacle_open_l'])
    
    # Pinnacle收盘
    pin_cw = _safe_float(row['pinnacle_close_w'])
    pin_cd = _safe_float(row['pinnacle_close_d'])
    pin_cl = _safe_float(row['pinnacle_close_l'])
    
    # Pinnacle开盘隐含概率（去抽水）
    has_pin_open = pin_ow > 1.01 and pin_od > 1.01 and pin_ol > 1.01
    if has_pin_open:
        inv_ow = 1.0 / pin_ow
        inv_od = 1.0 / pin_od
        inv_ol = 1.0 / pin_ol
        inv_total = inv_ow + inv_od + inv_ol
        pin_open_w = inv_ow / inv_total
        pin_open_d = inv_od / inv_total
        pin_open_l = inv_ol / inv_total
    else:
        pin_open_w = pin_open_d = pin_open_l = 0.0
    
    # Pinnacle收盘隐含概率（去抽水）
    has_pin_close = pin_cw > 1.01 and pin_cd > 1.01 and pin_cl > 1.01
    if has_pin_close:
        inv_cw = 1.0 / pin_cw
        inv_cd = 1.0 / pin_cd
        inv_cl = 1.0 / pin_cl
        inv_total = inv_cw + inv_cd + inv_cl
        pin_close_w = inv_cw / inv_total
        pin_close_d = inv_cd / inv_total
        pin_close_l = inv_cl / inv_total
    else:
        pin_close_w = pin_close_d = pin_close_l = 0.0
    
    # Pinnacle赔率变动方向
    move_w, move_d, move_l = _parse_movement(row['pinnacle_movement'] if hasattr(row, 'keys') else '')
    
    # Pinnacle开收差异（收盘-开盘隐含概率差）
    pin_diff_w = (pin_close_w - pin_open_w) if has_pin_open and has_pin_close else 0.0
    pin_diff_d = (pin_close_d - pin_open_d) if has_pin_open and has_pin_close else 0.0
    pin_diff_l = (pin_close_l - pin_open_l) if has_pin_open and has_pin_close else 0.0
    
    # Pinnacle margin
    pin_margin = _safe_float(row['pinnacle_margin'])
    
    # 百家 vs Pinnacle 分歧度
    if has_pin_close and imp_w > 0:
        disagree_w = imp_w - pin_close_w
        disagree_d = imp_d - pin_close_d
        disagree_l = imp_l - pin_close_l
    elif has_pin_open and imp_w > 0:
        disagree_w = imp_w - pin_open_w
        disagree_d = imp_d - pin_open_d
        disagree_l = imp_l - pin_open_l
    else:
        disagree_w = disagree_d = disagree_l = 0.0
    
    # 泊松 vs 市场差异
    poisson_market_margin = (p_w - p_l) - (imp_w - imp_l) if imp_w > 0 else 0.0
    poisson_market_draw_diff = p_d - imp_d if imp_d > 0 else 0.0
    
    # 赔率级别特征
    odds_w = _safe_float(row['odds_win'])
    odds_d = _safe_float(row['odds_draw'])
    odds_l = _safe_float(row['odds_loss'])
    odds_level = 1.0 / max(odds_w, 1.01) if odds_w > 1.01 else 0.0  # 最热门方向的信心度
    draw_premium = (odds_d - (odds_w + odds_l) / 2) / max((odds_w + odds_l) / 2, 0.01) if odds_w > 1.01 and odds_l > 1.01 else 0.0
    
    # lambda
    lambda_h = _safe_float(row['home_lambda'])
    lambda_a = _safe_float(row['away_lambda'])
    
    return [
        p_w, p_d, p_l,                    # 泊松
        f_w, f_d, f_l,                    # final
        imp_w, imp_d, imp_l,              # 百家隐含
        pin_open_w, pin_open_d, pin_open_l,  # Pinnacle开盘隐含
        pin_close_w, pin_close_d, pin_close_l,  # Pinnacle收盘隐含
        move_w, move_d, move_l,           # 变动方向
        pin_diff_w, pin_diff_d, pin_diff_l,  # 开收差异
        pin_margin,                        # margin
        disagree_w, disagree_d, disagree_l,  # 分歧度
        poisson_market_margin, poisson_market_draw_diff,  # 泊松vs市场
        odds_level, draw_premium,          # 赔率级别
        lambda_h, lambda_a,               # lambda
    ]


class DecisionTree:
    """单棵决策树（回归树，用于梯度提升）"""
    
    def __init__(self, max_depth=5, min_samples=10):
        self.max_depth = max_depth
        self.min_samples = min_samples
        self.tree = None
    
    def _find_best_split(self, X, y, feature_indices, sample_weights=None):
        best_gain = -1
        best_feat = None
        best_thresh = None
        n = len(y)
        
        if sample_weights is not None:
            total_weight = np.sum(sample_weights)
        else:
            total_weight = n
        
        for feat in feature_indices:
            values = X[:, feat]
            unique_vals = np.unique(values)
            if len(unique_vals) < 2:
                continue
            
            # 采样分位数加速
            if len(unique_vals) > 10:
                percentiles = np.percentile(unique_vals, [20, 40, 50, 60, 80])
            else:
                percentiles = unique_vals[:-1]
            
            for thresh in percentiles:
                left_mask = values <= thresh
                right_mask = ~left_mask
                
                n_left = np.sum(left_mask)
                n_right = np.sum(right_mask)
                
                if n_left < 5 or n_right < 5:
                    continue
                
                if sample_weights is not None:
                    w_left = np.sum(sample_weights[left_mask])
                    w_right = np.sum(sample_weights[right_mask])
                    if w_left < 1 or w_right < 1:
                        continue
                    mean_left = np.average(y[left_mask], weights=sample_weights[left_mask])
                    mean_right = np.average(y[right_mask], weights=sample_weights[right_mask])
                    # 方差减少
                    left_var = np.average((y[left_mask] - mean_left)**2, weights=sample_weights[left_mask])
                    right_var = np.average((y[right_mask] - mean_right)**2, weights=sample_weights[right_mask])
                    parent_mean = np.average(y, weights=sample_weights)
                    parent_var = np.average((y - parent_mean)**2, weights=sample_weights)
                else:
                    mean_left = np.mean(y[left_mask])
                    mean_right = np.mean(y[right_mask])
                    parent_mean = np.mean(y)
                    parent_var = np.var(y)
                    left_var = np.var(y[left_mask])
                    right_var = np.var(y[right_mask])
                
                gain = parent_var - (n_left/n * left_var + n_right/n * right_var)
                
                if gain > best_gain:
                    best_gain = gain
                    best_feat = feat
                    best_thresh = thresh
        
        return best_feat, best_thresh, best_gain
    
    def _build_tree(self, X, y, depth=0, feature_indices=None, sample_weights=None):
        n = len(y)
        
        if feature_indices is None:
            feature_indices = list(range(X.shape[1]))
        
        # 终止条件
        if depth >= self.max_depth or n < self.min_samples or np.var(y) < 1e-6:
            val = float(np.average(y, weights=sample_weights)) if sample_weights is not None else float(np.mean(y))
            return {'leaf': True, 'value': val, 'n': n}
        
        feat, thresh, gain = self._find_best_split(X, y, feature_indices, sample_weights=sample_weights)
        
        if feat is None or gain < 1e-8:
            val = float(np.average(y, weights=sample_weights)) if sample_weights is not None else float(np.mean(y))
            return {'leaf': True, 'value': val, 'n': n}
        
        left_mask = X[:, feat] <= thresh
        right_mask = ~left_mask
        
        w_left = sample_weights[left_mask] if sample_weights is not None else None
        w_right = sample_weights[right_mask] if sample_weights is not None else None
        
        return {
            'leaf': False,
            'feature': int(feat),
            'threshold': float(thresh),
            'gain': float(gain),
            'left': self._build_tree(X[left_mask], y[left_mask], depth+1, feature_indices, sample_weights=w_left),
            'right': self._build_tree(X[right_mask], y[right_mask], depth+1, feature_indices, sample_weights=w_right),
        }
    
    def fit(self, X, y, feature_indices=None, sample_weights=None):
        self.tree = self._build_tree(X, y, feature_indices=feature_indices, sample_weights=sample_weights)
        return self
    
    def predict_one(self, x):
        node = self.tree
        while not node['leaf']:
            if x[node['feature']] <= node['threshold']:
                node = node['left']
            else:
                node = node['right']
        return node['value']
    
    def predict(self, X):
        return np.array([self.predict_one(x) for x in X])


class SimpleLGBM:
    """轻量级梯度提升树 V3
    - One-vs-Rest三分类
    - 支持特征子采样(colsample_bytree)
    - 支持样本权重
    - 早停机制
    """
    
    def __init__(self, n_estimators=80, max_depth=5, learning_rate=0.08,
                 colsample_bytree=0.7, min_samples=10):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.colsample_bytree = colsample_bytree
        self.min_samples = min_samples
        self.trees = []
        self.init_pred = None
        self.feature_importance = None
        self.n_features = None
    
    def fit(self, X, y_labels, sample_weights=None):
        """训练3个方向的分类器"""
        n = len(y_labels)
        self.n_features = X.shape[1]
        self.init_pred = np.zeros(3)
        self.feature_importance = np.zeros(self.n_features)
        
        # One-vs-Rest
        for cls in range(3):
            y_cls = (y_labels == cls).astype(float)
            self.init_pred[cls] = np.mean(y_cls)
        
        self.trees = []
        
        for cls in range(3):
            y_cls = (y_labels == cls).astype(float)
            pred = np.full(n, self.init_pred[cls])
            cls_trees = []
            
            best_loss = float('inf')
            no_improve = 0
            
            for round_i in range(self.n_estimators):
                residual = y_cls - pred
                
                # 特征子采样
                n_select = max(1, int(self.n_features * self.colsample_bytree))
                feat_indices = sorted(np.random.choice(self.n_features, n_select, replace=False))
                
                tree = DecisionTree(max_depth=self.max_depth, min_samples=self.min_samples)
                tree.fit(X, residual, feature_indices=feat_indices, sample_weights=sample_weights)
                cls_trees.append(tree)
                
                # 收集特征重要性
                self._collect_importance(tree.tree, feat_indices)
                
                # 更新预测
                preds = tree.predict(X)
                pred += self.learning_rate * preds
                
                # 早停：检查加权logloss
                if sample_weights is not None:
                    w_total = np.sum(sample_weights)
                    logloss = -np.sum(sample_weights * (y_cls * np.log(np.clip(pred, 1e-7, 1-1e-7)) + 
                                   (1 - y_cls) * np.log(np.clip(1 - pred, 1e-7, 1-1e-7)))) / w_total
                else:
                    logloss = -np.mean(y_cls * np.log(np.clip(pred, 1e-7, 1-1e-7)) + 
                                   (1 - y_cls) * np.log(np.clip(1 - pred, 1e-7, 1-1e-7)))
                if logloss < best_loss - 1e-5:
                    best_loss = logloss
                    no_improve = 0
                else:
                    no_improve += 1
                
                if no_improve >= 10:
                    # 截断到当前轮数
                    cls_trees = cls_trees[:round_i+1]
                    break
            
            self.trees.append(cls_trees)
        
        # 归一化特征重要性
        total = np.sum(self.feature_importance)
        if total > 0:
            self.feature_importance /= total
        
        return self
    
    def _collect_importance(self, node, feat_indices):
        """递归收集特征重要性"""
        if node['leaf']:
            return
        global_feat = feat_indices[node['feature']] if node['feature'] < len(feat_indices) else node['feature']
        self.feature_importance[global_feat] += node.get('gain', 0)
        self._collect_importance(node['left'], feat_indices)
        self._collect_importance(node['right'], feat_indices)
    
    def predict_proba(self, X):
        """返回3个方向的概率 [n_samples, 3]"""
        n = X.shape[0]
        raw = np.zeros((n, 3))
        
        for cls in range(3):
            pred = np.full(n, self.init_pred[cls])
            for tree in self.trees[cls]:
                pred += self.learning_rate * tree.predict(X)
            raw[:, cls] = pred
        
        # Softmax归一化
        exp_raw = np.exp(raw - np.max(raw, axis=1, keepdims=True))
        proba = exp_raw / np.sum(exp_raw, axis=1, keepdims=True)
        
        return proba
    
    def to_dict(self):
        return {
            'version': 3,
            'n_estimators': self.n_estimators,
            'max_depth': self.max_depth,
            'learning_rate': self.learning_rate,
            'colsample_bytree': self.colsample_bytree,
            'init_pred': self.init_pred.tolist(),
            'n_features': self.n_features,
            'feature_importance': self.feature_importance.tolist() if self.feature_importance is not None else [],
            'trees': [[tree.tree for tree in cls_trees] for cls_trees in self.trees],
        }
    
    @classmethod
    def from_dict(cls, d):
        model = cls(d.get('n_estimators', 80), d.get('max_depth', 5), 
                    d.get('learning_rate', 0.08), d.get('colsample_bytree', 0.7))
        model.init_pred = np.array(d['init_pred'])
        model.n_features = d.get('n_features', 14)
        model.feature_importance = np.array(d.get('feature_importance', []))
        # 重建树
        model.trees = []
        for cls_trees_data in d['trees']:
            cls_trees = []
            for tree_data in cls_trees_data:
                t = DecisionTree()
                t.tree = tree_data
                cls_trees.append(t)
            model.trees.append(cls_trees)
        return model


def _parse_outcome(outcome):
    """解析赛果字符串，返回0=主胜/1=平局/2=客胜"""
    if not outcome:
        return None
    if '主胜' in outcome or '让球胜' in outcome:
        return 0
    elif '平局' in outcome or '让球平' in outcome:
        return 1
    elif '客胜' in outcome or '让球负' in outcome:
        return 2
    elif outcome in ('H',):
        return 0
    elif outcome in ('D',):
        return 1
    elif outcome in ('A',):
        return 2
    else:
        # 纯比分格式 "0-0", "1-0"
        try:
            parts = outcome.strip().split('-')
            if len(parts) == 2:
                h, a = int(parts[0]), int(parts[1])
                if h > a: return 0
                elif h == a: return 1
                else: return 2
        except:
            pass
    return None


def cross_validate(X, y, n_folds=5, sample_weights=None, **model_kwargs):
    """5折交叉验证"""
    n = len(y)
    indices = np.arange(n)
    np.random.seed(42)
    np.random.shuffle(indices)
    
    fold_size = n // n_folds
    accuracies = []
    
    for fold in range(n_folds):
        val_start = fold * fold_size
        val_end = val_start + fold_size if fold < n_folds - 1 else n
        
        val_idx = indices[val_start:val_end]
        train_idx = np.concatenate([indices[:val_start], indices[val_end:]])
        
        X_train, y_train = X[train_idx], y[train_idx]
        X_val, y_val = X[val_idx], y[val_idx]
        w_train = sample_weights[train_idx] if sample_weights is not None else None
        
        model = SimpleLGBM(**model_kwargs)
        model.fit(X_train, y_train, sample_weights=w_train)
        
        proba = model.predict_proba(X_val)
        pred_labels = np.argmax(proba, axis=1)
        acc = np.mean(pred_labels == y_val)
        accuracies.append(acc)
        
        # 各方向准确率
        for cls, name in enumerate(['主胜', '平局', '客胜']):
            mask = y_val == cls
            if np.sum(mask) > 0:
                cls_acc = np.mean(pred_labels[mask] == y_val[mask])
            else:
                cls_acc = 0
    
    return np.mean(accuracies), np.std(accuracies), accuracies


class FusionPredictor:
    """融合概率预测器 V3
    - 动态权重：根据特征完整度调整泊松/LGBM权重
    - 有Pinnacle时更信任LGBM，无Pinnacle时更信任泊松
    """
    
    # 不同特征完整度下的融合权重 (w_poisson, w_lgb)
    WEIGHT_PROFILES = {
        'full': (0.2, 0.8),       # 有Pinnacle+百家
        'partial': (0.3, 0.7),    # 有百家无Pinnacle
        'minimal': (0.5, 0.5),    # 只有泊松
    }
    
    def __init__(self, db_path=None):
        self.model = None
        self.model_loaded = False
        
        _db = db_path or DB_PATH
        
        # 尝试加载已有模型
        if os.path.exists(MODEL_PATH):
            try:
                with open(MODEL_PATH, 'r') as f:
                    model_dict = json.load(f)
                # V2模型只有14个特征，V3有28个，需要重训
                if model_dict.get('version', 2) < 3:
                    print(f"📊 V2模型需升级为V3，将重新训练")
                    self._train(_db)
                    return
                self.model = SimpleLGBM.from_dict(model_dict)
                self.model_loaded = True
                return
            except Exception as e:
                print(f"⚠️ 模型加载失败: {e}，将重新训练")
        
        # 没有模型则自动训练
        self._train(_db)
    
    def _train(self, db_path):
        """从DB中有赛果的记录训练模型"""
        if not os.path.exists(db_path):
            print(f"⚠️ DB不存在: {db_path}，无法训练")
            return
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        cur.execute("""
            SELECT * FROM poisson_predictions 
            WHERE actual_outcome IS NOT NULL 
            AND actual_outcome != ''
            AND poisson_win > 0
        """)
        rows = cur.fetchall()
        conn.close()
        
        if len(rows) < 50:
            print(f"⚠️ 训练数据不足: {len(rows)}条，需要至少50条")
            return
        
        print(f"📊 训练LGBM V3模型: {len(rows)}条有赛果记录")
        
        X_list = []
        y_list = []
        
        for row in rows:
            outcome = _parse_outcome(row['actual_outcome'])
            if outcome is None:
                continue
            feat = _extract_features(row)
            X_list.append(feat)
            y_list.append(outcome)
        
        if len(X_list) < 50:
            print(f"⚠️ 有效训练数据不足: {len(X_list)}条")
            return
        
        X = np.array(X_list, dtype=float)
        y = np.array(y_list)
        
        # 处理NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        counts = Counter(y.tolist())
        print(f"  主胜{counts.get(0,0)} / 平局{counts.get(1,0)} / 客胜{counts.get(2,0)}")
        
        # 5折交叉验证（无样本权重，CV最优）
        print("🔄 5折交叉验证...")
        cv_mean, cv_std, cv_folds = cross_validate(
            X, y, n_folds=5,
            n_estimators=60, max_depth=3, learning_rate=0.05,
            colsample_bytree=0.7
        )
        print(f"  CV准确率: {cv_mean:.1%} ± {cv_std:.1%}  (各折: {[f'{a:.1%}' for a in cv_folds]})")
        
        # 全量训练
        self.model = SimpleLGBM(
            n_estimators=60, max_depth=3, learning_rate=0.05,
            colsample_bytree=0.7
        )
        self.model.fit(X, y)
        self.model_loaded = True
        
        # 保存模型
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, 'w') as f:
            json.dump(self.model.to_dict(), f)
        print(f"✅ 模型已保存: {MODEL_PATH}")
        
        # 训练集准确率
        proba = self.model.predict_proba(X)
        pred_labels = np.argmax(proba, axis=1)
        acc = np.mean(pred_labels == y)
        print(f"  训练集准确率: {acc:.1%}")
        
        # 各方向准确率
        for cls, name in enumerate(['主胜', '平局', '客胜']):
            mask = y == cls
            if np.sum(mask) > 0:
                cls_acc = np.mean(pred_labels[mask] == y[mask])
                print(f"  {name}准确率: {cls_acc:.1%} ({np.sum(mask)}条)")
        
        # Top-10特征重要性
        if self.model.feature_importance is not None and len(self.model.feature_importance) > 0:
            top_idx = np.argsort(self.model.feature_importance)[::-1][:10]
            print("  Top-10特征:")
            for i, idx in enumerate(top_idx):
                fname = FEATURE_NAMES[idx] if idx < len(FEATURE_NAMES) else f'feat_{idx}'
                print(f"    {i+1}. {fname}: {self.model.feature_importance[idx]:.4f}")
        
        # Brier分数
        y_onehot = np.zeros((len(y), 3))
        for i, yi in enumerate(y):
            y_onehot[i, yi] = 1.0
        brier = np.mean(np.sum((proba - y_onehot) ** 2, axis=1))
        print(f"  Brier分数: {brier:.4f}")
    
    def _get_weight_profile(self, row):
        """根据特征完整度选择融合权重"""
        has_pin = _safe_float(row['pinnacle_close_w']) > 1.01 or _safe_float(row['pinnacle_open_w']) > 1.01
        has_implied = _safe_float(row['implied_prob_w']) > 0
        
        if has_pin and has_implied:
            return self.WEIGHT_PROFILES['full']
        elif has_implied:
            return self.WEIGHT_PROFILES['partial']
        else:
            return self.WEIGHT_PROFILES['minimal']
    
    def predict(self, row):
        """预测单场比赛"""
        # 泊松概率
        p_w = _safe_float(row['poisson_win'])
        p_d = _safe_float(row['poisson_draw'])
        p_l = _safe_float(row['poisson_loss'])
        poisson_prob = [p_w, p_d, p_l]
        
        if not self.model_loaded:
            # 无模型：用final概率
            f_w = _safe_float(row['final_win'], p_w)
            f_d = _safe_float(row['final_draw'], p_d)
            f_l = _safe_float(row['final_loss'], p_l)
            return {
                'prob': [f_w, f_d, f_l],
                'details': {
                    'lgb_prob': [f_w, f_d, f_l],
                    'poisson_prob': poisson_prob,
                }
            }
        
        # LGBM预测
        feat = _extract_features(row)
        X = np.array([feat], dtype=float)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        proba = self.model.predict_proba(X)[0]
        lgb_prob = [float(proba[0]), float(proba[1]), float(proba[2])]
        
        # 动态权重
        w_poisson, w_lgb = self._get_weight_profile(row)
        
        # 融合
        fusion_w = w_poisson * p_w + w_lgb * lgb_prob[0]
        fusion_d = w_poisson * p_d + w_lgb * lgb_prob[1]
        fusion_l = w_poisson * p_l + w_lgb * lgb_prob[2]
        
        # 归一化
        total = fusion_w + fusion_d + fusion_l
        if total > 0:
            fusion_w /= total
            fusion_d /= total
            fusion_l /= total
        
        return {
            'prob': [fusion_w, fusion_d, fusion_l],
            'details': {
                'lgb_prob': lgb_prob,
                'poisson_prob': poisson_prob,
                'weight_profile': f'w_poisson={w_poisson}, w_lgb={w_lgb}',
            }
        }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--retrain', action='store_true', help='强制重新训练模型')
    parser.add_argument('--db', default=DB_PATH, help='数据库路径')
    args = parser.parse_args()
    
    print("=" * 60)
    print("FusionPredictor V3")
    print("=" * 60)
    
    if args.retrain:
        # 删除旧模型强制重训
        if os.path.exists(MODEL_PATH):
            os.remove(MODEL_PATH)
            print("🗑️ 已删除旧模型")
    
    predictor = FusionPredictor(db_path=args.db)
    
    if predictor.model_loaded:
        # 测试几条记录
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM poisson_predictions 
            WHERE poisson_win > 0 AND actual_outcome IS NOT NULL AND actual_outcome != ''
            ORDER BY date DESC LIMIT 10
        """)
        for row in cur.fetchall():
            result = predictor.predict(row)
            outcome = row['actual_outcome']
            print(f"  {row['home_team']} vs {row['away_team']} "
                  f"泊松[{row['poisson_win']:.2f}/{row['poisson_draw']:.2f}/{row['poisson_loss']:.2f}] "
                  f"LGBM[{result['details']['lgb_prob'][0]:.2f}/{result['details']['lgb_prob'][1]:.2f}/{result['details']['lgb_prob'][2]:.2f}] "
                  f"融合[{result['prob'][0]:.2f}/{result['prob'][1]:.2f}/{result['prob'][2]:.2f}] "
                  f"赛果={outcome}")
        conn.close()
    else:
        print("❌ 模型未加载")
