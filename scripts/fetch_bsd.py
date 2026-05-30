#!/usr/bin/env python3
"""fetch_bsd.py — 赛果数据获取（BSD主数据源）

输出：data/raw/bsd/{YYYYMMDD}.json
结构：{date, fetch_time, jingcai:[], wanchang:[], beidan:[], summary:{}}

⚠️ 500.com 已启用 EdgeOne WAF，纯 requests 无法绕过
赛果数据来源优先级：
1. 已有缓存文件（云手机/云电脑提前抓好的）
2. 足彩网百家指数页面的完场数据（仅赔率，无比分）
3. 留空等待云手机补充

Termux场景：此脚本主要用于验证/加载已有缓存
"""

import os, re, json
from datetime import datetime
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(REPO_DIR, "data", "raw", "bsd")


def load_existing(date_str: str) -> Optional[Dict]:
    """加载已有缓存文件"""
    path = os.path.join(RAW_DIR, f"{date_str.replace('-','')}.json")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"  📂 已有缓存: {path}")
        return data
    return None


def fetch_all(date_str: str = None) -> Dict:
    """获取赛果数据（Termux模式：仅加载已有缓存）"""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    print(f"📥 BSD 赛果: {date_str}")

    # 尝试加载已有缓存
    existing = load_existing(date_str)
    if existing:
        s = existing.get('summary', {})
        print(f"  竞彩:{s.get('jingcai',0)} 完场:{s.get('wanchang',0)} 北单:{s.get('beidan',0)}")
        return existing

    # 无缓存，生成空结构
    print(f"  ⚠️ 无缓存，500.com被WAF拦截，赛果需从云手机补充")
    output = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'jingcai': [], 'wanchang': [], 'beidan': [],
        'summary': {'jingcai': 0, 'wanchang': 0, 'beidan': 0, 'beidan_with_result': 0}
    }
    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, f"{date_str.replace('-','')}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ → {out_path} (空)")
    return output


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', type=str, default=None)
    args = p.parse_args()
    fetch_all(args.date)
