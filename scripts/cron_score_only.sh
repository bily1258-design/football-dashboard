#!/data/data/com.termux/files/usr/bin/bash
# cron_score_only.sh — 抓比分 + 赔率 → 重建看板 → 推送
# 由 Termux crond 调度，每日 08:30 北京时间执行

set -e

cd /data/data/com.termux/files/home/football-dashboard || exit 1

TODAY=$(date +%Y-%m-%d)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "[${NOW}] 🚀 数据更新开始 (${TODAY})"

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

# Step 1.5: 抓取5家公司赔率（Pinnacle/Bet365/利记/明升/威廉希尔/HKJC）
echo "[$(date '+%H:%M:%S')] Step 1.5: 抓取赔率... (公司: all)"
python3 scripts/fetch_500com_odds.py --db "$DB" --company all 2>&1 || echo "  ⚠️ 抓取赔率失败"
echo ""

# Step 2: 重建看板JSON（含比分+赔率）
echo "[$(date '+%H:%M:%S')] Step 2: 重建看板JSON..."
python3 scripts/merge_and_build.py --db "$DB" 2>&1 || echo "  ⚠️ 重建JSON失败"

# Step 3: 推DB到Release（供GA使用带比分的DB）
echo "[$(date '+%H:%M:%S')] Step 3: 推DB到Release..."
source ~/.bashrc 2>/dev/null && \
gh release upload db-latest "$DB" --clobber 2>&1 && \
echo "  ✅ DB推送成功" || echo "  ⚠️ DB推送失败（可忽略）"

# Step 4: 推送docs/到GitHub
echo "[$(date '+%H:%M:%S')] Step 4: 推送看板..."
git add docs/ && \
git commit -m "docs: score backfill $(date +%Y-%m-%d)" 2>/dev/null && \
git push origin main 2>&1 && \
echo "  ✅ 推送成功" || echo "  ⏭️ 无变更可推"

echo "[$(date '+%H:%M:%S')] 🏁 数据更新结束"
