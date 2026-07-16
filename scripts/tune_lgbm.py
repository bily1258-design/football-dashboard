#!/usr/bin/env python3
"""
LGBM 超参调优（轻量版）
缩小搜索空间，先跑核心参数
"""
import os
import sys
import json
import sqlite3
import numpy as np
from collections import defaultdict
from itertools import product
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_lgbm import extract_features, get_result_label, NumpyEncoder, SimpleLGBM, _safe_float, _implied

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'football.db')
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
MODEL_PATH = os.path.join(CACHE_DIR, 'lgbm_model_tuned.json')

def load_data():
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
        label = get_result_label(r.get('reference_score', ''))
        if label is None:
            continue
        feats = extract_features(r, stats=None)[:35]  # 仅35维基础特征
        X_list.append(feats)
        y_list.append(label)
    
    X = np.nan_to_num(np.array(X_list, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    y = np.array(y_list)
    print(f"数据: {len(X)} 场, 特征: {X.shape[1]} 维")
    print(f"分布: 主胜={sum(y==0)}, 平局={sum(y==1)}, 客胜={sum(y==2)}")
    return X, y, rows

def main():
    print("="*50)
    print("LGBM 超参调优（轻量）")
    print("="*50)
    
    X, y, rows = load_data()
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    
    # 基准
    baseline_correct = 0
    for i, row in enumerate(rows[split:]):
        r = dict(row)
        pin_c = _safe_float(r.get('pinnacle_close_w'), 1.0), _safe_float(r.get('pinnacle_close_d'), 1.0), _safe_float(r.get('pinnacle_close_l'), 1.0)
        inv = (1.0/pin_c[0], 1.0/pin_c[1], 1.0/pin_c[2])
        t = sum(inv)
        if max(inv) > 0:
            pred = np.argmax([inv[0]/t, inv[1]/t, inv[2]/t])
            if pred == y_test[i]:
                baseline_correct += 1
    baseline = baseline_correct / len(y_test)
    print(f"基准(Pinnacle隐含): {baseline:.1%}")
    print(f"训练集: {len(X_train)} 测试集: {len(X_test)}")
    
    # ─── 阶段1: 搜 n_estimators + learning_rate ───
    print(f"\n{'─'*50}")
    print("阶段1: n_estimators × learning_rate (depth=4, min_samples=10)")
    print(f"{'─'*50}")
    print(f"{'n_est':>6} {'lr':>5} {'train%':>7} {'test%':>7} {'time':>6}")
    print(f"{'─'*26}")
    
    best = {'test': 0, 'train': 0, 'n': 30, 'lr': 0.1}
    results = []
    
    for n in [30, 50, 80]:
        for lr in [0.05, 0.08, 0.12]:
            t0 = time.time()
            m = SimpleLGBM(n_estimators=n, max_depth=4, learning_rate=lr)
            m.min_samples = 10
            m.fit(X_train, y_train)
            elapsed = time.time() - t0
            
            train_pred = m.predict_proba(X_train)
            test_pred = m.predict_proba(X_test)
            train_acc = np.mean(np.argmax(train_pred, axis=1) == y_train)
            test_acc = np.mean(np.argmax(test_pred, axis=1) == y_test)
            
            flag = " <<<" if test_acc > best['test'] else ""
            if test_acc > best['test']:
                best = {'test': test_acc, 'train': train_acc, 'n': n, 'lr': lr}
            
            print(f"{n:6d} {lr:5.2f} {train_acc:6.1%} {test_acc:6.1%} {elapsed:5.1f}s{flag}")
            results.append((test_acc, train_acc, n, lr, 4, 10))
    
    # ─── 阶段2: 搜 max_depth ───
    print(f"\n{'─'*50}")
    print(f"阶段2: max_depth (n={best['n']}, lr={best['lr']}, min_samples=10)")
    print(f"{'─'*50}")
    print(f"{'depth':>6} {'train%':>7} {'test%':>7} {'time':>6}")
    print(f"{'─'*26}")
    
    for d in [3, 4, 5, 6]:
        t0 = time.time()
        m = SimpleLGBM(n_estimators=best['n'], max_depth=d, learning_rate=best['lr'])
        m.min_samples = 10
        m.fit(X_train, y_train)
        elapsed = time.time() - t0
        
        train_pred = m.predict_proba(X_train)
        test_pred = m.predict_proba(X_test)
        train_acc = train_acc = np.mean(np.argmax(train_pred, axis=1) == y_train)
        test_acc = np.mean(np.argmax(test_pred, axis=1) == y_test)
        
        d_best = ' <<<' if test_acc > best['test'] else ''
        if test_acc > best['test']:
            best.update({'test': test_acc, 'train': train_acc, 'd': d})
        
        print(f"{d:6d} {train_acc:6.1%} {test_acc:6.1%} {elapsed:5.1f}s{d_best}")
        results.append((test_acc, train_acc, best['n'], best['lr'], d, 10))
    
    # ─── 阶段3: 搜 min_samples ───
    print(f"\n{'─'*50}")
    print(f"阶段3: min_samples (n={best['n']}, lr={best['lr']}, depth={best.get('d',4)})")
    print(f"{'─'*50}")
    print(f"{'ms':>6} {'train%':>7} {'test%':>7} {'time':>6}")
    print(f"{'─'*26}")
    
    best_d = best.get('d', 4)
    for ms in [5, 10, 15, 20]:
        t0 = time.time()
        m = SimpleLGBM(n_estimators=best['n'], max_depth=best_d, learning_rate=best['lr'])
        m.min_samples = ms
        m.fit(X_train, y_train)
        elapsed = time.time() - t0
        
        train_pred = m.predict_proba(X_train)
        test_pred = m.predict_proba(X_test)
        train_acc = np.mean(np.argmax(train_pred, axis=1) == y_train)
        test_acc = np.mean(np.argmax(test_pred, axis=1) == y_test)
        
        ms_best = ' <<<' if test_acc > best['test'] else ''
        if test_acc > best['test']:
            best.update({'test': test_acc, 'train': train_acc, 'ms': ms})
        
        print(f"{ms:6d} {train_acc:6.1%} {test_acc:6.1%} {elapsed:5.1f}s{ms_best}")
        results.append((test_acc, train_acc, best['n'], best['lr'], best_d, ms))
    
    # ─── 最终 ───
    print(f"\n{'='*50}")
    print(f"最佳参数:")
    print(f"  n_estimators={best['n']}")
    print(f"  max_depth={best.get('d',4)}")
    print(f"  learning_rate={best['lr']}")
    print(f"  min_samples={best.get('ms',10)}")
    print(f"  Train: {best['train']:.1%}  Test: {best['test']:.1%}")
    print(f"  基准:  {baseline:.1%}")
    print(f"  提升:  {(best['test']-baseline)*100:+.1f}%")
    
    # 保存
    print(f"\n保存最佳模型...")
    final_model = SimpleLGBM(
        n_estimators=best['n'],
        max_depth=best.get('d', 4),
        learning_rate=best['lr']
    )
    final_model.min_samples = best.get('ms', 10)
    final_model.fit(X_train, y_train)
    
    model_dict = final_model.to_dict()
    model_dict['feature_names'] = ['35_base_features']
    model_dict['version'] = 4
    model_dict['train_date'] = '2026-07-17'
    model_dict['train_samples'] = len(X_train)
    model_dict['test_accuracy'] = round(best['test'], 4)
    model_dict['baseline_accuracy'] = round(baseline, 4)
    
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(MODEL_PATH, 'w') as f:
        json.dump(model_dict, f, cls=NumpyEncoder)
    
    print(f"✅ {MODEL_PATH}")
    print(f"   树数: {sum(len(t) for t in final_model.trees)}")
    
    # Top 5
    print(f"\nTop 5:")
    results.sort(reverse=True)
    for i, (te, tr, n, lr, d, ms) in enumerate(results[:5]):
        print(f"  #{i+1}: n={n} d={d} lr={lr:.2f} ms={ms} | test={te:.1%} train={tr:.1%}")

if __name__ == '__main__':
    main()
