#!/data/data/com.termux/files/usr/bin/bash
# fetch_and_push.sh — Termux定时任务：抓取zqdc数据并推送至GitHub
# 用法：./scripts/fetch_and_push.sh           # 抓取今天
#       ./scripts/fetch_and_push.sh 2026-07-11 # 抓取指定日期

set -e
cd "$(dirname "$0")/.." || exit 1

DATE="${1:-$(date +%Y-%m-%d)}"
FILE="data/matches_${DATE//-/}.json"

echo "[$(date '+%H:%M:%S')] 抓取 $DATE ..."
python3 scripts/fetch_zqdc.py --date "$DATE"

if [ ! -f "$FILE" ]; then
    echo "[$(date '+%H:%M:%S')] 无比赛数据，跳过"
    exit 0
fi

echo "[$(date '+%H:%M:%S')] 推送至GitHub..."
git add "$FILE"
git commit -m "数据 $DATE" || echo "无新数据"
git push origin main

echo "[$(date '+%H:%M:%S')] ✅ 完成"