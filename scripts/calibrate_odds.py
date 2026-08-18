#!/usr/bin/env python3
"""赔率校准公式模块（纯 numpy，无 sklearn 依赖）

三层校准流水线:
  1. 去水 (margin removal):    q_i = (1/odds_i);  p_i = q_i / Σq
  2. Platt 校准 (probability calibration):
       logit(p') = a + b·logit(p)
       a, b 通过历史赛果用逻辑回归拟合（每类独立 OvR）
  3. 偏移计算 (odds movement):
       Δp = p'_close - p'_open   (校准后概率空间偏移)
       Δmargin = margin_close - margin_open

用法:
  from calibrate_odds import implied, fit_platt, platt_calibrate, Calibrator
  cal = Calibrator()
  cal.fit(probs_3xN, labels_N)       # 训练: 每类独立拟合 a,b
  p_cal = cal.apply(p_raw)           # 推理: 三类校准 + 归一化
"""
import numpy as np


def implied(odds_w, odds_d, odds_l):
    """去水: 隐含概率 + 边际 (margin)。
    返回 (p_w, p_d, p_l, margin)。
    """
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    inv = (1.0 / odds_w, 1.0 / odds_d, 1.0 / odds_l)
    t = sum(inv)
    if t <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    return (inv[0] / t, inv[1] / t, inv[2] / t, t - 1.0)


def logit(p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1.0 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))


def _fit_binary_logistic(X, y, iters=200, lr=0.1, l2=0.01):
    """二分类逻辑回归拟合 (a, b): P(y=1) = sigmoid(a + b·X)。
    X = logit(p), y ∈ {0,1}。返回 (a, b)。
    """
    X = np.asarray(X, dtype=np.float64).reshape(-1)
    y = np.asarray(y, dtype=np.float64).reshape(-1)
    mask = ~np.isnan(X)
    X, y = X[mask], y[mask]
    n = len(X)
    if n < 20:
        return (0.0, 1.0)  # 样本不足，退化为恒等校准
    # 梯度下降（牛顿法会因病态矩阵不稳定，用带 L2 的梯度下降）
    a, b = 0.0, 1.0
    Xm = X - X.mean()  # 中心化加速收敛
    yb = (y == 1).astype(np.float64)
    for _ in range(iters):
        z = a + b * Xm
        s = sigmoid(z)
        grad_a = np.mean(s - yb) + l2 * a / n
        grad_b = np.mean((s - yb) * Xm) + l2 * b / n
        a -= lr * grad_a
        b -= lr * grad_b
    # 反中心化修正: logit(p') = a' + b·logit(p)
    a_prime = a - b * X.mean()
    return (float(a_prime), float(b))


def fit_platt(probs, labels):
    """拟合三类 Platt 校准器。
    probs: (N, 3) 去水后隐含概率
    labels: (N,) 0=主胜 1=平 2=客胜
    返回 Calibrator 实例。
    """
    probs = np.asarray(probs, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.int64)
    params = []
    for k in range(3):
        X = logit(probs[:, k])
        y = (labels == k).astype(np.float64)
        params.append(_fit_binary_logistic(X, y))
    return Calibrator(params)


class Calibrator:
    """三结果 Platt 校准器。params = [(a_w,b_w), (a_d,b_d), (a_l,b_l)]"""

    def __init__(self, params=None):
        self.params = params or [(0.0, 1.0)] * 3

    def apply(self, p_w, p_d, p_l):
        """对单场三类概率校准（输入为去水后概率，输出校准概率，总和≈1）"""
        raw = np.array([p_w, p_d, p_l], dtype=np.float64)
        raw = np.clip(raw, 1e-9, 1.0)
        cal = np.zeros(3)
        for k in range(3):
            a, b = self.params[k]
            cal[k] = sigmoid(a + b * logit(raw[k]))
        s = cal.sum()
        if s > 0:
            cal /= s
        return tuple(float(x) for x in cal)

    def apply_open_close(self, open_p, close_p):
        """open_p/close_p: (p_w, p_d, p_l) 去水概率元组。
        返回 (open_cal, close_cal, delta): delta = close_cal - open_cal (概率空间偏移)
        """
        oc = self.apply(*open_p)
        cc = self.apply(*close_p)
        return oc, cc, tuple(cc[i] - oc[i] for i in range(3))

    def to_dict(self):
        return {'params': self.params, 'type': 'platt'}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get('params'))


if __name__ == '__main__':
    # 自测: 合成数据验证校准把过自信概率拉向真实频率
    rng = np.random.default_rng(42)
    n = 2000
    true_p = rng.beta(2, 2, size=n)
    # 模拟过自信: 预测概率 = true_p^1.4 归一化后
    noisy = np.clip(true_p ** 1.4, 0.01, 0.99)
    y = (rng.random(n) < true_p).astype(int)
    a, b = _fit_binary_logistic(logit(noisy), y)
    print(f'自测: 拟合 (a={a:.3f}, b={b:.3f}) — 过自信应得 b<1: {b < 1}')
    # 校准后 Brier 分数对比
    p_cal = sigmoid(a + b * logit(noisy))
    brier_raw = np.mean((noisy - y) ** 2)
    brier_cal = np.mean((p_cal - y) ** 2)
    print(f'Brier 原始 {brier_raw:.4f} -> 校准后 {brier_cal:.4f} (应下降)')
