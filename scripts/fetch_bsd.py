#!/usr/bin/env python3
"""fetch_bsd.py — 赛果数据获取

数据源：fetch_500com_termux.py（500.com完场+北单）
输出：data/raw/bsd/{YYYYMMDD}.json

Termux模式：调用fetch_500com_termux.py实际抓取，再转存为BSD格式
"""

import os, json, sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
RAW_DIR = os.path.join(REPO_DIR, "data", "raw", "bsd")

# 添加脚本目录到path
sys.path.insert(0, SCRIPT_DIR)


def fetch_all(date_str: str = None) -> dict:
    """获取赛果数据"""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    date_compact = date_str.replace('-', '')
    print(f"📥 BSD 赛果: {date_str}")

    # 尝试用fetch_500com_termux.py实际抓取
    try:
        from fetch_500com_termux import fetch_500com_results
        print("  🔄 调用fetch_500com_termux抓取...")
        results = fetch_500com_results(date_str)

        wanchang = results.get('wanchang', [])
        beidan = results.get('beidan', [])
        jingcai = results.get('jingcai', [])

        output = {
            'date': date_str,
            'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'jingcai': jingcai,
            'wanchang': wanchang,
            'beidan': beidan,
            'summary': {
                'jingcai': len(jingcai),
                'wanchang': len(wanchang),
                'beidan': len(beidan),
                'beidan_with_result': sum(1 for b in beidan if b.get('score'))
            }
        }

        # 保存到raw/bsd
        os.makedirs(RAW_DIR, exist_ok=True)
        out_path = os.path.join(RAW_DIR, f"{date_compact}.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        s = output['summary']
        print(f"  竞彩:{s['jingcai']} 完场:{s['wanchang']} 北单:{s['beidan']}")
        print(f"✅ → {out_path}")
        return output

    except ImportError:
        print("  ⚠️ fetch_500com_termux不可用，尝试加载缓存")
    except Exception as e:
        print(f"  ⚠️ 抓取失败: {e}，尝试加载缓存")

    # fallback: 加载已有缓存
    cache_path = os.path.join(RAW_DIR, f"{date_compact}.json")
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"  📂 已有缓存: {cache_path}")
        s = data.get('summary', {})
        print(f"  竞彩:{s.get('jingcai',0)} 完场:{s.get('wanchang',0)} 北单:{s.get('beidan',0)}")
        return data

    # 无缓存也无抓取能力
    print("  ❌ 无法获取赛果数据")
    output = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'jingcai': [], 'wanchang': [], 'beidan': [],
        'summary': {'jingcai': 0, 'wanchang': 0, 'beidan': 0, 'beidan_with_result': 0}
    }
    os.makedirs(RAW_DIR, exist_ok=True)
    out_path = os.path.join(RAW_DIR, f"{date_compact}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return output


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', type=str, default=None)
    args = p.parse_args()
    fetch_all(args.date)
