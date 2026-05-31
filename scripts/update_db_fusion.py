#!/usr/bin/env python3
"""
更新数据库中的LGBM和融合概率字段
使用新的0.3P+0.7L权重重新计算融合概率
"""

import sqlite3
import sys
import os

# 添加fusion_predict模块路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from fusion_predict import FusionPredictor

DB = os.path.join(SCRIPT_DIR, "data", "shared_state", "football.db")


def ensure_columns(cursor):
    """确保数据库表有必要的列"""
    cursor.execute("PRAGMA table_info(poisson_predictions)")
    cols = [r[1] for r in cursor.fetchall()]
    
    new_cols = ['lgb_win', 'lgb_draw', 'lgb_loss', 'fusion_win', 'fusion_draw', 'fusion_loss']
    for col in new_cols:
        if col not in cols:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} REAL")
            print(f"  + 添加列: {col}")
    
    return cols


def update_db(db_path, predictor):
    """更新单个数据库"""
    print(f"\n📊 处理数据库: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"  ⚠️ 数据库不存在，跳过")
        return 0
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 确保列存在
    ensure_columns(cursor)
    
    # 获取所有记录
    cursor.execute("SELECT * FROM poisson_predictions")
    rows = cursor.fetchall()
    total = len(rows)
    
    print(f"  总记录数: {total}")
    
    updated = 0
    errors = 0
    
    for row in rows:
        try:
            result = predictor.predict(row)
            
            # 获取LGBM概率（从details中）
            lgb_prob = result['details']['lgb_prob']
            # 获取融合概率（就是prob字段）
            fusion_prob = result['prob']
            
            # 更新数据库
            cursor.execute("""
                UPDATE poisson_predictions 
                SET lgb_win=?, lgb_draw=?, lgb_loss=?,
                    fusion_win=?, fusion_draw=?, fusion_loss=?
                WHERE id=?
            """, (
                float(lgb_prob[0]), float(lgb_prob[1]), float(lgb_prob[2]),
                float(fusion_prob[0]), float(fusion_prob[1]), float(fusion_prob[2]),
                row['id']
            ))
            updated += 1
            
            if updated % 20 == 0:
                print(f"  已更新: {updated}/{total}")
                
        except Exception as e:
            errors += 1
            if errors <= 5:
                match_id = row['match_id'] if 'match_id' in row.keys() else '?'
                print(f"  错误: {match_id} - {str(e)[:50]}")
    
    conn.commit()
    conn.close()
    
    print(f"  ✅ 更新完成: {updated} 条 (错误: {errors})")
    return updated


def main():
    print("=" * 60)
    print("LGBM/融合概率数据库更新")
    print(f"融合权重: 0.3P + 0.7L")
    print("=" * 60)
    
    try:
        predictor = FusionPredictor()
        print("✅ 模型加载成功")
        print(f"   泊松权重: {predictor.w_poisson}")
        print(f"   LGBM权重: {predictor.w_lgb}")
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        return
    
    total_updated = 0
    total_updated += update_db(DB, predictor)
    
    print("\n" + "=" * 60)
    print(f"🎉 全部完成! 共更新 {total_updated} 条记录")
    print("=" * 60)


if __name__ == "__main__":
    main()
