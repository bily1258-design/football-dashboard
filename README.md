# 竞彩足球泊松分布分析系统 - football-dashboard

## 快速开始

### 日常一条命令（Termux）

```bash
cd ~/football-dashboard && git pull && python scripts/pipeline.py --date $(date +%Y-%m-%d) --db data/football.db
```

流程：git pull（拉云端抓好的raw数据）→ 13步pipeline → 看板构建+推送

> **注意**：赔率数据由云端定时从足彩网抓取并推送到仓库（每天10:00/16:00），Termux只需pull+pipeline，不直接抓取（WAF拦截）。

### pipeline.py 13步流程

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 1 | `predict_from_odds.py` | OM赔率 → 泊松预测 INSERT DB |
| 2 | `fetch_pinnacle_odds.py` | Pinnacle/HKJC/威廉初终盘 UPDATE DB |
| 3 | `calc_lambda.py` | λ反算补缺 |
| 4 | `update_db_fusion.py` | LGBM融合概率 |
| 5 | `value_bet.py --all` | EV重算（tanh软压缩） |
| 6 | `update_db_kelly.py` | 凯利指数重算 |
| 7 | `align_and_merge.py --cleanup-db` | DB去重 |
| 8 | `align_and_merge.py --all` | 对齐合并 → processed/ |
| 9 | `review.py` | 赛果回填 + 命中分析 |
| 10 | `recalibrate_db.py` | 联赛分层 + isotonic校准 + 信心分层 |
| 11 | `merge_and_build.py --db` | 构建 docs/（results.json + index.html） |
| 12 | git push docs/ | 推docs/到仓库（触发GA部署） |
| 13 | `push_db.py` | DB推Release |

### 常用参数

```bash
python scripts/pipeline.py --date 2026-06-27 --db data/football.db          # 完整流程
python scripts/pipeline.py --date 2026-06-27 --db data/football.db --skip-push   # 跳过推送（本地测试）
python scripts/pipeline.py --date 2026-06-27 --db data/football.db --skip-review # 跳过复盘（当日无赛果）
python scripts/pipeline.py --date 2026-06-27 --db data/football.db --skip-predict # 跳过预测（已有预测时）
```

## Termux Cron 配置

```bash
# ① 12:00 football-dashboard pipeline
0 12 * * * cd ~/football-dashboard && git pull --rebase origin main 2>/dev/null && python scripts/pipeline.py --date $(date +\%Y-\%m-\%d) --db data/football.db >> logs/cron_12.log 2>&1

# ② 12:15 football-odds-api 刷新
15 12 * * * cd ~/football-odds-api && git pull 2>/dev/null; curl -s localhost:5000/pinbo_refresh

# ③ 18:00 football-dashboard pipeline
0 18 * * * cd ~/football-dashboard && git pull --rebase origin main 2>/dev/null && python scripts/pipeline.py --date $(date +\%Y-\%m-\%d) --db data/football.db >> logs/cron_18.log 2>&1
```

> 云端10:00/16:00自动抓raw数据推仓库，Termux的12:00/18:00 pipeline只需pull即可拿到数据。

## 目录结构

```
football-dashboard/
├── scripts/
│   ├── pipeline.py                 # 13步全链路入口（日常主力）
│   ├── odds_api.py                 # 足彩网赔率抓取（百家平均+oyzs三合一）
│   ├── fetch_pinnacle_odds.py      # Pinnacle/HKJC/威廉数据处理+入库
│   ├── predict_from_odds.py        # 泊松预测（含联赛分层+isotonic校准+信心分层）
│   ├── calc_lambda.py              # λ补算
│   ├── update_db_fusion.py         # LGBM融合概率
│   ├── value_bet.py                # EV价值投注（tanh软压缩）
│   ├── update_db_kelly.py          # 凯利指数
│   ├── align_and_merge.py          # 对齐合并+DB去重
│   ├── review.py                   # 复盘（赛果回填+命中分析）
│   ├── recalibrate_db.py           # 批量校准（联赛分层+isotonic+信心分层）
│   ├── calibrate_model.py          # 校准参数生成
│   ├── merge_and_build.py          # 看板数据合并+HTML/JSON构建
│   ├── push_db.py                  # DB推送+赛果回填
│   ├── daily_report.py             # 日报生成
│   ├── backfill_from_500com.py     # 500.com历史赛果回填
│   ├── backfill_from_footballdata.py  # football-data.org历史数据回填
│   ├── fundamental_analysis.py     # 基本面分析（daily_report依赖）
│   ├── fusion_predict.py           # 融合预测（update_db_fusion依赖）
│   ├── fetch_zgzcw_results.py      # 足彩网赛果抓取（review依赖）
│   ├── fetch_standings_qtx.py      # 球天下积分榜抓取
│   ├── oddsmagnet_to_realodds.py   # OM赔率格式转换
│   ├── team_aliases.py             # 队名别名归一化
│   ├── tools.py                    # 搜索工具（fundamental_analysis依赖）
│   └── utils.py                    # 工具函数（align_and_merge依赖）
├── data/
│   ├── raw/oddsmagnet/             # 原始赔率数据（足彩网JSON，云端抓取推仓库）
│   ├── cache/                      # 缓存
│   ├── calibration_params.json     # 校准参数（23联赛+isotonic曲线）
│   └── football.db                 # SQLite数据库（不提交git，推Release）
├── docs/                           # GitHub Pages 输出
├── .github/workflows/
│   └── fetch-and-build.yml         # GA 自动构建（只build+deploy）
└── README.md
```

## 数据流

```
足彩网 zgzcw.com（云端抓取）
  │
  ├── oyzs_ajax 三合一 ─────────────┐
  │   Pinnacle/HKJC/利记/明升/威廉   │
  │   1X2欧赔+亚盘+大小球(初盘+即时) │ odds_api.py → raw/*.json
  │                                  │
  ├── GET 百家平均欧赔 ─────────────┤
  ├── POST Betfair ─────────────────┤
  └── POST 百家平均亚盘 ────────────┘
                                     ↓
                         git push → 仓库（Termux pull）
                                     ↓
                         pipeline.py 13步流水线
                                     ↓
                         docs/ → GitHub Pages 看板
```

## GA 工作流

- **触发条件**：push `docs/` 后自动构建
- **GA只做构建**：下载Release DB → merge_and_build → 部署到 GitHub Pages
- **数据抓取在云端完成**：GA不执行fetch/pipeline，Termux也不直接抓取（WAF拦截）

## 关键特性

### 数据源架构（全足彩网）

所有赔率数据均来自**中国足彩网 zgzcw.com**，无第三方API依赖。

| 数据 | 抓取方式 | 说明 |
|------|---------|------|
| 百家平均欧赔 | GET + POST | 竞彩+北单，3天窗口 |
| 百家平均亚盘 | POST company=0 | companyType=y |
| Pinnacle/HKJC/利记/明升/威廉 1X2+AH+OU | **oyzs_ajax 三合一** | odds.zgzcw.com，含初盘+即时盘 |
| Betfair | POST company=56 | companyType=b |
| 赛果 | 500.com | backfill_from_500com.py |
| 积分榜 | 球天下 data.qtx.com | fetch_standings_qtx.py |

> **oyzs_ajax 三合一接口**：`odds.zgzcw.com/odds/oyzs_ajax.action` 一次请求返回 Pinnacle(22)/HKJC(136)/利记(15)/明升(6)/威廉希尔(5) 的1X2欧赔+亚盘+大小球（含初盘open+即时盘close），替代之前的散装POST。

### 校准系统

- **联赛分层**：23个联赛独立参数优化（alpha/beta/poisson_weight/ev_scale）
- **isotonic regression**：20点校准曲线，修正概率偏差
- **信心分层**：high(≥0.6)/medium(0.45-0.6)/low(<0.45)，推荐仅high+medium
- **参数文件**：`data/calibration_params.json`
- **重新校准**：`recalibrate_db.py` 批量更新DB记录

### Pinnacle赔率

- **即时盘(close)**：主行显示，用于1X2对比表和分歧对比
- **初盘(open)**：次行显示，覆盖率仅约2.3%（大部分来源无初盘数据）
- **对beidan来源**：close值与竞彩赔率高度重合（数据源特性，非bug）
- **分歧对比**：竞彩 vs Pinnacle + Pinnacle vs HKJC，均使用即时盘，显示在弹窗底部

### 队名别名归一化

`team_aliases.py` 维护50+队名别名映射，解决跨源队名译名不一致问题。

### 亚盘数据

亚盘列优先级：**Pinnacle → 利记 → 百家平均**

Pinnacle/HKJC/利记/明升/威廉的亚盘+大小球数据通过 oyzs_ajax 三合一接口获取（含初盘+即时盘），compactCompany统一排版。

### Pinnacle 主客 swap 检测

`fetch_pinnacle_odds.py` 的 `save_to_db` 函数会对比 DB 竞彩赔率方向，若 Pinnacle 最低赔对应竞彩最高赔 → 触发 swap，翻转 Pinnacle 全部赔率（1X2 + 亚盘）。

### 异常赔率过滤

Pinnacle 单值 > 30 且其他值 < 10 的记录判定为异常，整条丢弃。

### AH/OU 段 SQL 防冻结

所有 AH/OU 相关 SQL 使用 `COALESCE(new_value, old_value)` 模式，新值有效→更新，新值为空/0→保留旧值。

### EV算法

- tanh软压缩替代硬截断，保留区分度
- 原始EV存 `ev_win_raw` 等列，压缩后存 `ev_win` 等列
- **EV方向**：从ev_win/ev_draw/ev_loss最大值计算（独立于概率方向）
- Pinnacle 1X2 为唯一有效欧赔源（company=106）

### DB赛果回填

`push_db.py` 推送前自动从Release旧DB回填 `actual_outcome` 和 `deviation_analysis`，防止赛果丢失。

## DB结构

- **唯一索引**：`idx_pred_uq ON poisson_predictions(date, home_team, away_team)`
- **校准列**：`confidence_tier(TEXT)`, `calibrated_prob(REAL)`, `best_direction_cn(TEXT)`
- **Pinnacle列**：`pinnacle_open_w/d/l`, `pinnacle_close_w/d/l`（即时盘=close，初盘=open）

## 数据源

- **赔率**：中国足彩网 zgzcw.com（唯一数据源）
- **历史校准数据**：football-data.org（2赛季，13944条）
- **赛果**：500.com
- **积分榜**：球天下 data.qtx.com

## 看板地址

https://bily1258-design.github.io/football-dashboard/

## 变更记录

### 2026-06-26
- **清理废弃文件**：删除9个废弃脚本+3个bak+26个bsd数据+空目录，减90780行
- **pipeline替代fetch_data**：13步全闭环，含recalibrate_db
- **cron改用pipeline.py**：Termux不再直接抓数据，改为git pull + pipeline
- **云端定时抓数据**：每天10:00/16:00从足彩网抓raw数据推仓库
- **Pinnacle排版重构**：compactCompany统一格式，顶部1X2表4行（竞彩/Pinnacle/HKJC/威廉）
- **分歧对比**：竞彩vs Pinnacle + Pinnacle vs HKJC，即时盘，弹窗底部
- **EV方向修复**：独立从ev值计算，不再等于概率方向
- **Pinnacle即时+初盘双行显示**

### 2026-06-25
- **校准系统上线**：联赛分层+isotonic regression+信心分层，推荐命中率63.8%
- **recalibrate_db.py**：批量校准DB记录
- **历史数据回填**：football-data.org 2赛季13944条，用于概率校准

### 2026-06-21
- **移除 api-football 依赖**：删 434 行代码，消除队名匹配/swap方向/脏数据三大问题
- Pinnacle 亚盘改用足彩网 POST（company=106, companyType=y）
- Pinnacle 主客 swap 检测改用 DB 竞彩赔率
- AH/OU 段 SQL 全改 COALESCE 防冻结
- 异常赔率过滤（Pinnacle 单值>30 且其他<10 → 丢弃）
