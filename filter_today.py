#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 today_picks.md 筛选今日(08-28)赛事, 按①②③档分组并标注避雷"""
import re

path = "/data/data/com.termux/files/home/football-dashboard/docs/today_picks.md"
text = open(path, encoding="utf-8").read()

# 按档位切块
sections = {}
for m in re.finditer(r'^([①②③])[^\n]*?\n', text, re.M):
    # 找到该档的开始行号
    pass

lines = text.splitlines()

# 档位块边界: 找 "①高置信", "②三方", "③三方", "避雷汇总"
cur = None
entries = {"①": [], "②": [], "③": []}
for i, line in enumerate(lines):
    if line.startswith("①"):
        cur = "①"
        continue
    if line.startswith("②"):
        cur = "②"
        continue
    if line.startswith("③"):
        cur = "③"
        continue
    if "避雷汇总" in line:
        cur = None
        continue
    if cur is None:
        continue
    m = re.match(r'^(\d{2}-\d{2}) (\d{2}:\d{2}) \[([^\]]+)\] (.+?)(?:\s*→([主平客])|\s*★|\s*⚠|\s*$)', line)
    if m:
        entries[cur].append({
            "date": m.group(1), "time": m.group(2), "league": m.group(3),
            "match": m.group(4), "dir": m.group(5) or ("客" if cur == "②" else "主"),
            "line": i, "flag": "🚫避雷" if "🚫" in line else "",
            "star": "★" in line,
        })

# 补全每条明细: 读后续行取概率
for k in entries:
    for e in entries[k]:
        e["detail"] = []
        for j in range(e["line"] + 1, min(e["line"] + 3, len(lines))):
            s = lines[j].strip()
            if s and not s.startswith("===") and not re.match(r'^\d{2}-\d{2} ', s):
                e["detail"].append(s)
            else:
                break

for k in ["①", "②", "③"]:
    todays = [e for e in entries[k] if e["date"] == "08-28"]
    print(f"===== {k} 档 今日({len(todays)}场) =====")
    for e in todays:
        print(f"{e['time']} [{e['league']}] {e['match']} →{e['dir']} {e['flag']} {('★' if e['star'] else '')}")
        for d in e["detail"]:
            print(f"    {d}")
    print()

# 避雷汇总里的今日场次
print("===== 避雷汇总 · 今日场次 =====")
in_sec = False
for line in lines:
    if "避雷汇总" in line:
        in_sec = True
        continue
    if in_sec:
        m = re.match(r'^\s*(\d{2}-\d{2}) (\d{2}:\d{2})', line)
        if m and m.group(1) == "08-28":
            print(line.strip())
        elif m and m.group(1) != "08-28":
            break
