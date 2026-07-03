#!/data/data/com.termux/files/usr/bin/bash
# cron-pipeline.sh — 定时 pipeline 执行脚本
# 由 cron-daemon.sh 在 UTC 00:30 和 07:30 调用（北京时间 8:30 / 15:30）
# 执行完整 pipeline，包含 500.com 赔率抓取

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY_UTC=$(date -u +%Y-%m-%d)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚀 cron: 启动完整 pipeline (日期=$TODAY_UTC)"

# 先 pull 最新代码（同步 GA 调整等）
git pull origin main --rebase 2>&1 || echo "⚠️ git pull 失败，继续使用本地代码"

# 运行完整 pipeline（不含 --skip-odds — 本地要抓赔率）
python3 scripts/pipeline.py \
    --date "$TODAY_UTC" \
    --db data/football.db \
    2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ cron: pipeline 完成"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ cron: pipeline 失败 (exit=$EXIT_CODE)"
fi

exit $EXIT_CODE
