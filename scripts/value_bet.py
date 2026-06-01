#!/usr/bin/env python3
"""价值投注计算模块 V3

【数据源说明】：
- 赔率：中国足彩网 zgzcw.com (fetch_pinnacle_odds.py)，非500.com，非中国竞彩网(sporttery.cn)

核心变更（V2→V3）：
- EV公式改为概率优势法：EV = fusion概率 / 市场隐含概率 - 1
  衡量模型vs市场的概率偏差，不受赔率绝对值和抽水影响
- 隐含概率和EV统一用百家欧赔（同体系，不再HAD/百家混用）
- 冷门风险改为调整fusion概率（间接影响EV），不再直接打折EV
- 添加数据质量守卫：负抽水标记、HAD/百家偏差检测、无fusion降级
- ev_adjust改为调整fusion概率而非EV值

历史问题（V2）：
- 隐含概率(百家) × HAD赔率 体系混用 → EV虚高
- 全负EV系统性偏向冷门方向 → 命中率仅26%
- fusion概率未填充 → 0.4模型权重丢失
- 负抽水/HAD百家偏差大未检测 → 虚高EV
"""

import json


def _safe_float(val, default=0.0):
    """安全转为float，兼容str/int/float/None"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def calc_implied_prob(odds_w, odds_d, odds_l):
    """从赔率计算去抽水隐含概率
    
    Args:
        odds_w/d/l: 胜平负赔率
        
    Returns:
        dict: {'w': prob, 'd': prob, 'l': prob}, margin
    """
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return {'w': 0, 'd': 0, 'l': 0}, 0.0
    
    inv_w = 1.0 / odds_w
    inv_d = 1.0 / odds_d
    inv_l = 1.0 / odds_l
    total = inv_w + inv_d + inv_l
    margin = total - 1.0
    
    return {
        'w': round(inv_w / total, 4),
        'd': round(inv_d / total, 4),
        'l': round(inv_l / total, 4),
    }, round(margin, 4)


def _calc_direction_ev(fusion_prob, implied_prob):
    """概率优势法计算单方向EV
    
    EV = fusion概率 / 市场隐含概率 - 1
    
    含义：模型认为该方向概率比市场高出多少
    - EV > 0: 模型认为概率被市场低估（价值方向）
    - EV < 0: 模型认为概率被市场高估（无价值）
    - EV = 0: 模型与市场一致
    
    优势：
    - 不依赖赔率绝对值，不受高赔率冷门方向放大效应
    - 不受抽水影响（隐含概率已去抽水）
    - 三向EV可比，最高EV方向=模型vs市场分歧最大方向
    """
    if implied_prob <= 0 or fusion_prob <= 0:
        return 0.0
    return fusion_prob / implied_prob - 1


def calculate_value_bet(match_data):
    """计算一场比赛的三向EV，推荐EV最高方向
    
    Args:
        match_data: dict, 包含以下字段：
            - odds_win/draw/loss: 竞彩赔率（用于HAD/百家偏差检测）
            - fusion_win/draw/loss: LGBM融合概率 (0-1)
            - final_win/draw/loss: final概率(0.7泊松+0.3市场)，fusion降级用
            - avg_odds_open_w/d/l: 百家平均欧赔初盘
            - avg_odds_close_w/d/l: 百家平均欧赔最新
            - cold_risk: 冷门风险文本 "低"/"中"/"高" 或数值
            - ev_adjust: 基本面修正因子（调整fusion概率）
            
    Returns:
        dict: {
            ev_win/draw/loss: 三个方向EV（概率优势值）,
            ev_value: 推荐方向EV（最高）,
            best_direction: EV最高方向 win/draw/loss,
            best_direction_cn: 中文方向,
            implied_prob_w/d/l: 隐含概率（百家去抽水）,
            avg_margin: 百家抽水率,
            cold_signals: 冷门/数据质量信号,
            data_quality: 数据质量标记 normal/degraded/abnormal,
        }
    """
    default_result = {
        'ev_win': 0.0, 'ev_draw': 0.0, 'ev_loss': 0.0,
        'ev_value': 0.0,
        'best_direction': 'win',
        'best_direction_cn': '主胜',
        'implied_prob_w': 0, 'implied_prob_d': 0, 'implied_prob_l': 0,
        'avg_margin': 0.0,
        'cold_signals': '',
        'data_quality': 'normal',
    }
    
    # ===== 1. 获取赔率 =====
    
    # 竞彩赔率（用于HAD/百家偏差检测，不参与EV计算）
    had_w = _safe_float(match_data.get('odds_win', 0))
    had_d = _safe_float(match_data.get('odds_draw', 0))
    had_l = _safe_float(match_data.get('odds_loss', 0))
    
    # 百家平均欧赔（EV计算核心数据源）
    avg_close_w = _safe_float(match_data.get('avg_odds_close_w', 0))
    avg_close_d = _safe_float(match_data.get('avg_odds_close_d', 0))
    avg_close_l = _safe_float(match_data.get('avg_odds_close_l', 0))
    avg_open_w = _safe_float(match_data.get('avg_odds_open_w', 0))
    avg_open_d = _safe_float(match_data.get('avg_odds_open_d', 0))
    avg_open_l = _safe_float(match_data.get('avg_odds_open_l', 0))
    
    # Pinnacle/平博赔率（最高优先级数据源，抽水5-8%）
    pin_close_w = _safe_float(match_data.get('pinnacle_close_w', 0))
    pin_close_d = _safe_float(match_data.get('pinnacle_close_d', 0))
    pin_close_l = _safe_float(match_data.get('pinnacle_close_l', 0))
    pin_open_w = _safe_float(match_data.get('pinnacle_open_w', 0))
    pin_open_d = _safe_float(match_data.get('pinnacle_open_d', 0))
    pin_open_l = _safe_float(match_data.get('pinnacle_open_l', 0))
    
    odds_source = match_data.get('odds_source', '')
    
    has_avg_close = avg_close_w > 0 and avg_close_d > 0 and avg_close_l > 0
    has_avg_open = avg_open_w > 0 and avg_open_d > 0 and avg_open_l > 0
    has_pin_close = pin_close_w > 0 and pin_close_d > 0 and pin_close_l > 0
    has_pin_open = pin_open_w > 0 and pin_open_d > 0 and pin_open_l > 0
    has_had = had_w > 0 and had_d > 0 and had_l > 0
    
    if not has_pin_close and not has_pin_open and not has_avg_close and not has_avg_open and not has_had:
        return default_result
    
    # ===== 2. 计算隐含概率（降级链：平博收盘→平博开盘→百家收盘→百家开盘→竞彩） =====
    
    if has_pin_close:
        implied, avg_margin = calc_implied_prob(pin_close_w, pin_close_d, pin_close_l)
    elif has_pin_open:
        implied, avg_margin = calc_implied_prob(pin_open_w, pin_open_d, pin_open_l)
    elif has_avg_close:
        implied, avg_margin = calc_implied_prob(avg_close_w, avg_close_d, avg_close_l)
    elif has_avg_open:
        implied, avg_margin = calc_implied_prob(avg_open_w, avg_open_d, avg_open_l)
    else:
        # 无SB和百家赔率时降级用竞彩赔率（抽水27%，EV不可靠）
        implied, avg_margin = calc_implied_prob(had_w, had_d, had_l)
    
    if implied['w'] <= 0:
        return default_result
    
    # ===== 3. 数据质量检查 =====
    
    quality_signals = []
    data_quality = 'normal'
    
    # 3a. 负抽水 → 赔率异常（可能数据源错误或赔率大反转）
    if avg_margin < -0.02:
        quality_signals.append(f"负抽水{avg_margin:.1%}")
        data_quality = 'abnormal'
    elif avg_margin < 0:
        quality_signals.append(f"零抽水{avg_margin:.1%}")
        data_quality = 'degraded'
    
    # 3b. HAD与SB/百家赔率偏差检测（两者严重不一致时数据可疑）
    if has_had and (has_pin_close or has_pin_open or has_avg_close or has_avg_open):
        ref_w = pin_close_w if has_pin_close else (pin_open_w if has_pin_open else (avg_close_w if has_avg_close else avg_open_w))
        ref_d = pin_close_d if has_pin_close else (pin_open_d if has_pin_open else (avg_close_d if has_avg_close else avg_open_d))
        ref_l = pin_close_l if has_pin_close else (pin_open_l if has_pin_open else (avg_close_l if has_avg_close else avg_open_l))
        
        gap_w = abs(had_w - ref_w) / ref_w * 100 if ref_w > 0 else 0
        gap_d = abs(had_d - ref_d) / ref_d * 100 if ref_d > 0 else 0
        gap_l = abs(had_l - ref_l) / ref_l * 100 if ref_l > 0 else 0
        max_gap = max(gap_w, gap_d, gap_l)
        
        if max_gap > 50:
            quality_signals.append(f"HAD/百家偏差{max_gap:.0f}%")
            data_quality = 'abnormal'
        elif max_gap > 30:
            quality_signals.append(f"HAD/百家偏差{max_gap:.0f}%")
            data_quality = 'degraded' if data_quality == 'normal' else data_quality
    
    # 3c. 无SB和百家赔率，用竞彩降级
    if not has_pin_close and not has_pin_open and not has_avg_close and not has_avg_open:
        quality_signals.append("无百家赔率(竞彩降级)")
        data_quality = 'degraded' if data_quality == 'normal' else data_quality
    
    # ===== 4. 获取模型融合概率 =====
    
    fusion_w = _safe_float(match_data.get('fusion_win', 0))
    fusion_d = _safe_float(match_data.get('fusion_draw', 0))
    fusion_l = _safe_float(match_data.get('fusion_loss', 0))
    
    # 无fusion概率时降级：用final概率（0.7泊松+0.3市场）替代
    if fusion_w <= 0:
        final_w = _safe_float(match_data.get('final_win', 0))
        final_d = _safe_float(match_data.get('final_draw', 0))
        final_l = _safe_float(match_data.get('final_loss', 0))
        if final_w > 0:
            fusion_w, fusion_d, fusion_l = final_w, final_d, final_l
            quality_signals.append("无LGBM(用final降级)")
            data_quality = 'degraded' if data_quality == 'normal' else data_quality
    
    # 仍然没有模型概率 → 纯市场信号（EV全0）
    if fusion_w <= 0:
        quality_signals.append("无模型概率")
        default_result['implied_prob_w'] = implied['w']
        default_result['implied_prob_d'] = implied['d']
        default_result['implied_prob_l'] = implied['l']
        default_result['avg_margin'] = avg_margin
        default_result['cold_signals'] = '|'.join(quality_signals)
        default_result['data_quality'] = 'abnormal'
        return default_result
    
    # ===== 5. 冷门风险 → 调整fusion概率 =====
    
    cold_risk_raw = match_data.get('cold_risk', 0)
    if isinstance(cold_risk_raw, str):
        cold_risk_map = {'高': 0.5, '中': 0.3, '低': 0.1, '': 0}
        cold_risk = cold_risk_map.get(cold_risk_raw, 0)
    else:
        cold_risk = float(cold_risk_raw) if cold_risk_raw else 0
    
    # 高冷门风险：降低热门方向概率，提升冷门方向概率
    # 本质：模型可能高估了热门方向的确定性
    if cold_risk > 0.3:
        probs = {'win': fusion_w, 'draw': fusion_d, 'loss': fusion_l}
        hot_dir = max(probs, key=probs.get)
        cold_dirs = [d for d in probs if d != hot_dir]
        
        # 从热门方向扣除概率，分配给冷门方向
        discount = cold_risk * 0.15  # 高0.5→扣7.5%, 中0.3→扣4.5%
        transfer = probs[hot_dir] * discount
        if hot_dir == 'win':
            fusion_w -= transfer
        elif hot_dir == 'draw':
            fusion_d -= transfer
        else:
            fusion_l -= transfer
        
        for d in cold_dirs:
            if d == 'win':
                fusion_w += transfer / len(cold_dirs)
            elif d == 'draw':
                fusion_d += transfer / len(cold_dirs)
            else:
                fusion_l += transfer / len(cold_dirs)
        
        # 归一化
        total = fusion_w + fusion_d + fusion_l
        if total > 0:
            fusion_w /= total
            fusion_d /= total
            fusion_l /= total
    
    # ===== 6. 基本面修正（调整fusion概率） =====
    
    ev_adjust = _safe_float(match_data.get('ev_adjust', 0))
    if ev_adjust != 0:
        adj_abs = abs(ev_adjust) * 0.05  # 控制影响幅度
        if ev_adjust > 0:
            max_dir = max([('win', fusion_w), ('draw', fusion_d), ('loss', fusion_l)], key=lambda x: x[1])[0]
        else:
            max_dir = max([('win', fusion_w), ('draw', fusion_d), ('loss', fusion_l)], key=lambda x: x[1])[0]
        
        if max_dir == 'win':
            fusion_w += adj_abs if ev_adjust > 0 else -adj_abs
        elif max_dir == 'draw':
            fusion_d += adj_abs if ev_adjust > 0 else -adj_abs
        else:
            fusion_l += adj_abs if ev_adjust > 0 else -adj_abs
        
        # 归一化
        total = fusion_w + fusion_d + fusion_l
        if total > 0:
            fusion_w /= total
            fusion_d /= total
            fusion_l /= total
    
    # ===== 7. 计算三向EV（概率优势法） =====
    
    ev_win = _calc_direction_ev(fusion_w, implied['w'])
    ev_draw = _calc_direction_ev(fusion_d, implied['d'])
    ev_loss = _calc_direction_ev(fusion_l, implied['l'])
    
    # EV异常值钳制：|EV| > 50% 标记为模型偏差
    # 正常EV范围 -10% ~ +20%，超过50%说明模型概率严重偏离市场
    EV_ABS_CAP = 0.30  # 30%，超过说明模型概率严重偏离市场
    for ev_name, ev_val in [('ev_win', ev_win), ('ev_draw', ev_draw), ('ev_loss', ev_loss)]:
        if abs(ev_val) > EV_ABS_CAP:
            quality_signals.append(f'|{ev_name}|={ev_val:.0%}>30%模型偏差')
            data_quality = 'abnormal'
    # 钳制EV值到[-50%, 50%]范围，避免极端值污染推荐
    ev_win = max(-EV_ABS_CAP, min(EV_ABS_CAP, ev_win))
    ev_draw = max(-EV_ABS_CAP, min(EV_ABS_CAP, ev_draw))
    ev_loss = max(-EV_ABS_CAP, min(EV_ABS_CAP, ev_loss))
    
    # ===== 8. 推荐方向 =====
    # 推荐方向逻辑：
    # 1. 模型偏差场次（|EV|>30%或数据质量异常）→ 用概率最高方向（EV不可信）
    # 2. 降级场次（无平博赔率，竞彩27%抽水）→ EV最低即冷门方向
    # 3. 正常场次 → EV最高方向（平博抽水低，EV可信）
    
    evs = {'win': ev_win, 'draw': ev_draw, 'loss': ev_loss}
    dir_cn = {'win': '主胜', 'draw': '平局', 'loss': '客胜'}
    probs = {'win': fusion_w, 'draw': fusion_d, 'loss': fusion_l}
    
    is_degraded_odds = not has_pin_close and not has_pin_open
    is_model_biased = data_quality == 'abnormal'
    
    if is_model_biased:
        # 模型偏差：EV不可信，直接用概率最高方向
        best_dir = max(probs, key=probs.get)
        cold_signals_parts = list(quality_signals)  # 预初始化
        cold_signals_parts.append('EV不可信(模型偏差)→概率推荐')
    elif is_degraded_odds:
        best_dir = min(evs, key=evs.get)
    else:
        best_dir = max(evs, key=evs.get)
    
    # ===== 9. 生成信号说明 =====
    
    if not is_model_biased:
        cold_signals_parts = list(quality_signals)
    
    if cold_risk >= 0.3:
        cold_signals_parts.append(f"冷门风险{'高' if cold_risk >= 0.5 else '中'}")
    
    # 模型与市场分歧检测
    if fusion_w > 0 and implied['w'] > 0:
        max_diff = max(abs(fusion_w - implied['w']), 
                       abs(fusion_d - implied['d']), 
                       abs(fusion_l - implied['l']))
        if max_diff > 0.15:
            cold_signals_parts.append(f"模型市场分歧{max_diff:.0%}")
    
    # 推荐方向的概率优势说明
    best_ev = evs[best_dir]
    if is_model_biased:
        cold_signals_parts.append(f"模型偏差(概率推荐)")
    elif is_degraded_odds:
        cold_signals_parts.append(f"降级冷门(无平博)")
    elif best_ev > 0.10:
        cold_signals_parts.append(f"模型高估{best_ev:.0%}")
    elif best_ev > 0:
        cold_signals_parts.append(f"模型微估{best_ev:.0%}")
    
    return {
        'ev_win': round(ev_win, 4),
        'ev_draw': round(ev_draw, 4),
        'ev_loss': round(ev_loss, 4),
        'ev_value': round(evs[best_dir], 4),
        'best_direction': best_dir,
        'best_direction_cn': dir_cn[best_dir],
        'implied_prob_w': implied['w'],
        'implied_prob_d': implied['d'],
        'implied_prob_l': implied['l'],
        'avg_margin': avg_margin,
        'cold_signals': '|'.join(cold_signals_parts),
        'data_quality': data_quality,
    }


def batch_calculate(db_path, date_str=None):
    """批量计算数据库中记录的三向EV"""
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if date_str:
        cursor.execute(
            "SELECT * FROM poisson_predictions WHERE date = ?",
            (date_str,)
        )
    else:
        cursor.execute(
            "SELECT * FROM poisson_predictions WHERE ev_value = 0"
        )
    
    rows = cursor.fetchall()
    print(f"[INFO] 找到 {len(rows)} 条记录")
    
    updated = 0
    for row in rows:
        match_data = dict(row)
        result = calculate_value_bet(match_data)
        
        cursor.execute("""
            UPDATE poisson_predictions SET
                ev_win = ?, ev_draw = ?, ev_loss = ?,
                ev_value = ?, best_direction = ?, best_direction_cn = ?,
                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                avg_margin = ?, cold_signals = ?
            WHERE id = ?
        """, (result['ev_win'], result['ev_draw'], result['ev_loss'],
              result['ev_value'], result['best_direction'], result['best_direction_cn'],
              result['implied_prob_w'], result['implied_prob_d'], result['implied_prob_l'],
              result['avg_margin'], result['cold_signals'], row['id']))
        updated += 1
    
    conn.commit()
    conn.close()
    print(f"[INFO] 更新 {updated} 条记录")
    return updated


def recalculate_all(db_path):
    """重算所有记录的三向EV（用于公式变更后全量刷新）"""
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM poisson_predictions")
    rows = cursor.fetchall()
    print(f"[INFO] 全量重算 {len(rows)} 条记录")
    
    updated = 0
    for row in rows:
        match_data = dict(row)
        result = calculate_value_bet(match_data)
        
        cursor.execute("""
            UPDATE poisson_predictions SET
                ev_win = ?, ev_draw = ?, ev_loss = ?,
                ev_value = ?, best_direction = ?, best_direction_cn = ?,
                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                avg_margin = ?, cold_signals = ?
            WHERE id = ?
        """, (result['ev_win'], result['ev_draw'], result['ev_loss'],
              result['ev_value'], result['best_direction'], result['best_direction_cn'],
              result['implied_prob_w'], result['implied_prob_d'], result['implied_prob_l'],
              result['avg_margin'], result['cold_signals'], row['id']))
        updated += 1
    
    conn.commit()
    conn.close()
    print(f"[INFO] 更新 {updated} 条记录")
    return updated


def print_value_summary(db_path, date_str=None):
    """打印价值投注摘要"""
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    if date_str:
        cursor.execute("""
            SELECT home_team, away_team, best_direction_cn, 
                   odds_win, odds_draw, odds_loss,
                   ev_win, ev_draw, ev_loss, ev_value,
                   implied_prob_w, implied_prob_d, implied_prob_l,
                   cold_signals
            FROM poisson_predictions 
            WHERE date = ? AND ev_value > 0 
            ORDER BY ev_value DESC
        """, (date_str,))
    else:
        cursor.execute("""
            SELECT home_team, away_team, best_direction_cn,
                   odds_win, odds_draw, odds_loss,
                   ev_win, ev_draw, ev_loss, ev_value,
                   implied_prob_w, implied_prob_d, implied_prob_l,
                   cold_signals
            FROM poisson_predictions 
            WHERE ev_value > 0 
            ORDER BY ev_value DESC LIMIT 20
        """)
    
    value_matches = cursor.fetchall()
    if value_matches:
        print(f"\n价值场次 (EV>0):")
        for m in value_matches:
            print(f"  💎 {m[0]} vs {m[1]} | 推荐{m[2]} | "
                  f"EV: 主{m[6]:.1%}/平{m[7]:.1%}/客{m[8]:.1%} | "
                  f"最佳EV={m[9]:.1%} | "
                  f"赔率{m[3]:.2f}/{m[4]:.2f}/{m[5]:.2f} | "
                  f"隐含{m[10]:.0%}/{m[11]:.0%}/{m[12]:.0%}"
                  f"{' | ' + m[13] if m[13] else ''}")
    else:
        print("\n无EV正的场次")
    
    conn.close()


if __name__ == '__main__':
    import sys, os, argparse
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
    
    parser = argparse.ArgumentParser(description='价值投注EV计算 V3')
    parser.add_argument('--all', action='store_true', help='全量重算所有记录')
    parser.add_argument('--date', type=str, help='只重算指定日期的记录（YYYY-MM-DD）')
    args = parser.parse_args()
    
    DB = os.path.join(base_dir, "data/shared_state/football.db")
    
    if args.all:
        print("\n=== 全量重算（football.db）===")
        recalculate_all(DB)
        print_value_summary(DB)
    elif args.date:
        print(f"\n=== {args.date} ===")
        batch_calculate(DB, args.date)
        print_value_summary(DB, args.date)
    else:
        # 默认：只算ev_value=0的记录
        print("\n=== 增量更新（football.db）===")
        batch_calculate(DB)
        print_value_summary(DB)
