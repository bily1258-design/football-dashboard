#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 档离线 A/B 对照: v11(39维) vs v11+阵容阵型攻防(50维)

口径 (2026-09-30 用户拍板):
  · 只在「有阵容子样本」上判断是否提升 —— 该场两队均有历史阵容可算特征(赛前可得)
  · 时间序列切分 (按 date 升序) + 滚动多折扩展窗, 看符号与一致性
  · 缺失一律 -1; 训练/推理同源, 无泄漏 (见 lineup_features.py)
  · 只读生产库; 候选写到 data/cache/, 绝不碰生产 lgbm_model.json

判定用 lightgbm 原生 API (生产是手写 SimpleLGBM, 纯 Python 千行级训练太慢):
  两侧同参数同轮数, 结论看「加特征是否提升」这一相对差异; 真要上生产须用
  train_lgbm 重建同构模型并在同实现下复验 (见 SKILL 换模流程)。

用法:
  python3 scripts/lineup_model_ab.py                  # 出对照报告(分 4 组变体)
  python3 scripts/lineup_model_ab.py --folds 5
  python3 scripts/lineup_model_ab.py --full           # 追加全样本参考口径
  python3 scripts/lineup_model_ab.py --write-candidate
"""
import argparse
import json
import math
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_analysis          # noqa: E402
import train_lgbm           # noqa: E402
from lineup_features import (ATTACK_NAMES, FEATURE_NAMES, LINEUP_NAMES,  # noqa: E402
                             MISSING, LineupFeatureBuilder)

try:
    import lightgbm as lgb
except ImportError:                                     # pragma: no cover
    lgb = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'football.db')
CACHE = os.path.join(BASE, 'data', 'cache')
SEED = 42
N_TREES, DEPTH, LR = 120, 5, 0.05                      # 与生产 v11 同参
_PARAMS = dict(objective='multiclass', num_class=3, learning_rate=LR, num_leaves=31,
               min_data_in_leaf=10, feature_fraction=0.9, bagging_fraction=0.9,
               bagging_freq=1, verbose=-1, seed=SEED, num_threads=4,
               deterministic=True, force_row_wise=True)
BASE_NAMES = list(train_lgbm.FEATURE_NAMES)


def load_scored(conn):
    return conn.execute(
        "SELECT * FROM poisson_predictions "
        "WHERE pinnacle_close_w > 1.01 AND reference_score IS NOT NULL AND reference_score != '' "
        "ORDER BY date, id"
    ).fetchall()


def fid_of(r):
    try:
        return int(r.get('match_id') or 0)
    except (TypeError, ValueError):
        return 0


def build_both(rows, conn, priors, timeline, builder, progress=0):
    """一次遍历同时产出 39 维(A) 与 50 维(B) —— 单遍省一半时间"""
    Xa, Xb, y, meta = [], [], [], []
    n = len(rows)
    for i, row in enumerate(rows, 1):
        r = dict(row)
        label = train_lgbm.get_result_label(r.get('reference_score', ''))
        if label is None:
            continue
        date = r.get('date', '')
        ht, at = r.get('home_team', ''), r.get('away_team', '')
        form = {ht: train_lgbm.get_team_form(timeline, ht, date),
                at: train_lgbm.get_team_form(timeline, at, date)}
        feats = list(train_lgbm.extract_features(r, form_data=form, conn=conn,
                                                 league_priors=priors))
        lf = builder.features(fid_of(r), ht, at, date)
        Xa.append(feats)
        Xb.append(feats + [lf[k] for k in FEATURE_NAMES])
        y.append(label)
        meta.append((r, lf))
        if progress and i % progress == 0:
            print('  已提取 %d/%d' % (i, n), flush=True)
    clean = lambda M: np.nan_to_num(np.array(M, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    return clean(Xa), clean(Xb), np.array(y), meta


def train_eval(Xtr, ytr, Xte, yte):
    ds = lgb.Dataset(np.ascontiguousarray(Xtr), label=ytr)
    booster = lgb.train(_PARAMS, ds, num_boost_round=N_TREES)
    p = booster.predict(np.ascontiguousarray(Xte))
    acc = float(np.mean(np.argmax(p, axis=1) == yte))
    ll = float(-np.mean([math.log(max(p[i][yte[i]], 1e-9)) for i in range(len(yte))]))
    per = [float(np.mean(np.argmax(p[yte == c], axis=1) == c)) if np.any(yte == c) else float('nan')
           for c in range(3)]
    return booster, acc, ll, per, np.argmax(p, axis=1)


def arm_cols(arm_names):
    """变体列索引: 39 维基座 + 指定新增列"""
    cols = list(range(len(BASE_NAMES)))
    cols += [len(BASE_NAMES) + FEATURE_NAMES.index(nm) for nm in arm_names]
    return cols


ATTACK2_NAMES = [nm for nm in ATTACK_NAMES if nm != 'corner_diff_3']   # 角球全缺, 剔

ARMS = [('A v11(39维)', []),
        ('B +11维 全量', FEATURE_NAMES),
        ('C +8维 阵容阵型', LINEUP_NAMES),
        ('D +3维 攻防差', ATTACK_NAMES),
        ('E +2维 射门控球', ATTACK2_NAMES),
        ('F +1维 仅射门差', ['shot_diff_3'])]


def run_folds(X, y, F, min_test=15, min_train=40):
    """滚动扩展窗: 返回每折 (训练n, 测试n, 各变体 acc)"""
    n = len(y)
    out = []
    for k in range(F):
        tr = int(n * (k + 1) / (F + 1))
        te = int(n * (k + 2) / (F + 1)) if k + 1 < F else n
        if te - tr < min_test or tr < min_train:
            continue
        res = {}
        for label, names in ARMS:
            _, acc, ll, _, _ = train_eval(X[np.ix_(range(tr), arm_cols(names))], y[:tr],
                                          X[np.ix_(range(tr, te), arm_cols(names))], y[tr:te])
            res[label] = (acc, ll)
        out.append((tr, te - tr, res))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folds', type=int, default=4, help='滚动折数(扩展窗)')
    ap.add_argument('--full', action='store_true', help='追加全样本参考口径(安全参考, 非判定口径)')
    ap.add_argument('--full-folds', type=int, default=0, help='全样本滚动折数(判攻防差信号, 0=跳过)')
    ap.add_argument('--write-candidate', action='store_true',
                    help='把候选写到 data/cache/ (不动生产)')
    args = ap.parse_args()

    if lgb is None:
        print('需要 lightgbm: pip install lightgbm')
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    priors = ai_analysis.load_league_priors(DB)
    rows = load_scored(conn)
    timeline = train_lgbm.build_team_form_map(rows)
    builder = LineupFeatureBuilder(conn)

    print('=' * 70)
    print('C 档离线 A/B: v11(39维) vs 加阵容阵型攻防(最多+11维)')
    print('=' * 70)
    print('训练样本(有赔率有比分): %d | 阵容历史球队: %d' % (len(rows), len(builder.hist)))

    Xa, Xb, ya, meta = build_both(rows, conn, priors, timeline, builder, progress=4000)

    idx = [i for i, (r, lf) in enumerate(meta)
           if lf['stab_home'] != MISSING and lf['stab_away'] != MISSING]
    print('\n【子样本】双边可算阵容特征: %d 场 (占全部 %.1f%%)'
          % (len(idx), 100.0 * len(idx) / max(1, len(ya))))
    if len(idx) < 80:
        print('子样本太小, 等回填覆盖更多球队的连续场次再跑。')
        return 0
    Xb_s, y_s = Xb[idx], ya[idx]
    dts = [meta[i][0].get('date', '') for i in idx]
    print('  区间 %s → %s' % (dts[0], dts[-1]))
    print('  子样本内 有值比例: ' + ' '.join(
        '%s %.0f%%' % (nm, 100.0 * np.mean(np.array([meta[i][1][nm] for i in idx]) != MISSING))
        for nm in FEATURE_NAMES))

    # ── 单次 80/20 (详版: 各变体) ──
    cut = int(len(y_s) * 0.8)
    print('\n-- 单次切分 前80%%/后20%% (训练%d / 测试%d) --' % (cut, len(y_s) - cut))
    single = {}
    for label, names in ARMS:
        cols = arm_cols(names)
        _, acc, ll, per, pred = train_eval(Xb_s[np.ix_(range(cut), cols)], y_s[:cut],
                                           Xb_s[np.ix_(range(cut, len(y_s)), cols)], y_s[cut:])
        single[label] = (acc, ll, pred)
        print('  %-16s 准确率 %.4f | logloss %.4f | 主/平/客 %.3f/%.3f/%.3f'
              % (label, acc, ll, per[0], per[1], per[2]))
    base_acc = single[ARMS[0][0]][0]
    print('  差异(vs A): ' + ' | '.join(
        '%s %+.1fpp' % (lb, (single[lb][0] - base_acc) * 100) for lb, _ in ARMS[1:]))

    # ── 滚动多折 ──
    F = max(2, args.folds)
    print('\n-- 滚动 %d 折 扩展窗 (每折训练只用过去) --' % F)
    folds = run_folds(Xb_s, y_s, F)
    if folds:
        for lb, _ in ARMS:
            accs = [f[2][lb][0] for f in folds]
            print('  %-16s 均值 %.4f | 各折 %s' % (lb, np.mean(accs),
                  ' '.join('%.3f' % a for a in accs)))
        print('  （合计测试 %d 场 → 1pp ≈ %.1f 场; 小样本对照看符号与一致性）'
              % (sum(f[1] for f in folds), sum(f[1] for f in folds) / 100.0))

    # ── 特征重要度 (全量变体) ──
    cols = arm_cols(FEATURE_NAMES)
    full_booster, _, _, _, _ = train_eval(Xb_s[np.ix_(range(cut), cols)], y_s[:cut],
                                         Xb_s[np.ix_(range(cut, len(y_s)), cols)], y_s[cut:])
    try:
        gains = np.asarray(full_booster.feature_importance(importance_type='gain'), dtype=float)
        names = BASE_NAMES + FEATURE_NAMES
        tot = float(gains.sum()) or 1.0
        print('\n-- B 模型特征重要度 Top12 (gain 占比) --')
        for j in np.argsort(-gains)[:12]:
            print('  %-24s %5.2f%%%s' % (names[j], 100.0 * gains[j] / tot,
                                         '  ←新' if names[j] in FEATURE_NAMES else ''))
        share = 100.0 * sum(g for j, g in enumerate(gains) if names[j] in FEATURE_NAMES) / tot
        print('  新增 11 维合计 gain 占比: %.2f%% (<1%% 说明模型没用上)' % share)
    except Exception as e:                               # noqa: BLE001
        print('  特征重要度不可用: %s' % e)

    # ── 稳定度 ↔ 赛果 (只读关系) ──
    print('\n-- 诊断: 子样本内 稳定度 与 赛果 --')
    for key, tag in (('stab_home', '主队稳定度'), ('stab_away', '客队稳定度')):
        vals = np.array([meta[i][1][key] for i in idx])
        for lo, hi in ((0.0, 0.8), (0.8, 0.9), (0.9, 2.0)):
            m2 = (vals >= lo) & (vals < hi)
            if m2.sum() >= 5:
                sub = y_s[m2]
                print('  %s [%.2f,%.2f) n=%d | 主胜%.0f%% 平%.0f%% 客胜%.0f%%'
                      % (tag, lo, hi, int(m2.sum()), 100 * np.mean(sub == 0),
                         100 * np.mean(sub == 1), 100 * np.mean(sub == 2)))

    if args.full:
        print('\n-- 全样本参考口径(安全参考; 用户口径以子样本为准) --')
        cut2 = int(len(ya) * 0.8)
        for label, names in ARMS:
            cols = arm_cols(names) if names else list(range(len(BASE_NAMES)))
            Xsrc = Xa if not names else Xb
            _, acc, ll, _, _ = train_eval(Xsrc[np.ix_(range(cut2), cols)], ya[:cut2],
                                          Xsrc[np.ix_(range(cut2, len(ya)), cols)], ya[cut2:])
            print('  %-16s %.4f (ll %.4f)' % (label, acc, ll))
        print('  生产 v11 记录值: 0.4995 (注意: 上方 A 也非生产实现, 只能同实现内互比)')

        # 全样本滚动多折: 攻防差覆盖 ~99%, 样本大, 是判断该信号最有力的一档
        if args.full_folds >= 2:
            FF = args.full_folds
            print('\n-- 全样本 滚动 %d 折 扩展窗 (判断攻防差信号; 训练只用过去) --' % FF)
            ffull = run_folds(Xb, ya, FF, min_test=200, min_train=800)
            for label, _ in ARMS:
                accs = [f[2][label][0] for f in ffull]
                lls = [f[2][label][1] for f in ffull]
                wins = sum(1 for f in ffull
                           if f[2][label][0] > f[2][ARMS[0][0]][0] + 1e-9)
                print('  %-16s acc均值 %.4f | 各折 %s | ll均值 %.4f | 胜A %d/%d'
                      % (label, np.mean(accs), ' '.join('%.3f' % a for a in accs),
                         np.mean(lls), wins, len(ffull)))
            tot = sum(f[1] for f in ffull)
            print('  （合计测试 %d 场 → 1pp ≈ %.1f 场）' % (tot, tot / 100.0))

    if args.write_candidate:
        os.makedirs(CACHE, exist_ok=True)
        p_txt = os.path.join(CACHE, 'lgbm_v12_candidate_lgb.txt')
        full_booster.save_model(p_txt)
        p_json = os.path.join(CACHE, 'lgbm_model_v12_candidate.json')
        with open(p_json, 'w') as f:
            json.dump({'version': 12, 'engine': 'lightgbm',
                       'feature_names': BASE_NAMES + FEATURE_NAMES,
                       'subsample_n': int(len(y_s)),
                       'sub_acc_full11': float(single[ARMS[1][0]][0]),
                       'sub_acc_base': float(base_acc),
                       'note': 'C档候选(含阵容/阵型/攻防11维); 未上生产'}, f,
                      ensure_ascii=False, indent=1)
        print('\n候选已写: %s\n          %s (未动生产 lgbm_model.json)' % (p_txt, p_json))
    return 0


if __name__ == '__main__':
    sys.exit(main())
