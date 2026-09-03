# ⚽ Football Dashboard — 足彩价值投注分析系统

基于竞彩 + 香港马会(HKJC)赔率数据的泊松-EV/LGBM 价值投注分析系统。
Termux 定时抓取 → 本地分析 → GitHub Pages 展示，全程自动。

> 📌 **北单看板不在本仓库**：北京单场看板（脚本+说明+产物）独立管理于
> [football-odds-api](https://github.com/bily1258-design/football-odds-api) 仓库。本仓只负责足彩价值链。

## 🏗 架构

```
Termux (Android, 定时 cron)
│
├─ 12:30 / 16:10  fetch_and_push.sh        ← 足彩主链路
│    ├─ fetch_zqdc.py        抓竞彩 (含明日期, 比分回填前3天)
│    ├─ fetch_hkjc_all.py    抓香港马会赔率 (回填比分)
│    ├─ sync_results_to_db.py / sync_scores_and_lambdas.py
│    │                       赛果+λ同步进 football.db
│    ├─ ai_analysis.py       泊松/EV 分析 → docs/data/results*.json
│    ├─ backfill_ah*.py      亚盘概率回补
│    ├─ away_value_picks.py  客胜价值/三方一致清单 → docs/today_picks.md
│    ├─ gen_daily_review.py  昨日清单赛果回填复盘 (md+xlsx)
│    ├─ high_weight_tracker.py / fetch_daily_xg.py
│    └─ git add/commit/push
│
└─ 16:40  gen_ledger_xlsx.sh  投注簿.xlsx (账本) → ~/storage/shared/Documents/

GitHub Actions (auto_process.yml)
  └─ push 触发 → peaceiris/actions-gh-pages 部署 docs/ → GitHub Pages
```

分析全部在本地 Termux 完成，GitHub Actions 只负责部署 `docs/` 到 Pages。

## 📁 目录结构

```
data/                抓取结果 + 数据库
  ├─ football.db      主库 (赛果/λ/泊松/赔率, gitignore)
  ├─ matches_YYYYMMDD.json        竞彩
  └─ matches_hkjc_YYYYMMDD.json   香港马会
docs/                前端 + 分析产物 (部署到 Pages)
  ├─ index.html / script.js / style.css
  ├─ data/results.json · results_light.json · betting_records.json
  ├─ today_picks.md          今日推荐清单
  ├─ 推荐清单·赛果回填复盘.md  昨日复盘 (固定名每日覆盖)
  └─ picks_YYYYMMDD.md       每日清单存档
scripts/              全部抓取/分析/回填/生成脚本
  ├─ fetch_*.py       多源抓取 (zqdc/hkjc/xg/stats…)
  ├─ backfill_*.py    各维度回补
  ├─ ai_analysis.py · train_lgbm.py · update_lambdas.py
  ├─ gen_*            HTML/xlsx/md 生成器
  └─ titan007_utils.py  共享工具库 (抓取/赔率/队名繁简转换, 须同目录)
```

## 🧠 算法

- **隐含概率**: 1/odds 去抽水归一化
- **泊松 λ**: 从隐含概率反推主客队预期进球（数据库统一管理，随赛果更新）
- **泊松 1X2**: 泊松分布积分求主胜/平/客胜概率
- **融合概率**: 0.5×泊松 + 0.5×隐含
- **EV**: fusion/implied − 1；软压缩 tanh(ev/0.50)×0.50
- **LGBM**: 亚盘/大小球胜率模型（生产 v11，换模须过回测门槛）
- **投注信号**: 客胜价值(EV>0.5)、三方一致·客客客、⚡高权重、HKJC升水避雷、平博/掉水规则

## 🚀 本地运行

```bash
# 依赖
pip install -r requirements.txt   # numpy scipy opencc

# 足彩主链路 (抓取+分析+推送, 日期可选)
./scripts/fetch_and_push.sh

# 单脚本示例
python3 scripts/fetch_hkjc_all.py --date 2026-08-30 --parallel 5 --delay 0.15
```

## 🔎 数据源

- **竞彩**: `trade.500.com`（原 zx.500.com 曾被腾讯云 EdgeOne bot 防护封锁，2026-09 已迁 trade）
- **香港马会**: hkjc 官方赔率
- **统计/xG**: titan007

## ⏰ 定时任务

| 时间 | 脚本 | 产出 |
|---|---|---|
| 12:30 / 16:10 | `fetch_and_push.sh` | 抓取→分析→推送→今日清单/复盘 |
| 16:40 | `gen_ledger_xlsx.sh` | 投注簿.xlsx → 手机 Documents |

> 约定: 交付物用固定文件名每日覆盖（投注簿.xlsx / 复盘.xlsx），不留日期后缀副本。
