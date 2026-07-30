#!/data/data/com.termux/files/usr/bin/bash
# fetch_and_push.sh — Termux定时任务：抓取zqdc数据 → 分析 → 推送GitHub
# 用法：./scripts/fetch_and_push.sh           # 抓取+分析今天
#       ./scripts/fetch_and_push.sh 2026-07-11 # 指定日期

set -e
cd "$(dirname "$0")/.." || exit 1

DATE="${1:-$(date +%Y-%m-%d)}"
FILE="data/matches_${DATE//-/}.json"

echo "[$(date '+%H:%M:%S')] 抓取 $DATE (24h窗口: 12:00~次日11:59)..."
# 北单: fetch_all_matches 已自动包含今天+明天
python3 scripts/fetch_zqdc.py --date "$DATE"
# 额外抓明天文件, 覆盖旧数据(让load_raw_matches拿到正确日期)
TOMORROW=$(date -d "$DATE +1 day" '+%Y-%m-%d')
echo "[$(date '+%H:%M:%S')] 抓取 $TOMORROW (凌晨场/次日午前)..."
python3 scripts/fetch_zqdc.py --date "$TOMORROW"

if [ ! -f "$FILE" ]; then
    echo "[$(date '+%H:%M:%S')] 无比赛数据，跳过"
    exit 0
fi

# ========== 比分回填：重新抓取今天+前3天，更新已完赛比分 ==========
for i in 0 1 2 3; do
    BACK_DATE=$(date -d "$DATE -$i day" '+%Y-%m-%d')
    BACK_FILE="data/matches_${BACK_DATE//-/}.json"
    if [ -f "$BACK_FILE" ]; then
        echo "[$(date '+%H:%M:%S')] 回填 $BACK_DATE 比分(北单)..."
        python3 scripts/fetch_zqdc.py --date "$BACK_DATE" --backfill
    fi
    HKJC_FILE="data/matches_hkjc_${BACK_DATE//-/}.json"
    if [ -f "$HKJC_FILE" ]; then
        echo "[$(date '+%H:%M:%S')] 回填 $BACK_DATE 比分(HKJC)..."
        python3 scripts/fetch_hkjc_all.py --date "$BACK_DATE" --backfill
    fi
done

# ========== 同步比分到 reference_score，重算 λ（在分析之前） ==========
echo "[$(date '+%H:%M:%S')] 同步比分到 reference_score，重算 λ..."
python3 scripts/sync_scores_and_lambdas.py

# ========== 香港马会赔率全量抓取（今天） ==========
echo "[$(date '+%H:%M:%S')] 抓取 $DATE 香港马会比赛..."
python3 scripts/fetch_hkjc_all.py --date "$DATE" --parallel 5 --delay 0.15

echo "[$(date '+%H:%M:%S')] 分析 $DATE ..."
python3 scripts/ai_analysis.py

echo "[$(date '+%H:%M:%S')] 补抓亚盘..."
python3 scripts/backfill_ah.py
python3 scripts/backfill_ah_probs.py

# ========== 同步 results.json → poisson_predictions（让xG/历史相似/总进球覆盖当日） ==========
echo "[$(date '+%H:%M:%S')] 同步AI分析结果到数据库..."
python3 scripts/sync_results_to_db.py

echo "[$(date '+%H:%M:%S')] 抓取xG特征数据（历史趋势表）..."
python3 scripts/fetch_daily_xg.py

echo "[$(date '+%H:%M:%S')] 推送至GitHub..."
git add -A
git commit -m "数据+分析 $DATE" || echo "无新数据"
git push origin main

echo "[$(date '+%H:%M:%S')] ✅ 完成"