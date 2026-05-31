# 竞彩足球泊松分布分析系统 - football-dashboard

## 快速开始

### Termux 模式（一条命令完成全部流程）

```bash
cd football-dashboard
python scripts/fetch_data.py --fetch-and-push
```

这将执行：赔率抓取 → 日报生成 → 复盘 → git push → GA自动构建看板

### 完整参数列表

```bash
# 基本用法
python scripts/fetch_data.py --date 2026-05-30       # 指定日期
python scripts/fetch_data.py --yesterday              # 用昨天日期
python scripts/fetch_data.py --today                   # 用今天日期

# 抓取模式
python scripts/fetch_data.py --fetch-only              # 只抓取数据
python scripts/fetch_data.py --fetch-and-push         # 抓取+推送

# 完整流程（含日报和复盘）
python scripts/fetch_data.py --fetch-and-push \
    --with-report \         # 生成日报
    --with-review \         # 执行复盘
    --incremental           # 日报增量模式

# 只构建看板
python scripts/fetch_data.py --build-only
```

## 目录结构

```
football-dashboard/
├── scripts/
│   ├── fetch_data.py          # 主调度脚本
│   ├── daily_report.py        # 日报生成
│   ├── review.py              # 复盘脚本
│   ├── odds_api.py            # 赔率抓取
│   ├── fetch_bsd.py           # BSD赛果抓取
│   ├── align_and_merge.py     # 对齐合并
│   ├── merge_and_build.py     # 看板构建
│   ├── push_db.py             # DB推送
│   ├── fetch_standings_qtx.py # 积分榜抓取
│   └── value_bet.py          # 价值投注计算
├── data/
│   ├── raw/                   # 原始数据
│   ├── cache/                 # 缓存
│   ├── reports/               # 日报输出
│   └── football.db           # 数据库（不提交）
├── docs/                      # GitHub Pages 输出
├── .github/workflows/
│   └── fetch-and-build.yml    # GA 自动构建
├── requirements.txt
└── README.md
```

## GA 工作流

- **触发条件**：
  - push data/raw/ 或 data/reports/ 后自动构建
  - schedule: 北京时间 13:00 和 19:00
  - 手动触发（可选择是否运行日报/复盘）

- **工作流程**：
  1. 下载最新 DB
  2. 抓取赔率数据
  3. 生成日报（可选）
  4. 执行复盘（可选）
  5. 对齐合并数据
  6. 构建看板
  7. 部署到 GitHub Pages
  8. 推送 processed 数据
  9. 上传 DB 到 Release

## 依赖

```
requests>=2.28
beautifulsoup4>=4.12.0
scipy>=1.10.0       # 可选
openpyxl>=3.1.0     # 可选
```

## 数据源

- **赔率**：中国足彩网 zgzcw.com
- **赛果**：500.com
- **积分榜**：球天下 data.qtx.com
