# 竞彩足球泊松分布分析系统 - football-dashboard

## 快速开始

### 日常一条命令（Termux）

```bash
fd
# 等价于: cd ~/football-dashboard && git pull && git checkout -- data/raw/ && python scripts/fetch_data.py --fetch-and-push --with-report --with-review --today
```

完整流程：赔率抓取 → Pinnacle亚盘 → 泊松计算 → 日报 → 复盘 → push DB → git push → GA自动构建看板

### fetch_data.py 常用参数

```bash
# 基本用法
python scripts/fetch_data.py --today              # 今天数据
python scripts/fetch_data.py --yesterday           # 昨天数据
python scripts/fetch_data.py --date 2026-06-15     # 指定日期

# 抓取模式
python scripts/fetch_data.py --fetch-only           # 只抓取（Termux用）
python scripts/fetch_data.py --fetch-and-push       # 抓取+推送（默认）

# 完整流程
python scripts/fetch_data.py --fetch-and-push --with-report --with-review --today
```

## 目录结构

```
football-dashboard/
├── scripts/
│   ├── fetch_data.py          # 主调度脚本（日常入口）
│   ├── odds_api.py            # 足彩网赔率抓取（百家平均+Pinnacle+亚盘+大小球）
│   ├── fetch_pinnacle_odds.py # 足彩网Pinnacle数据处理+入库
│   ├── predict_from_odds.py   # 泊松预测
│   ├── calc_lambda.py         # λ补算
│   ├── fusion_predict.py      # 融合预测
│   ├── value_bet.py           # EV价值投注（tanh软压缩）
│   ├── merge_and_build.py     # 看板数据合并+构建
│   ├── align_and_merge.py     # 对齐合并
│   ├── push_db.py             # DB推送+赛果回填
│   ├── team_aliases.py        # 队名别名归一化
│   ├── daily_report.py        # 日报生成
│   ├── review.py              # 复盘脚本
│   └── ...                    # 其他辅助脚本
├── data/
│   ├── raw/                   # 原始数据（足彩网JSON）
│   ├── processed/             # 处理后数据（JSON/天）
│   ├── cache/                 # 缓存
│   ├── reports/               # 日报输出
│   └── football.db            # SQLite数据库（不提交git）
├── docs/                      # GitHub Pages 输出
├── .github/workflows/
│   └── fetch-and-build.yml    # GA 自动构建
└── README.md
```

## 数据流

```
足彩网 zgzcw.com
  │
  ├── GET 百家平均欧赔 ──────────────┐
  ├── POST Pinnacle 1X2 (company=106) ──┤
  ├── POST Pinnacle 亚盘 (company=106) ─┤
  ├── POST 百家平均亚盘 (company=0)  ──┤ odds_api.py → raw/*.json
  ├── POST 利记亚盘 (company=15)  ────┤
  ├── POST 明升亚盘 (company=6)  ─────┤
  └── POST Betfair (company=56)  ─────┘
                                       ↓
                          fetch_pinnacle_odds.py
                          （解析+入库到 football.db）
                                       ↓
                          pipeline.py 8步流水线
                                       ↓
                          align_and_merge + merge_and_build
                                       ↓
                          docs/ → GitHub Pages 看板
```

### fetch_data.py 调度步骤

| 步骤 | 函数 | 说明 |
|------|------|------|
| Step 1 | `step_fetch` | 足彩网赔率抓取（odds_api.py） |
| Step 2 | `step_fetch_pinnacle` | Pinnacle数据处理+入库 |
| Step 3 | `step_update_ah` | 亚盘数据入库 |
| Step 4 | `step_update_db` | DB字段更新 |
| Step 5 | `step_predict` | 泊松预测 |
| Step 6 | `step_daily_report` | 日报生成（可选 `--with-report`） |
| Step 7 | `step_review` | 复盘（可选 `--with-review`） |
| Step 8 | `step_align` + `step_build` | 对齐+看板构建 |
| Step 9 | `step_push_db` | 推送DB到GitHub Release |

## Pipeline 8步流程

`pipeline.py` 内部按序执行：

1. `predict_from_odds` — 泊松预测
2. `calc_lambda` — λ反算补缺
3. `fusion_predict` — 融合预测
4. `value_bet` — EV计算（tanh软压缩，scale=0.50）
5. `update_db_kelly` — 凯利指数
6. `cleanup_db` — 清理重复/无效记录
7. `align_and_merge` — 对齐合并
8. `push_db` — 推送DB到Release（含赛果回填）

## GA 工作流

- **触发条件**：
  - push `data/raw/`、`data/reports/`、`data/processed/` 后自动构建
  - 代码变更需手动触发：`gh workflow run "Build Dashboard"`
  - schedule: 北京时间 13:00 和 19:00

- **GA只做构建**：下载Release DB → align → merge_and_build → 部署到 GitHub Pages
- **数据抓取在Termux本地完成**：GA不执行fetch/pipeline，避免WAF拦截和数据不完整

## 关键特性

### 数据源架构（全足彩网）

所有赔率数据均来自**中国足彩网 zgzcw.com**，无第三方API依赖。

| 数据 | 抓取方式 | 说明 |
|------|---------|------|
| 百家平均欧赔 | GET + POST | 竞彩+北单，3天窗口 |
| 百家平均亚盘 | POST company=0 | companyType=y |
| Pinnacle/HKJC/利记/明升 1X2+AH+OU | **oyzs_ajax 三合一** | odds.zgzcw.com，一次请求4家公司×3类型 |
| Betfair | POST company=56 | companyType=b |
| 赛果 | 500.com | fetch_500com_results.py |
| 积分榜 | 球天下 data.qtx.com | fetch_standings_qtx.py |

> **oyzs_ajax 三合一接口**（2026-06-22起）：`odds.zgzcw.com/odds/oyzs_ajax.action` 一次请求返回 Pinnacle(22)/HKJC(136)/利记(15)/明升(6) 的1X2欧赔+亚盘+大小球（含初盘+即时盘），替代之前的散装POST（6次×4公司=24次请求→1次）。

> **注意**：百家平均(0)和Betfair(56)不在oyzs_ajax中提供，仍使用bjzs POST。plzx域名被WAF拦截，百家平均GET/POST需通过odds.zgzcw.com或Termux本地跑。

### 队名别名归一化
`team_aliases.py` 维护50+队名别名映射，解决跨源队名译名不一致问题：
- 竞彩 vs 北单不同译名（如"谢尔伯恩"vs"舒尔本"）
- 队名截断差异（如"博塔弗戈SP"vs"博塔弗戈"）
- `merge_and_build.py` 3处匹配逻辑均使用 `canonical()` 归一化

### 亚盘数据

亚盘列优先级：**Pinnacle → 利记 → 百家平均**

Pinnacle/HKJC/利记/明升的亚盘+大小球数据通过 oyzs_ajax 三合一接口获取（含初盘+即时盘）。百家平均亚盘仍通过 bjzs POST（companyType=y）获取。

### Pinnacle 主客 swap 检测

Pinnacle 赔率方向可能与竞彩相反（主客队顺序不同），`fetch_pinnacle_odds.py` 的 `save_to_db` 函数会：
1. 从 DB 读取竞彩赔率（odds_win / odds_loss）
2. 对比 Pinnacle 赔率方向：若 Pinnacle 最低赔对应竞彩最高赔 → 触发 swap
3. swap 后翻转 Pinnacle 全部赔率（1X2 + 亚盘）

### 异常赔率过滤

Pinnacle 单值 > 30 且其他值 < 10 的记录判定为异常，整条丢弃，不写入 DB。

### AH/OU 段 SQL 防冻结

所有 AH/OU 相关 SQL 使用 `COALESCE(new_value, old_value)` 模式：
- 新值有效 → 更新
- 新值为空/0 → 保留 DB 旧值（`_or_none` helper：dict 空/全0 → None → COALESCE 保留）
- 真平手（handicap=0.0）不会被误判为"没数据"

### EV算法
- tanh软压缩替代硬截断，保留区分度
- 原始EV存 `ev_win_raw` 等列，压缩后存 `ev_win` 等列
- Pinnacle 1X2 为唯一有效欧赔源（company=106）

### DB赛果回填
`push_db.py` 推送前自动从Release旧DB回填 `actual_outcome` 和 `deviation_analysis`，防止赛果丢失。

## 数据源

- **赔率**：中国足彩网 zgzcw.com（唯一数据源）
- **赛果**：500.com
- **积分榜**：球天下 data.qtx.com

## 看板地址

https://bily1258-design.github.io/football-dashboard/

## 变更记录

### 2026-06-21
- **移除 api-football 依赖**：删 434 行代码，消除队名匹配/swap方向/脏数据三大问题
- Pinnacle 亚盘改用足彩网 POST（company=106, companyType=y）
- Pinnacle 主客 swap 检测改用 DB 竞彩赔率（不再依赖 api-football match 对象）
- AH/OU 段 SQL 全改 COALESCE 防冻结（14处 CASE WHEN != 0 → COALESCE + _or_none helper）
- daily_report.py push_db 超时 60s → 300s，push_db.py 内部 60s → 180s
- 异常赔率过滤（Pinnacle 单值>30 且其他<10 → 丢弃）
