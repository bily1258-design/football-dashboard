# 竞彩泊松预测看板

基于泊松分布的竞彩足球预测系统看板。

## 架构

```
football-dashboard/
├─ .github/workflows/fetch-and-build.yml  # GitHub Actions 自动构建+部署
├─ data/
│   ├─ results.json                        # 结构化数据（由 merge_and_build.py 生成）
│   └─ cache/                              # 原始缓存（可选）
├─ docs/
│   └─ index.html                          # 静态看板页面（GitHub Pages 发布）
├─ scripts/
│   ├─ fetch_data.py                       # 主调度
│   ├─ fetch_results_dom.py                # 赛果爬取
│   ├─ fetch_odds.py                       # 赔率爬取
│   └─ merge_and_build.py                  # 核心：读DB → 生成 results.json + index.html
└─ README.md
```

## 数据流

1. **爬虫层**（云电脑执行）：`fetch_500com_results.py` + `fetch_pinnacle_odds.py` → 缓存JSON + football.db
2. **构建层**：`merge_and_build.py` 读取 football.db → 生成 `data/results.json` + `docs/index.html`
3. **部署层**：git push → GitHub Actions 自动部署 docs/ 到 GitHub Pages

## 本地构建

```bash
# 从本仓库根目录运行
python scripts/merge_and_build.py --db /path/to/football.db --output .

# 仅生成 JSON
python scripts/merge_and_build.py --json-only

# 仅生成 HTML
python scripts/merge_and_build.py --html-only
```

## 看板地址

https://bily1258-design.github.io/football-dashboard/
