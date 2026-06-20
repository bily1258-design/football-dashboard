# 竞彩足球泊松分布分析系统 - football-dashboard

## 快速开始

### 日常一条命令（Termux）

```bash
fd
# 等价于: cd ~/football-dashboard && git pull && git checkout -- data/raw/ && python scripts/fetch_data.py --fetch-and-push --with-report --with-review --today
```

完整流程：赔率抓取 → Pinnacle亚盘/大小球 → 泊松计算 → 日报 → 复盘 → push DB → git push → GA自动构建看板

### 三个入口脚本

| 脚本 | 用途 |
|------|------|
| `scripts/fetch.py` | 数据抓取（足彩网赔率 + api-football Pinnacle） |
| `scripts/pipeline.py` | 8步流水线（预测→λ补算→融合→EV→凯利→清理→对齐→push_db） |
| `scripts/build.py` | 看板构建（merge_and_build） |

日常用 `fetch_data.py` 一条命令即可，它内部按序调用以上三个入口。

### fetch_data.py 常用参数

```bash
# 基本用法
python scripts/fetch_data.py --today              # 今天数据
python scripts/fetch_data.py --yesterday           # 昨天数据
python scripts/fetch_data.py --date 2026-06-15     # 指定日期

# 抓取模式
python scripts/fetch_data.py --fetch-only           # 只抓取
python scripts/fetch_data.py --fetch-and-push       # 抓取+推送（默认）

# 完整流程
python scripts/fetch_data.py --fetch-and-push --with-report --with-review --today
```

## 目录结构

```
football-dashboard/
├── scripts/
│   ├── fetch_data.py          # 主调度脚本（日常入口）
│   ├── fetch.py               # 数据抓取入口
│   ├── pipeline.py            # 8步流水线入口
│   ├── build.py               # 看板构建入口
│   ├── odds_api.py            # 足彩网赔率抓取
│   ├── fetch_pinnacle_odds.py # api-football Pinnacle数据
│   ├── predict_from_odds.py   # 泊松预测
│   ├── calc_lambda.py         # λ补算
│   ├── fusion_predict.py      # 融合预测
│   ├── value_bet.py           # EV价值投注（tanh软压缩）
│   ├── merge_and_build.py     # 看板数据合并+构建
│   ├── push_db.py             # DB推送+赛果回填
│   ├── team_aliases.py        # 队名别名归一化
│   ├── daily_report.py        # 日报生成
│   ├── review.py              # 复盘脚本
│   └── ...                    # 其他辅助脚本
├── data/
│   ├── raw/                   # 原始数据
│   ├── processed/             # 处理后数据（JSON/天）
│   ├── cache/                 # 缓存
│   ├── reports/               # 日报输出
│   └── football.db            # SQLite数据库（不提交git）
├── docs/                      # GitHub Pages 输出
├── .github/workflows/
│   └── fetch-and-build.yml    # GA 自动构建
└── README.md
```

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

### 队名别名归一化
`team_aliases.py` 维护50+队名别名映射，解决跨源队名译名不一致问题：
- 竞彩 vs 北单不同译名（如"谢尔伯恩"vs"舒尔本"）
- 队名截断差异（如"博塔弗戈SP"vs"博塔弗戈"）
- `merge_and_build.py` 3处匹配逻辑均使用 `canonical()` 归一化

### 亚盘数据
| 来源 | Company ID | 数据 |
|------|-----------|------|
| 百家平均 | 0 | AH初盘+即时盘 |
| 利记 | 15 | AH初盘+即时盘 |
| 明升 | 6 | AH初盘+即时盘 |
| Pinnacle | api-football | AH + OU（1X2+亚盘+大小球） |

亚盘列优先显示 Pinnacle 让球，点击弹窗查看完整赔率详情。

### EV算法
- tanh软压缩替代硬截断，保留区分度
- 原始EV存 `ev_win_raw` 等列，压缩后存 `ev_win` 等列
- Pinnacle 1X2 为唯一有效欧赔源（company=106）

### DB赛果回填
`push_db.py` 推送前自动从Release旧DB回填 `actual_outcome` 和 `deviation_analysis`，防止赛果丢失。

## 数据源

- **赔率**：中国足彩网 zgzcw.com
- **Pinnacle**：api-football（bookmaker_id=4，日限100次）
- **赛果**：500.com（WAF拦截中，当前靠足彩网+手动补录）
- **积分榜**：球天下 data.qtx.com

## 看板地址

https://bily1258-design.github.io/football-dashboard/
