# 竞彩足球泊松分布分析系统 - football-dashboard

## 快速开始

### 新版 Pipeline（500.com 数据源）

```bash
# 完整流程：补fid → 抓赔率 → 建看板 → 推送
cd ~/football-dashboard && git pull && \
python3 scripts/extract_fids_from_live.py --db data/football.db --date $(date +%Y-%m-%d) -v && \
python3 scripts/fetch_500com_odds.py --db data/football.db --company all --limit 30 && \
python3 scripts/merge_and_build.py --db data/football.db && \
python3 scripts/push_db.py && \
git add -A && git commit -m "data update $(date +%Y-%m-%d)" && git push
```

### 只刷新赔率（最简）

```bash
cd ~/football-dashboard && git pull && \
python3 scripts/fetch_500com_odds.py --db data/football.db --company all --limit 30 && \
python3 scripts/merge_and_build.py --db data/football.db && \
git add -A && git commit -m "odds refresh" && git push
```

### 指定公司（更快）

```bash
# 只要 Bet365(3) + Pinnacle(1055)
python3 scripts/fetch_500com_odds.py --db data/football.db --company 3,1055 --limit 30
```

## 三个核心脚本

| 脚本 | 作用 | 说明 |
|------|------|------|
| `extract_fids_from_live.py` | 从 live.500.com 补 fid_500 | 匹配DB中缺fid的场次，精确→别名→安全模糊三级匹配 |
| `fetch_500com_odds.py` | 从 odds.500.com 抓赔率写DB | Bet365/Pinnacle/利记/明升/威廉，1X2+AH+OU |
| `merge_and_build.py` | 合并数据构建 results.json | 优先 bet365_* 列，fallback hkjc_* 列，输出到 docs/ |

### Pipeline 流程图

```
live.500.com/2h1.php          odds.500.com                    DB
     │                           │                            │
     ▼                           ▼                            │
extract_fids_from_live.py   fetch_500com_odds.py              │
  补 fid_500 ──────┐        写 bet365_* 列 ─────┐            │
                  │                             │            │
                  └──────────┬──────────────────┘            │
                             ▼                               │
                      merge_and_build.py ◄───────────────────┘
                    读 bet365_* 优先 / hkjc_* 兜底
                             │
                             ▼
                      docs/results.json
                             │
                             ▼
                      GitHub Pages 看板
```

## Termux Cron 配置

```bash
crontab -e
```

```
0 12 * * * cd ~/football-dashboard && git pull && python3 scripts/extract_fids_from_live.py --db data/football.db --date $(date +\%Y-\%m-\%d) -v && python3 scripts/fetch_500com_odds.py --db data/football.db --company all --limit 30 && python3 scripts/merge_and_build.py --db data/football.db && python3 scripts/push_db.py && git add -A && git commit -m "cron: data update $(date +\%Y-\%m-\%d)" && git push
0 18 * * * cd ~/football-dashboard && git pull && python3 scripts/extract_fids_from_live.py --db data/football.db --date $(date +\%Y-\%m-\%d) -v && python3 scripts/fetch_500com_odds.py --db data/football.db --company all --limit 30 && python3 scripts/merge_and_build.py --db data/football.db && python3 scripts/push_db.py && git add -A && git commit -m "cron: data update $(date +\%Y-\%m-\%d)" && git push
```

## 目录结构

```
football-dashboard/
├── scripts/
│   ├── extract_fids_from_live.py  # 从500.com补fid（精确+别名+安全模糊匹配）
│   ├── fetch_500com_odds.py       # 500.com赔率抓取（Bet365/Pinnacle/利记/明升/威廉）
│   ├── merge_and_build.py         # 看板数据合并构建（bet365优先hkjc兜底）
│   ├── pipeline.py                # 13步全链路入口（含预测/校准/复盘）
│   ├── odds_api.py                # 足彩网赔率抓取（oyzs三合一+百家平均）
│   ├── fetch_pinnacle_odds.py     # Pinnacle/HKJC数据处理+入库
│   ├── predict_from_odds.py       # 泊松预测
│   ├── calc_lambda.py             # λ补算
│   ├── update_db_fusion.py        # LGBM融合概率
│   ├── value_bet.py               # EV价值投注
│   ├── update_db_kelly.py         # 凯利指数
│   ├── align_and_merge.py         # 对齐合并+DB去重
│   ├── review.py                  # 复盘（赛果回填+命中分析）
│   ├── recalibrate_db.py          # 批量校准
│   ├── push_db.py                 # DB推Release
│   ├── daily_report.py            # 日报生成
│   ├── backfill_from_500com.py    # 500.com批量补缺赛果
│   ├── backfill_from_footballdata.py  # football-data.org历史赔率
│   ├── fundamental_analysis.py    # 基本面分析
│   ├── fusion_predict.py          # 融合预测
│   ├── fetch_zgzcw_results.py     # 足彩网赛果抓取
│   ├── fetch_standings_qtx.py     # 球天下积分榜
│   ├── team_aliases.py            # 队名别名归一化
│   └── utils.py / tools.py        # 工具函数
├── data/
│   ├── raw/                       # 原始赔率数据
│   ├── football.db                # SQLite数据库（推Release，不入git）
│   └── calibration_params.json    # 校准参数
├── docs/                          # GitHub Pages 输出
│   ├── results.json
│   ├── index.html
│   ├── script.js
│   └── style.css
├── .github/workflows/
│   └── fetch-and-build.yml        # GA 自动构建部署
└── README.md
```

## 数据源

### 赔率数据（500.com 优先）

| 数据 | 来源 | 脚本 | 说明 |
|------|------|------|------|
| Bet365 1X2+AH+OU | odds.500.com (cid=3) | fetch_500com_odds.py | 主力数据源，覆盖最广 |
| Pinnacle 1X2+AH+OU | odds.500.com (cid=1055) | fetch_500com_odds.py | EV计算核心 |
| 利记 1X2+AH+OU | odds.500.com (cid=651) | fetch_500com_odds.py | |
| 明升 1X2+AH+OU | odds.500.com (cid=140) | fetch_500com_odds.py | |
| 威廉希尔 1X2+AH+OU | odds.500.com (cid=293) | fetch_500com_odds.py | |
| 竞彩百家平均 | zgzcw.com | odds_api.py | 备用/补充 |
| Betfair | zgzcw.com (cid=56) | odds_api.py | 备用 |

### 赛果与积分

| 数据 | 来源 | 脚本 |
|------|------|------|
| 赛果比分 | 500.com | backfill_from_500com.py |
| 积分榜 | 球天下 data.qtx.com | fetch_standings_qtx.py |
| 历史赔率(校准) | football-data.org | backfill_from_footballdata.py |

## 前端显示标签映射

看板前端显示的 "Bet365" 大小球和亚盘，数据来源为 DB 中 `bet365_*` 列，由 `fetch_500com_odds.py` 写入。`merge_and_build.py` 输出到 results.json 时 key 名保持 `hkjc`/`hkjc_ah`/`hkjc_ou`（前端兼容），数据优先取 `bet365_*` 列。

| 前端标签 | DB列前缀 | 数据来源 |
|----------|---------|---------|
| Bet365 大小球 | bet365_ou_* | fetch_500com_odds.py (cid=3) |
| Bet365 亚盘 | bet365_ah_* | fetch_500com_odds.py (cid=3) |
| Pinnacle | pinnacle_* | fetch_500com_odds.py (cid=1055) |

## 校准系统

- **联赛分层**：23个联赛独立参数优化（alpha/beta/poisson_weight/ev_scale）
- **isotonic regression**：20点校准曲线，修正概率偏差
- **信心分层**：high(≥0.6) / medium(0.45-0.6) / low(<0.45)
- **参数文件**：`data/calibration_params.json`

## GA 工作流

- **触发条件**：push `docs/` 或 `data/raw/` 后自动构建
- **流程**：下载 Release DB → merge_and_build → 部署 GitHub Pages
- **GA 不执行抓取**：数据抓取在 Termux/云端完成

## 看板地址

https://bily1258-design.github.io/football-dashboard/

## 变更记录

### 2026-07-02
- **数据源切换**：Bet365 替代 HKJC，500.com 替代足彩网 oyzs_ajax
- **新 pipeline**：extract_fids_from_live.py → fetch_500com_odds.py → merge_and_build.py
- **extract_fids_from_live.py**：从 live.500.com 补 fid_500，安全模糊匹配+40+别名
- **fetch_500com_odds.py**：Bet365/Pinnacle/利记/明升/威廉 1X2+AH+OU，ALTER TABLE 自动加列
- **merge_and_build.py**：读 bet365_* 优先 hkjc_* 兜底，输出 key 名不变（前端兼容）
- **前端**：标签 HKJC → Bet365，CSS badge 更新，分歧对比改为 Pinnacle vs Bet365
- **DB 双列共存**：hkjc_*（oyzs 写入）+ bet365_*（500.com 写入），bet365 优先

### 2026-06-26
- pipeline 替代 fetch_data，13步全闭环
- 云端定时抓数据推仓库，Termux pull+pipeline
- Pinnacle 排版重构，分歧对比，EV方向修复

### 2026-06-25
- 校准系统上线：联赛分层+isotonic+信心分层
- 历史数据回填：football-data.org 2赛季13944条

### 2026-06-21
- 移除 api-football 依赖
- Pinnacle 亚盘改足彩网 POST，swap 检测改竞彩赔率
- AH/OU SQL 全改 COALESCE 防冻结
