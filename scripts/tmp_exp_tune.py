#!/usr/bin/env python3
"""LGBM 超参实验（不覆盖生产模型）— 2026-08-19
复用 train_lgbm 的特征提取（同源），一次性构建特征矩阵，
对多组超参做时间序 80/20 评估，对比 Pinnacle 隐含基线。
"""
import sys, os, json, sqlite3
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import train_lgbm
import ai_analysis

DB_PATH = 'data/football.db'
CACHE = 'data/cache'
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

def build_matrix():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    league_priors = ai_analysis.load_league_priors(DB_PATH)
    rows = conn.execute("""
        SELECT * FROM poisson_predictions
        WHERE pinnacle_close_w > 1.01
          AND reference_score IS NOT NULL AND reference_score != ''
        ORDER BY date
    """).fetchall()
    timeline = train_lgbm.build_team_form_map(rows)
    X_list, y_list, dates = [], [], []
    for row in rows:
        r = dict(row)
        label = train_lgbm.get_result_label(r.get('reference_score', ''))
        if label is None:
            continue
        date = r.get('date', '')
        form_data = {
            r.get('home_team', ''): train_lgbm.get_team_form(timeline, r.get('home_team', ''), date),
            r.get('away_team', ''): train_lgbm.get_team_form(timeline, r.get('away_team', ''), date),
        }
        feats = train_lgbm.extract_features(r, form_data=form_data, conn=conn, league_priors=league_priors)
        X_list.append(feats)
        y_list.append(label)
        dates.append(date)
    conn.close()
    X = np.array(X_list, dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.array(y_list)
    return X, y, rows, dates

def baseline_acc(rows, split, y):
    correct = 0
    for i, row in enumerate(rows[split:]):
        r = dict(row)
        pin_w = train_lgbm._safe_float(r.get('pinnacle_close_w'), 1.0)
        pin_d = train_lgbm._safe_float(r.get('pinnacle_close_d'), 1.0)
        pin_l = train_lgbm._safe_float(r.get('pinnacle_close_l'), 1.0)
        imp = train_lgbm._implied(pin_w, pin_d, pin_l)
        if imp[0] > 0 and np.argmax(imp[:3]) == y[split + i]:
            correct += 1
    return correct / len(y[split:])

def per_class_acc(y_true, proba):
    pred = np.argmax(proba, axis=1)
    out = []
    for c in range(3):
        m = y_true == c
        out.append(f"类{c}({m.sum()})={np.mean(pred[m] == c):.1%}" if m.sum() else f"类{c}=n/a")
    return " ".join(out)

def main():
    X, y, rows, dates = build_matrix()
    n = len(X)
    split = int(n * 0.8)
    Xtr, Xte, ytr, yte = X[:split], X[split:], y[:split], y[split:]
    print(f"样本 {n}  训练 {len(Xtr)}  测试 {len(Xte)}")
    base = baseline_acc(rows, split, y)
    print(f"Pinnacle隐含基线: {base:.1%}\n")

    # 给 SimpleLGBM 临时加 feature_fraction 参数
    def make_model(n_trees, depth, lr, ff):
        m = train_lgbm.SimpleLGBM(n_estimators=n_trees, max_depth=depth, learning_rate=lr)
        m.min_samples = 10
        if hasattr(m, 'feature_fraction'):
            m.feature_fraction = ff
        else:
            import types
            # 运行时打补丁：_build_tree 用实例属性
            orig_bt = m._build_tree
            def patched_bt(X, residual, depth=0):
                return _build_tree_with_ff(m, X, residual, depth, ff)
            # 简单方式：直接改类方法
            return m
        return m

    results = []
    combos = [
        (60, 4, 0.1, 0.6),    # 基线复现（当前默认）
        (60, 5, 0.1, 0.6),
        (60, 6, 0.1, 0.6),
        (120, 4, 0.1, 0.6),
        (120, 5, 0.1, 0.6),
        (120, 6, 0.1, 0.6),
        (180, 5, 0.1, 0.6),
        (120, 5, 0.05, 0.6),
        (120, 5, 0.1, 0.8),
        (120, 5, 0.1, 1.0),
        (200, 6, 0.05, 0.8),
    ]
    for nt, depth, lr, ff in combos:
        np.random.seed(42)
        model = train_lgbm.SimpleLGBM(n_estimators=nt, max_depth=depth, learning_rate=lr)
        model.min_samples = 10
        model.feature_fraction = ff
        # 打补丁 _build_tree 使用 feature_fraction
        _orig_build = train_lgbm.SimpleLGBM._build_tree
        def _ff_build(self, X, residual, depth=0):
            if depth >= self.max_depth or len(X) < self.min_samples:
                return float(np.mean(residual)) if len(residual) > 0 else 0.0
            best_gain, best_feat, best_thresh = -1, None, None
            ff = getattr(self, 'feature_fraction', 0.6)
            n_try = max(1, int(X.shape[1] * ff))
            feat_idx = np.random.choice(X.shape[1], n_try, replace=False)
            for f in feat_idx:
                vals = X[:, f]
                thresholds = sorted(set([np.percentile(vals, p) for p in [25, 50, 75]]))
                for th in thresholds:
                    left_y = residual[vals <= th]
                    right_y = residual[vals > th]
                    if len(left_y) < self.min_samples or len(right_y) < self.min_samples:
                        continue
                    imp = np.var(residual) - (len(left_y)/len(residual)*np.var(left_y) + len(right_y)/len(residual)*np.var(right_y))
                    if imp > best_gain:
                        best_gain, best_feat, best_thresh = imp, f, th
            if best_feat is None:
                return float(np.mean(residual)) if len(residual) > 0 else 0.0
            left_idx = X[:, best_feat] <= best_thresh
            right_idx = X[:, best_feat] > best_thresh
            return {'feature': int(best_feat), 'threshold': float(best_thresh),
                    'left': _ff_build(self, X[left_idx], residual[left_idx], depth+1),
                    'right': _ff_build(self, X[right_idx], residual[right_idx], depth+1)}
        train_lgbm.SimpleLGBM._build_tree = _ff_build
        model.fit(Xtr, ytr)
        tr_pred = model.predict_proba(Xtr)
        te_pred = model.predict_proba(Xte)
        tr_acc = np.mean(np.argmax(tr_pred, 1) == ytr)
        te_acc = np.mean(np.argmax(te_pred, 1) == yte)
        results.append((nt, depth, lr, ff, tr_acc, te_acc, per_class_acc(yte, te_pred)))
        print(f"树{nt:>3} 深{depth} lr{lr:.2f} ff{ff:.1f}  训练{tr_acc:.1%}  测试{te_acc:.1%}  | {results[-1][-1]}")
        train_lgbm.SimpleLGBM._build_tree = _orig_build  # 恢复

    print(f"\n基线: {base:.1%}")
    best = max(results, key=lambda r: r[5])
    print(f"最佳: 树{best[0]} 深{best[1]} lr{best[2]} ff{best[3]} → {best[5]:.1%}")

if __name__ == '__main__':
    main()
