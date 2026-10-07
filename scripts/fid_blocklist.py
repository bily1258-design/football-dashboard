#!/usr/bin/env python3
"""fid_blocklist.py — 「永远拿不到比分」场次的屏蔽名单（fid 级）。

用途: 比分源（titan007/HKJC/北单）本来就不覆盖的赛事（美超、女足、U20、俄盃…），
赛程+赔率抓得到、比分永远空 → 落在库里当永久积压。把它们记进
`data/excluded_fids.json`，在**结果库组装时**直接跳过，重建也不会再灌回来。

名单格式:
{
  "fids": {
    "1358414": {"team": "芝加哥火焰-温哥华白浪", "date": "2026-07-17",
                 "reason": "比分源无覆盖", "added": "2026-10-07"}
  },
  "updated": "2026-10-07"
}

用的时候:
    from fid_blocklist import load_excluded_fids
    blocked = load_excluded_fids()          # set[str]
    matches = [m for m in matches if str(m.get('fid') or '') not in blocked]
"""
import json
import os

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCKLIST_PATH = os.path.join(REPO_DIR, "data", "excluded_fids.json")


def load_blocklist() -> dict:
    """返回完整名单 dict；文件不存在/损坏 → 空名单（绝不因此中断主流程）。"""
    try:
        with open(BLOCKLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"fids": {}}
        data.setdefault("fids", {})
        return data
    except Exception:
        return {"fids": {}}


def load_excluded_fids() -> set:
    """返回被屏蔽的 fid 集合（字符串）。"""
    return set(load_blocklist().get("fids", {}).keys())


def save_blocklist(data: dict) -> None:
    data["fids"] = {str(k): v for k, v in sorted(data.get("fids", {}).items(), key=lambda kv: str(kv[0]))}
    os.makedirs(os.path.dirname(BLOCKLIST_PATH), exist_ok=True)
    with open(BLOCKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    bl = load_blocklist()
    fids = bl.get("fids", {})
    print(f"屏蔽名单: {len(fids)} 条  ({BLOCKLIST_PATH})")
    for fid, info in fids.items():
        print(f"  fid={fid:<9} {info.get('date','')} {info.get('team','')}  ← {info.get('reason','')}")
