#!/usr/bin/env python3
"""
融合概率预测模块 V2
- LGBM模型：用泊松概率+市场隐含概率+赔率特征预测赛果
- 融合概率：0.3×泊松 + 0.7×LGBM
- 自动训练：首次运行时从DB中有赛果的记录训练模型
"""

import os
import json
import sqlite3
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
MODEL_PATH = os.path.join(REPO_DIR, "data", "cache", "lgbm_model.json")
DB_PATH = os.path.join(REPO_DIR, "data", "shared_state", "football.db")


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _extract_features(row):
    """从DB行提取LGBM特征"""
    # 泊松概率
    p_w = _safe_float(row['poisson_win'])
    p_d = _safe_float(row['poisson_draw'])
    p_l = _safe_float(row['poisson_loss'])
    
    # 市场概率（final = 0.7泊松+0.3市场）
    f_w = _safe_float(row['final_win'])
    f_d = _safe_float(row['final_draw'])
    f_l = _safe_float(row['final_loss'])
    
    # 赔率
    odds_w = _safe_float(row['odds_win'])
    odds_d = _safe_float(row['odds_draw'])
    odds_l = _safe_float(row['odds_loss'])
    
    # 隐含概率
    imp_w = _safe_float(row['implied_prob_w'])
    imp_d = _safe_float(row['implied_prob_d'])
    imp_l = _safe_float(row['implied_prob_l'])
    
    # 赔率倒数（简单隐含概率，不去抽水）
    inv_w = 1.0 / odds_w if odds_w > 1.01 else 0
    inv_d = 1.0 / odds_d if odds_d > 1.01 else 0
    inv_l = 1.0 / odds_l if odds_l > 1.01 else 0
    inv_total = inv_w + inv_d + inv_l
    if inv_total > 0:
        inv_w /= inv_total
        inv_d /= inv_total
        inv_l /= inv_total
    
    # 派生特征
    poisson_margin = p_w - p_l  # 主客概率差
    odds_margin = inv_w - inv_l  # 主客赔率差
    
    return [
        p_w, p_d, p_l,           # 泊松概率
        f_w, f_d, f_l,           # final概率
        inv_w, inv_d, inv_l,     # 赔率隐含概率
        imp_w, imp_d, imp_l,     # 市场隐含概率（去抽水）
        poisson_margin, odds_margin,  # 派生特征
    ]


class SimpleLGBM:
    """轻量级梯度提升树（纯Python，不依赖lightgbm库）"""
    
    def __init__(self, n_estimators=50, max_depth=4, learning_rate=0.1):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.trees = []  # 每个方向3棵树
        self.init_pred = None
    
    def _gini_impurity(self, y):
        if len(y) == 0:
            return 0
        p = np.mean(y)
        return 2 * p * (1 - p)
    
    def _find_best_split(self, X, y, feature_indices):
        best_gain = -1
        best_feat = None
        best_thresh = None
        n = len(y)
        
        for feat in feature_indices:
            values = X[:, feat]
            unique_vals = np.unique(values)
            if len(unique_vals) < 2:
                continue
            
            # 只试中位数和四分位数，加速
            percentiles = np.percentile(unique_vals, [25, 50, 75])
            
            for thresh in percentiles:
                left_mask = values <= thresh
                right_mask = ~left_mask
                
                n_left = np.sum(left_mask)
                n_right = np.sum(right_mask)
                
                if n_left < 5 or n_right < 5:
                    continue
                
                gini_parent = self._gini_impurity(y)
                gini_left = self._gini_impurity(y[left_mask])
                gini_right = self._gini_impurity(y[right_mask])
                
                gain = gini_parent - (n_left/n * gini_left + n_right/n * gini_right)
                
                if gain > best_gain:
                    best_gain = gain
                    best_feat = feat
                    best_thresh = thresh
        
        return best_feat, best_thresh
    
    def _build_tree(self, X, y, depth=0):
        """递归构建决策树"""
        n = len(y)
        
        # 终止条件
        if depth >= self.max_depth or n < 10 or self._gini_impurity(y) < 0.05:
            return {'leaf': True, 'value': float(np.mean(y))}
        
        feat, thresh = self._find_best_split(X, y, range(X.shape[1]))
        
        if feat is None:
            return {'leaf': True, 'value': float(np.mean(y))}
        
        left_mask = X[:, feat] <= thresh
        right_mask = ~left_mask
        
        return {
            'leaf': False,
            'feature': int(feat),
            'threshold': float(thresh),
            'left': self._build_tree(X[left_mask], y[left_mask], depth+1),
            'right': self._build_tree(X[right_mask], y[right_mask], depth+1),
        }
    
    def _predict_tree(self, tree, x):
        if tree['leaf']:
            return tree['value']
        if x[tree['feature']] <= tree['threshold']:
            return self._predict_tree(tree['left'], x)
        else:
            return self._predict_tree(tree['right'], x)
    
    def fit(self, X, y_labels):
        """训练3个方向的分类器
        y_labels: 0=主胜, 1=平局, 2=客胜
        """
        n = len(y_labels)
        self.init_pred = np.zeros(3)
        
        # One-vs-Rest: 每个方向独立训练
        for cls in range(3):
            y_cls = (y_labels == cls).astype(float)
            self.init_pred[cls] = np.mean(y_cls)
        
        self.trees = []
        
        for cls in range(3):
            y_cls = (y_labels == cls).astype(float)
            pred = np.full(n, self.init_pred[cls])
            cls_trees = []
            
            for _ in range(self.n_estimators):
                residual = y_cls - pred
                
                # 构建回归树拟合残差
                tree = self._build_tree(X, residual)
                cls_trees.append(tree)
                
                # 更新预测
                for i in range(n):
                    pred[i] += self.learning_rate * self._predict_tree(tree, X[i])
            
            self.trees.append(cls_trees)
        
        return self
    
    def predict_proba(self, X):
        """返回3个方向的概率 [n_samples, 3]"""
        n = X.shape[0]
        raw = np.zeros((n, 3))
        
        for cls in range(3):
            pred = np.full(n, self.init_pred[cls])
            for tree in self.trees[cls]:
                for i in range(n):
                    pred[i] += self.learning_rate * self._predict_tree(tree, X[i])
            raw[:, cls] = pred
        
        # Softmax归一化
        exp_raw = np.exp(raw - np.max(raw, axis=1, keepdims=True))
        proba = exp_raw / np.sum(exp_raw, axis=1, keepdims=True)
        
        return proba
    
    def to_dict(self):
        return {
            'n_estimators': self.n_estimators,
            'max_depth': self.max_depth,
            'learning_rate': self.learning_rate,
            'init_pred': self.init_pred.tolist(),
            'trees': self.trees,
        }
    
    @classmethod
    def from_dict(cls, d):
        model = cls(d['n_estimators'], d['max_depth'], d['learning_rate'])
        model.init_pred = np.array(d['init_pred'])
        model.trees = d['trees']
        return model


class FusionPredictor:
    """融合概率预测器：0.3泊松 + 0.7LGBM"""
    
    def __init__(self, db_path=None):
        self.w_poisson = 0.3
        self.w_lgb = 0.7
        self.model = None
        self.model_loaded = False
        
        _db = db_path or DB_PATH
        
        # 尝试加载已有模型
        if os.path.exists(MODEL_PATH):
            try:
                with open(MODEL_PATH, 'r') as f:
                    model_dict = json.load(f)
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
        
        if len(rows) < 30:
            print(f"⚠️ 训练数据不足: {len(rows)}条，需要至少30条")
            return
        
        print(f"📊 训练LGBM模型: {len(rows)}条有赛果记录")
        
        X_list = []
        y_list = []
        
        for row in rows:
            feat = _extract_features(row)
            outcome = row['actual_outcome']
            # 解析格式："主胜 2-1", "客胜 0-3", "平局 1-1", "0-0", "让球胜 2-1" 等
            if '主胜' in outcome or '让球胜' in outcome:
                y = 0
            elif '平局' in outcome or '让球平' in outcome:
                y = 1
            elif '客胜' in outcome or '让球负' in outcome:
                y = 2
            elif outcome in ('H',):
                y = 0
            elif outcome in ('D',):
                y = 1
            elif outcome in ('A',):
                y = 2
            else:
                # 纯比分格式 "0-0", "1-0" → 从比分推断
                try:
                    parts = outcome.strip().split('-')
                    if len(parts) == 2:
                        h, a = int(parts[0]), int(parts[1])
                        if h > a:
                            y = 0
                        elif h == a:
                            y = 1
                        else:
                            y = 2
                    else:
                        continue
                except:
                    continue
            
            X_list.append(feat)
            y_list.append(y)
        
        if len(X_list) < 30:
            print(f"⚠️ 有效训练数据不足: {len(X_list)}条")
            return
        
        X = np.array(X_list, dtype=float)
        y = np.array(y_list)
        
        print(f"  主胜{np.sum(y==0)} / 平局{np.sum(y==1)} / 客胜{np.sum(y==2)}")
        
        self.model = SimpleLGBM(n_estimators=50, max_depth=4, learning_rate=0.1)
        self.model.fit(X, y)
        self.model_loaded = True
        
        # 保存模型
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, 'w') as f:
            json.dump(self.model.to_dict(), f)
        print(f"✅ 模型已保存: {MODEL_PATH}")
        
        # 计算训练集准确率
        proba = self.model.predict_proba(X)
        pred_labels = np.argmax(proba, axis=1)
        acc = np.mean(pred_labels == y)
        print(f"  训练集准确率: {acc:.1%}")
    
    def predict(self, row):
        """预测单场比赛
        Args:
            row: sqlite3.Row 或 dict
        Returns:
            {'prob': [fusion_w, fusion_d, fusion_l], 
             'details': {'lgb_prob': [lgb_w, lgb_d, lgb_l], 'poisson_prob': [p_w, p_d, p_l]}}
        """
        # 泊松概率
        p_w = _safe_float(row['poisson_win'])
        p_d = _safe_float(row['poisson_draw'])
        p_l = _safe_float(row['poisson_loss'])
        poisson_prob = [p_w, p_d, p_l]
        
        if not self.model_loaded:
            # 无模型：融合=final概率（已经是0.7泊松+0.3市场）
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
        proba = self.model.predict_proba(X)[0]
        lgb_prob = [float(proba[0]), float(proba[1]), float(proba[2])]
        
        # 融合：0.3泊松 + 0.7LGBM
        fusion_w = self.w_poisson * p_w + self.w_lgb * lgb_prob[0]
        fusion_d = self.w_poisson * p_d + self.w_lgb * lgb_prob[1]
        fusion_l = self.w_poisson * p_l + self.w_lgb * lgb_prob[2]
        
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
            }
        }


if __name__ == '__main__':
    print("=" * 60)
    print("FusionPredictor 测试")
    print("=" * 60)
    
    predictor = FusionPredictor()
    
    if predictor.model_loaded:
        # 测试几条记录
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM poisson_predictions WHERE poisson_win > 0 ORDER BY date DESC LIMIT 5")
        for row in cur.fetchall():
            result = predictor.predict(row)
            print(f"  {row['home_team']} vs {row['away_team']}: "
                  f"泊松[{row['poisson_win']:.2f}/{row['poisson_draw']:.2f}/{row['poisson_loss']:.2f}] "
                  f"LGBM[{result['details']['lgb_prob'][0]:.2f}/{result['details']['lgb_prob'][1]:.2f}/{result['details']['lgb_prob'][2]:.2f}] "
                  f"融合[{result['prob'][0]:.2f}/{result['prob'][1]:.2f}/{result['prob'][2]:.2f}]")
        conn.close()
    else:
        print("❌ 模型未加载")
