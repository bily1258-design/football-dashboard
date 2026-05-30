# 竞彩泊松预测看板

基于泊松分布的竞彩足球预测系统看板。

## 架构

```
football-dashboard/
├─ .github/workflows/fetch-and-build.yml  # GitHub Actions（自动构建）
├─ data/
│   ├─ raw/
│   │   ├─ bsd/          # 500.com 原始赛果（BSD主数据源）
│   │   └─ oddsmagnet/   # 足彩网原始赔率（OddsMagnet辅助源）
│   ├─ processed/         # 对齐合并后的数据
│   ├─ results.json       # 前端结构化数据
│   └─ cache/             # 临时缓存
├─ docs/
│   ├─ index.html         # 看板页面
│   ├─ data/results.json  # 前端数据（Pages发布）
│   ├─ style.css          # 样式
│   └─ script.js          # 前端逻辑
├─ scripts/
│   ├─ fetch_bsd.py        # 500.com赛果爬虫
│   ├─ fetch_oddsmagnet.py # 足彩网赔率爬虫
│   ├─ align_and_merge.py  # raw → processed 对齐合并
│   ├─ merge_and_build.py  # processed → results.json + index.html
│   ├─ fetch_data.py       # 主调度
│   ├─ push_db.py          # 推送DB到GitHub Release
│   └─ utils.py            # 公共工具
├─ requirements.txt
├─ README.md
└─ .gitignore
```

## 分工

| 平台 | 职责 | 命令 |
|------|------|------|
| **Termux** | 抓取 raw 数据 → git push | `python scripts/fetch_data.py --fetch-and-push` |
| **云电脑** | 预测 → 复盘 → push DB + 看板 | `python review.py --date YYYY-MM-DD` |
| **GitHub Actions** | pull DB → align → build → 部署 | 自动触发（push/schedule） |

## 数据流

```
┌─────────┐   fetch raw    ┌──────────┐   git push    ┌─────────────────┐
│  Termux  │ ─────────────→ │ data/raw │ ────────────→ │ GitHub Repo     │
└─────────┘                 └──────────┘               └────────┬────────┘
                                                                │ trigger
┌─────────┐   predict       ┌──────────┐   push DB    ┌────────▼────────┐
│ 云电脑   │ ─────────────→ │football.db│ ───────────→ │ GitHub Release  │
└─────────┘                 └──────────┘               └────────┬────────┘
                                                                │ download
                                                     ┌──────────▼──────────┐
                                                     │  GitHub Actions      │
                                                     │  1. gh release download football.db
                                                     │  2. align_and_merge  │
                                                     │  3. merge_and_build  │
                                                     │  4. deploy → Pages   │
                                                     └─────────────────────┘
```

**分层：raw → processed → results → pages**

## Termux 使用

```bash
# 首次设置
pkg install python git
pip install requests
git clone https://github.com/bily1258-design/football-dashboard.git
cd football-dashboard

# 每日抓取 + 推送
python scripts/fetch_data.py --fetch-and-push --today

# 只抓取不推送
python scripts/fetch_data.py --fetch-only --today

# 指定日期
python scripts/fetch_data.py --fetch-and-push --date 2026-05-30
```

## 云电脑使用

```bash
# 复盘 + 自动推送看板和DB
cd 竞彩足球泊松分布分析清单
python review.py --date 2026-05-30
```

## 数据源

| 源 | 简称 | 用途 | 脚本 |
|----|------|------|------|
| 500.com | BSD | 赛果/比分/竞彩开奖 | `fetch_bsd.py` |
| 足彩网 | OddsMagnet | 百家赔率/Pinnacle/HKJC | `fetch_oddsmagnet.py` |

## 看板地址

https://bily1258-design.github.io/football-dashboard/
