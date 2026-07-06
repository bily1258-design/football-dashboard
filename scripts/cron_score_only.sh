#!/data/data/com.termux/files/usr/bin/bash
# cron_score_only.sh — 仅抓比分（fid回填）
# 由 Termux crond 调度，每日 08:30 北京时间执行

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "[${NOW}] 🚀 比分回填开始 (${TODAY})"

# Step 0: 同步最新代码（强制覆盖本地，避免stash冲突卡住）
echo "[$(date '+%H:%M:%S')] Step 0: 同步代码..."
git fetch origin 2>&1 || echo "  ⚠️ git fetch 失败"
git reset --hard origin/main 2>&1 || echo "  ⚠️ git reset 失败"

# Step 0.5: 检查DB完整性
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

# Step 1: 补fid + 按fid回填比分
echo "[$(date '+%H:%M:%S')] Step 1: 补fid+回填比分..."
python3 scripts/backfill_from_500com.py --db "$DB" 2>&1 || echo "  ⚠️ 回填比分失败"

echo "[$(date '+%H:%M:%S')] 🏁 比分回填结束"
