#!/data/data/com.termux/files/usr/bin/bash
# cron_full_pipeline.sh — 完整流程：回填赛果 + 赔率/预测/看板/推送
# 由 Hermes cron 系统调度，每3小时运行一次

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "[${NOW}] 🚀 完整流程开始 (${TODAY})"

# Step 0: 同步最新代码
git pull origin main --rebase 2>&1 || echo "  ⚠️ git pull 失败"

# Step 1: 从500.com回填赛果 (替代 review.py 的 zgzcw 源)
python3 scripts/backfill_from_500com.py --db data/football.db 2>&1

# Step 2: 完整 pipeline (赔率/预测/融合/校准/构建/推送)
# --skip-review: 赛果已由 backfill_from_500com 处理，跳过 review.py
python3 scripts/pipeline.py \
    --date "$TODAY" \
    --db data/football.db \
    --skip-review \
    2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🏁 完整流程结束 (exit=$?)"
