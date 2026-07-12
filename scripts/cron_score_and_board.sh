#!/data/data/com.termux/files/usr/bin/bash
# cron_score_and_board.sh — 定时回填赛果 + 更新看板
# 由 Hermes cron 系统调度，每30分钟检查一次

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "[${NOW}] 🚀 开始: 回填赛果 + 构建看板 (${TODAY})"

# Step 1: 确保是最新代码
git pull origin main --rebase 2>&1 || echo "  ⚠️ git pull 失败，继续"

# Step 2: 从500.com回填赛果
python3 scripts/backfill_from_500com.py --db data/football.db 2>&1
BACKFILL_EXIT=$?

# Step 3: 构建看板 (merge_and_build 会读 actual_outcome)
python3 scripts/merge_and_build.py --db data/football.db 2>&1
BUILD_EXIT=$?

# Step 4: 推送
if [ $BUILD_EXIT -eq 0 ]; then
    git add -A 2>&1
    # 检查是否有变化
    if git diff --cached --quiet; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⏭️  无变化，跳过推送"
    else
        git commit -m "auto update $(date '+%Y-%m-%d %H:%M')" 2>&1 || echo "  ⚠️ commit 失败"
        git push origin main 2>&1 || echo "  ⚠️ push 失败"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 构建+推送完成"
    fi
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 构建失败 (exit=$BUILD_EXIT)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🏁 完成"
