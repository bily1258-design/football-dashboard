# ⚽ 足彩价值投注看板

基于500.com北单/竞彩数据的Poisson-EV价值投注分析系统。

## 数据流

```
Termux (定时抓取)
  └─ fetch_zqdc.py → data/matches_*.json
      └─ git push

GitHub Actions (触发)
  └─ ai_analysis.py → docs/data/results.json
      └─ docs/index.html + script.js + style.css
          └─ peaceiris/actions-gh-pages → GitHub Pages
```

## 本地运行

```bash
# 抓取数据
python3 scripts/fetch_zqdc.py --date 2026-07-12

# 分析并生成前端
python3 scripts/ai_analysis.py
```

## 算法

- **隐含概率**: 1/odds，去抽水归一化
- **泊松λ**: 从隐含概率反推主客队预期进球
- **泊松1X2**: 泊松分布积分求主胜/平/客胜概率
- **融合概率**: 0.5×泊松 + 0.5×隐含
- **EV**: 概率优势法 EV = fusion/implied - 1
- **软压缩**: tanh(ev/0.50) × 0.50
