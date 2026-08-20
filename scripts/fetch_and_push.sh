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

# ========== 昨日清单存档 + 赛果回填复盘（每日12:30/16:10任务自动执行） ==========
# 存档: today_picks.md 在 fetch 前仍是「昨日清单」→ 存 picks_YYYYMMDD.md 留底
# 复盘: 用存档清单 + 已回填的 results.json 生成「推荐清单·赛果回填复盘.md」(固定名, Pages可看)
YDAY=$(date -d "$DATE -1 day" '+%Y-%m-%d')
YDAY_C=${YDAY//-/}
if [ -f "docs/today_picks.md" ] && [ ! -f "docs/picks_${YDAY_C}.md" ]; then
    echo "[$(date '+%H:%M:%S')] 存档昨日清单 → docs/picks_${YDAY_C}.md"
    cp docs/today_picks.md "docs/picks_${YDAY_C}.md"
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

# ========== 同步 results.json → poisson_predictions（含比分补写，必须在 λ 重算之前） ==========
echo "[$(date '+%H:%M:%S')] 同步AI分析结果到数据库..."
python3 scripts/sync_results_to_db.py

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

# 生成看板精简版 JSON (剔除 stats 等无用大字段, 12.7MB→1.6MB, 加速页面加载)
echo "[$(date '+%H:%M:%S')] 生成看板精简数据 (results_light.json)..."
python3 scripts/gen_light_results.py

# 客胜价值投注清单 (回测验证: 客胜+EV>0.5+HKJC赔率3-6 唯一正期望; 供每日推送)
# --md: 完整清单写入 docs/today_picks.md, GitHub Pages 渲染成网页, 微信只推摘要+链接(省限流)
echo "[$(date '+%H:%M:%S')] 生成客胜价值投注清单(+今日推荐文档)..."
python3 scripts/away_value_picks.py --md docs/today_picks.md

# ========== 昨日清单赛果回填复盘（固定文件名, Pages 可看） ==========
if [ -f "docs/picks_${YDAY_C}.md" ]; then
    echo "[$(date '+%H:%M:%S')] 生成昨日清单赛果回填复盘..."
    python3 scripts/gen_daily_review.py --date "$YDAY" --picks "docs/picks_${YDAY_C}.md"
fi

# ⚡高权重场次追踪 (⚡>=1.14 临场窗口记录, 验证顶级1.2 vs 次级1.14 开出规律; 逐轮攒样本)
echo "[$(date '+%H:%M:%S')] 追踪⚡高权重场次..."
python3 scripts/high_weight_tracker.py

# ========== 抓取xG特征数据（历史趋势表） ==========
python3 scripts/fetch_daily_xg.py

echo "[$(date '+%H:%M:%S')] 推送至GitHub..."
git add -A
git commit -m "数据+分析 $DATE" || echo "无新数据"
git push origin main

# ========== ntfy 推送（独立通知通道, 不占微信限流配额） ==========
# ntfy.sh 国内直连被墙, 走 v2rayNG socks 代理; 代理没开时静默跳过, 不影响主流程
if curl -s -m 15 --socks5-hostname 127.0.0.1:10808 -o /dev/null \
    -H "Title: ⚽ 今日清单已更新" \
    -H "Priority: default" \
    -H "Tags: soccer" \
    -d "今日推荐清单已生成 → https://bily1258-design.github.io/football-dashboard/today_picks.md (GitHub Pages 原文)" \
    "https://ntfy.sh/bily1258-football-daily"; then
    echo "[$(date '+%H:%M:%S')] ✅ ntfy 推送成功"
else
    echo "[$(date '+%H:%M:%S')] ⚠️ ntfy 推送失败(代理未开?), 静默跳过" || true
fi

echo "[$(date '+%H:%M:%S')] ✅ 完成"