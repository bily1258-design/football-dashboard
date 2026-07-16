#!/usr/bin/env python3
"""快速尝试几组不同的超参"""
import sys, os, json, sqlite3
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')

# 从train_lgbm复制 SimpleLGBM 和 extract_features
# ...但是更简单的方式: 直接 import train_lgbm 模块
sys.path.insert(0, os.path.dirname(__file__))
import importlib.util
spec = importlib.util.spec_from_file_location("train_lgbm", 
    os.path.join(os.path.dirname(__file__), "train_lgbm.py"))
train = importlib.util.module_from_spec(spec)
spec.loader.exec_module(train)

# 加载数据
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.execute("""
    SELECT * FROM poisson_predictions
    WHERE pinnacle_close_w > 1.01 
      AND reference_score IS NOT NULL AND reference_score != ''
    ORDER BY date
""")
rows = cur.fetchall()
conn.close()

X_list, y_list = [], []
for row in rows:
    r = dict(row)
    label = train.get_result_label(r.get('reference_score', ''))
    if label is None: continue
    feats = train.extract_features(r)
    X_list.append(feats)
    y_list.append(label)

X = np.array(X_list, dtype=float)
y = np.array(y_list, dtype=int)

# 时间切片 80/20
split = int(len(X) * 0.8)
X_train, X_test = X[:split], X[split:]
y_train, y_test = y[:split], y[split:]

# 基准准确率
baseline = 0
from collections import Counter
n_test = len(y_test)
for i in range(n_test):
    pred = X_test[i, 6:9]  # implied_w/d/l
    base_dir = int(np.argmax(pred))
    if base_dir == y_test[i]:
        baseline += 1
baseline_acc = baseline / n_test

print(f"训练: {len(X_train)}, 测试: {len(X_test)}")
print(f"基准 (Pinnacle隐含): {baseline_acc:.1%}")
print()

combos = [
    (30, 4, 0.1, "基线"),
    (50, 4, 0.1, "更多树"),
    (80, 4, 0.1, "更多树+"),
    (30, 5, 0.1, "更深树"),
    (50, 5, 0.1, "更多+更深"),
    (30, 4, 0.08, "低学习率"),
    (50, 4, 0.08, "多树+低学习率"),
    (30, 4, 0.15, "高学习率"),
    (60, 6, 0.08, "激进"),
]

for n_est, depth, lr, label in combos:
    model = train.SimpleLGBM(n_estimators=n_est, max_depth=depth, learning_rate=lr)
    model.min_samples = 10
    model.fit(X_train, y_train)
    probas = model.predict_proba(X_test)
    preds = np.argmax(probas, axis=1)
    acc = np.mean(preds == y_test)
    train_probas = model.predict_proba(X_train)
    train_preds = np.argmax(train_probas, axis=1)
    train_acc = np.mean(train_preds == y_train)
    delta = acc - baseline_acc
    marker = " ✓" if acc > 0.60 else ""
    print(f"{label:>16s}: n={n_est:2d} d={depth} lr={lr:.2f} | "
          f"train={train_acc:.1%} test={acc:.1%} Δ={delta:+.1%}{marker}")
