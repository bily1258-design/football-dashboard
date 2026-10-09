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
python3 scripts/fetch_zqdc.py --date "$DATE" --parallel 8 --delay 0.5
# 额外抓明天文件, 覆盖旧数据(让load_raw_matches拿到正确日期)
TOMORROW=$(date -d "$DATE +1 day" '+%Y-%m-%d')
echo "[$(date '+%H:%M:%S')] 抓取 $TOMORROW (凌晨场/次日午前)..."
python3 scripts/fetch_zqdc.py --date "$TOMORROW" --parallel 8 --delay 0.5

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
python3 scripts/fetch_hkjc_all.py --date "$DATE" --parallel 8 --delay 0.15

echo "[$(date '+%H:%M:%S')] 分析 $DATE ..."
python3 scripts/ai_analysis.py

echo "[$(date '+%H:%M:%S')] 补抓亚盘..."
python3 scripts/backfill_ah.py
python3 scripts/backfill_ah_probs.py

# 500.com 北单双玩法: 让球胜平负→亚初行, 胜负过关→亚即行 (写 ahbd_* 字段, 不参与规则)
# 放在 backfill_ah_probs 之后: 真亚盘概率只由 titan007 计算, 免得 500.com 数据污染
echo "[$(date '+%H:%M:%S')] 补 500.com 北单让球 (亚初/亚即兜底)..."
python3 scripts/fetch_500_bjdc.py --write

# 北单「胜负过关」盘口赢盘概率 (命中列 上/下 方向 + ahbd_pred_desc)
# 必须跑在 fetch_500_bjdc.py 之后: 让球数 ahbd_cur_handicap 那时才写进 results.json
# 口径: 正=主队受让, 主队过关 ⟺ 净胜+让球>0; 纯看板显示层, 不参与清单规则
echo "[$(date '+%H:%M:%S')] 补算北单「过」盘口赢盘概率 (ahbd_*)..."
python3 scripts/backfill_bd_probs.py

# 500.com 北单全池表 (docs/bjdc.html 的数据源, 含我方清单未收录的场次; 纯展示, 不参与规则)
echo "[$(date '+%H:%M:%S')] 生成北单全池表 (bjdc_pool.json)..."
python3 scripts/gen_bjdc_pool.py || echo "  ⚠️ 北单全池表生成失败, 继续"

# 生成看板精简版 JSON (剔除 stats 等无用大字段, 12.7MB→1.6MB, 加速页面加载)
echo "[$(date '+%H:%M:%S')] 生成看板精简数据 (results_light.json)..."
python3 scripts/gen_light_results.py

# ========== 增量抓取首发阵容/阵型/主教练 (近3天; 只读参考段用, 不参与选号) ==========
# 必须排在「生成清单」之前, 否则当天新场次的阵容赶不上当天清单
echo "[$(date '+%H:%M:%S')] 增量抓取阵容(近3天)..."
python3 scripts/fetch_lineups.py --days 3 --limit 200 --sleep 0.8 >> "$HOME/lineup_daily.log" 2>&1 || echo "⚠️ 阵容抓取失败(不影响主流程)"

# 今日推荐清单 (①高置信方向投注=model/LGBM同向且>44.9% / ②客客客 / ③胜胜胜 / 📐低熵核心区 / 📋战术阵容参考)
# 注: 老注释「客胜价值投注清单 = 客胜+EV>0.5+HKJC赔率3-6」已过时 —— 该口径现在只由 yesterday_review.py 复盘统计, 不在清单里选号
# --md: 完整清单写入 docs/today_picks.md, GitHub Pages 渲染成网页, 微信只推摘要+链接(省限流)
echo "[$(date '+%H:%M:%S')] 生成今日推荐清单(+今日推荐文档)..."
python3 scripts/away_value_picks.py --md docs/today_picks.md

# ========== 昨日清单赛果回填复盘（固定文件名, Pages 可看） ==========
# 2026-09-02 防空壳覆盖: picks_*.md 不含场次(<10场)则跳过复盘生成, 防止凌晨空壳清单覆盖真实复盘
REVIEW_LOG="$HOME/review_shell_guard.log"
PICKS_FILE="docs/picks_${YDAY_C}.md"
if [ -f "$PICKS_FILE" ]; then
    PICKS_N=$(grep -cE '^[0-9]{2}-[0-9]{2}[[:space:]]+[0-9]{2}:[0-9]{2}[[:space:]].* vs ' "$PICKS_FILE" 2>/dev/null)
    [ -z "$PICKS_N" ] && PICKS_N=0
    if [ "$PICKS_N" -lt 10 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠️ 跳过复盘: $PICKS_FILE 仅 $PICKS_N 场(疑似空壳), 不覆盖真实复盘" | tee -a "$REVIEW_LOG"
    else
        echo "[$(date '+%H:%M:%S')] 生成昨日清单赛果回填复盘...($PICKS_FILE $PICKS_N 场)"
        python3 scripts/gen_daily_review.py --date "$YDAY" --picks "$PICKS_FILE" >> "$REVIEW_LOG" 2>&1
        # 备份真实复盘到仓库外(防再被空壳覆盖, 不进git避免仓库膨胀)
        mkdir -p "$HOME/football-review-backups"
        cp "$PICKS_FILE" "$HOME/football-review-backups/picks_${YDAY_C}.md" >> "$REVIEW_LOG" 2>&1
        cp "docs/推荐清单·赛果回填复盘.md" "$HOME/football-review-backups/review_${YDAY_C}.md" >> "$REVIEW_LOG" 2>&1
        echo "[$(date '+%H:%M:%S')] 复盘md已备份 → ~/football-review-backups/review_${YDAY_C}.md" >> "$REVIEW_LOG"
        # Excel 版复盘 (复盘.xlsx, ~/storage/shared/Documents/, 每日覆盖)
        echo "[$(date '+%H:%M:%S')] 生成 Excel 版复盘 (复盘.xlsx)..."
        python3 scripts/gen_review_xlsx.py >> "$REVIEW_LOG" 2>&1
    fi
fi

# ⚡高权重场次追踪 (⚡>=1.14 临场窗口记录, 验证顶级1.2 vs 次级1.14 开出规律; 逐轮攒样本)
echo "[$(date '+%H:%M:%S')] 追踪⚡高权重场次..."
python3 scripts/high_weight_tracker.py

# ========== 抓取xG特征数据（历史趋势表） ==========
python3 scripts/fetch_daily_xg.py

echo "[$(date '+%H:%M:%S')] 推送至GitHub..."
git add -A
git commit -m "数据+分析 $DATE" || echo "无新数据"

# 2026-10-08 加护: 手机网络/ssh 抖动会让单次 push 以 rc=128 中断 (10-07、10-08 连续两次同因:
# "Connection to ssh.github.com closed by remote host"), 而此前 30 分钟的抓取/分析产物已全部生成完毕,
# 白白让整轮报 failed 且清单没上 Pages。此处只重试推送(不重跑抓取), 三次仍失败才置非零退出码告警。
PUSH_OK=0
for i in 1 2 3; do
    if git push origin main; then PUSH_OK=1; break; fi
    if [ "$i" -lt 3 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠️ push 第 $i 次失败(多为 ssh.github.com 连接被远端断开), 20s 后重试..."
        sleep 20
    else
        echo "[$(date '+%H:%M:%S')] ⚠️ push 第 $i 次失败"
    fi
done
if [ "$PUSH_OK" -ne 1 ]; then
    echo "[$(date '+%H:%M:%S')] ❌ push 3 次均失败: 产物已本地 commit, 未推远端; 下次运行会自动补推(不需重跑抓取)"
    exit 1
fi

echo "[$(date '+%H:%M:%S')] ✅ 完成"