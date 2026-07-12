#!/data/data/com.termux/files/usr/bin/bash
# cron_full_pipeline.sh — 完整流程：回填赛果 + 赔率/预测/看板/推送
# 由 Hermes cron 系统调度，每3小时运行一次

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "[${NOW}] 🚀 完整流程开始 (${TODAY})"

# Step 0: 同步最新代码（先暂存本地修改再pull，pull完再恢复）
echo "[$(date '+%H:%M:%S')] Step 0: 同步代码..."
STASH_MSG="cron-auto-stash-$(date +%s)"
git stash push -m "$STASH_MSG" 2>&1 || echo "  ℹ️ 无需暂存"
git pull origin main --rebase 2>&1 || echo "  ⚠️ git pull 失败"
# 尽量恢复暂存，不阻塞流程
git stash pop 2>&1 || echo "  ℹ️ 无暂存可恢复"

# Step 0.5: 检查DB完整性，损坏则从Release恢复
echo "[$(date '+%H:%M:%S')] Step 0.5: 检查DB完整性..."
DB="data/football.db"
if ! sqlite3 "$DB" "PRAGMA integrity_check;" 2>/dev/null | grep -q "ok"; then
    echo "  ⚠️ DB损坏，从Release下载恢复..."
    gh release download db-latest --pattern 'football.db' --dir data/ --clobber 2>&1 || {
        echo "  ❌ 从Release恢复失败，跳过此轮"
        exit 1
    }
    echo "  ✅ DB已恢复 ($(du -h "$DB" | cut -f1))"
fi

# Step 1: 从500.com回填赛果 (替代 review.py 的 zgzcw 源)
echo "[$(date '+%H:%M:%S')] Step 1: 回填赛果..."
python3 scripts/backfill_from_500com.py --db "$DB" 2>&1 || echo "  ⚠️ 回填赛果失败，继续执行pipeline"

# Step 2: 完整 pipeline (赔率/预测/融合/校准/构建/推送)
echo "[$(date '+%H:%M:%S')] Step 2: 运行pipeline..."
python3 scripts/pipeline.py \
    --date "$TODAY" \
    --db "$DB" \
    --skip-review \
    2>&1 && \
echo "[$(date '+%H:%M:%S')] 🏁 完整流程结束 成功" || \
echo "[$(date '+%H:%M:%S')] 🏁 完整流程结束 失败(exit=$?)"
