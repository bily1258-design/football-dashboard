# 竞彩泊松预测看板

基于泊松分布的竞彩足球预测系统看板。

## 架构

```
football-dashboard/
├─ .github/workflows/fetch-and-build.yml  # GitHub Actions
├─ data/
│   ├─ raw/
│   │   ├─ bsd/          # 500.com 原始赛果（BSD主数据源）
│   │   └─ oddsmagnet/   # 足彩网原始赔率（OddsMagnet辅助源）
│   ├─ processed/         # 对齐合并后的数据
│   ├─ results.json       # 前端结构化数据
│   └─ cache/             # 临时缓存
├─ docs/
│   ├─ index.html         # 看板页面
│   ├─ style.css          # 样式
│   └─ script.js          # 前端逻辑
├─ scripts/
│   ├─ fetch_bsd.py       # 500.com赛果爬虫
│   ├─ fetch_oddsmagnet.py # 足彩网赔率爬虫
│   ├─ align_and_merge.py  # raw → processed 对齐合并
│   ├─ merge_and_build.py  # processed → results.json + index.html
│   ├─ fetch_data.py       # 主调度（全流程）
│   └─ utils.py            # 公共工具
├─ requirements.txt
├─ README.md
└─ .gitignore
```

## 数据流

```
BSD(500.com) ──赛果──┐
                      ├─→ raw/ ─→ align_and_merge ─→ processed/ ─→ merge_and_build ─→ results.json + index.html
OddsMagnet(zgzcw)─赔率─┘                              ↑
                                                       │
                                         football.db ──┘（预测数据主轴）
```

**分层策略：raw → processed → results → pages**
- `raw/`: 两源原始数据，按日期存储，不修改
- `processed/`: 以DB预测为主轴，BSD赛果+OM赔率按队名模糊匹配对齐
- `results.json`: 前端消费的聚合数据
- `docs/`: GitHub Pages 发布的静态页面

## 使用

```bash
# 全流程（抓取 + 合并 + 构建）
python scripts/fetch_data.py --date 2026-05-30

# 只抓取
python scripts/fetch_data.py --fetch-only

# 只构建（不抓取）
python scripts/fetch_data.py --build-only

# 单独运行某个步骤
python scripts/fetch_bsd.py --date 2026-05-30
python scripts/fetch_oddsmagnet.py --date 2026-05-30
python scripts/align_and_merge.py --date 2026-05-30
python scripts/merge_and_build.py
```

## 数据源

| 源 | 简称 | 用途 | URL |
|----|------|------|-----|
| 500.com | BSD | 赛果/比分/竞彩开奖 | zx.500.com, live.500.com |
| 足彩网 | OddsMagnet | 百家赔率/Pinnacle/HKJC | plzx.zgzcw.com |

## 看板地址

https://bily1258-design.github.io/football-dashboard/
