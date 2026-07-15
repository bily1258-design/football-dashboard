#!/data/data/com.termux/files/usr/bin/bash
# fetch_and_push.sh — Termux定时任务：抓取zqdc数据 → 分析 → 推送GitHub
# 用法：./scripts/fetch_and_push.sh           # 抓取+分析今天
#       ./scripts/fetch_and_push.sh 2026-07-11 # 指定日期

set -e
cd "$(dirname "$0")/.." || exit 1

DATE="${1:-$(date +%Y-%m-%d)}"
FILE="data/matches_${DATE//-/}.json"

echo "[$(date '+%H:%M:%S')] 抓取 $DATE ..."
# 自动发现当前期号，整期抓取（一次请求获得所有日期的比赛+赔率）
PERIOD=$(python3 << 'PYEOF' 2>/dev/null
import sys; sys.path.insert(0, 'scripts')
from fetch_zqdc import fetch_available_periods
import contextlib
with contextlib.redirect_stdout(None):
    ps = fetch_available_periods()
if ps: print(ps[0])
PYEOF
)
if [ -n "$PERIOD" ]; then
    echo "[$(date '+%H:%M:%S')] 整期抓取 $PERIOD ..."
    python3 scripts/fetch_zqdc.py --fetch-period "$PERIOD"
else
    # 兜底：按单日抓取
    python3 scripts/fetch_zqdc.py --date "$DATE"
    TOMORROW=$(date -d "$DATE +1 day" '+%Y-%m-%d')
    TOMORROW_FILE="data/matches_${TOMORROW//-/}.json"
    if [ ! -f "$TOMORROW_FILE" ]; then
        echo "[$(date '+%H:%M:%S')] 抓取 $TOMORROW (凌晨场)..."
        python3 scripts/fetch_zqdc.py --date "$TOMORROW" --no-pinnacle --no-hkjc
    fi
fi

if [ ! -f "$FILE" ]; then
    echo "[$(date '+%H:%M:%S')] 无比赛数据，跳过"
    exit 0
fi

# ========== 比分回填：重新抓取前3天，更新已完赛比分 ==========
for i in 1 2 3; do
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

# ========== 香港马会赔率全量抓取（今天） ==========
echo "[$(date '+%H:%M:%S')] 抓取 $DATE 香港马会比赛..."
python3 scripts/fetch_hkjc_all.py --date "$DATE"

echo "[$(date '+%H:%M:%S')] 分析 $DATE ..."
python3 scripts/ai_analysis.py

echo "[$(date '+%H:%M:%S')] 推送至GitHub..."
git add -A
git commit -m "数据+分析 $DATE" || echo "无新数据"
git push origin main

echo "[$(date '+%H:%M:%S')] ✅ 完成"