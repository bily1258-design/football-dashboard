#!/bin/bash
# 北单看板定时抓取推送脚本
# 生成 Excel 并推送到 football-odds-api 仓库

set -e
cd /data/data/com.termux/files/home/football-dashboard

# 生成看板
echo "=== 北单看板生成: $(date) ==="
OUTPUT=$(python3 scripts/gen_500_dashboard.py 2>&1)
echo "$OUTPUT"

# 重新生成 index.html（含所有期数 + 全部赛事 + 下拉选择）
python3 scripts/gen_beidan_html.py 2>&1

# 提取 xlsx 路径
XLSX_PATH=$(echo "$OUTPUT" | grep -oP '/data/.*?\.xlsx' | tail -1)
if [ -z "$XLSX_PATH" ]; then
  echo "❌ 未找到生成的 xlsx 文件"
  exit 1
fi
echo "文件: $XLSX_PATH"

# 获取期数
EXPECT=$(basename "$XLSX_PATH" | sed 's/beidan_//;s/_dashboard.xlsx//')
echo "期数: $EXPECT"

# 同步到 football-odds-api
ODDS_REPO=/data/data/com.termux/files/home/football-odds-api-repo
rm -rf "$ODDS_REPO"
mkdir -p "$ODDS_REPO"
cd "$ODDS_REPO"
git init
git checkout -b main
cp "$XLSX_PATH" "beidan_${EXPECT}_dashboard.xlsx"
cp /data/data/com.termux/files/home/football-dashboard/docs/beidan_dashboard.html "$ODDS_REPO/index.html"
git add -A
git commit -m "update: 北单${EXPECT}期看板 $(date +%Y-%m-%d) (index.html+xlsx)"
git remote add origin git@github.com:bily1258-design/football-odds-api.git
git push origin +main 2>&1
echo "✅ 推送完成: football-odds-api beidan_${EXPECT}_dashboard.xlsx"
