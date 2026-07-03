#!/data/data/com.termux/files/usr/bin/bash
# cron-daemon.sh — 后台守护循环，替代 crond
# 每 15 分钟检查一次，在北京时间 8:30 (UTC 00:30) 和 15:30 (UTC 07:30) 执行完整 pipeline
# 常驻后台，由 boot 脚本自动启动
# 北京时间 8:30 = UTC 00:30, 北京时间 15:30 = UTC 07:30

SCRIPT_DIR="/data/data/com.termux/files/home/football-dashboard"
PIPELINE_SCRIPT="$SCRIPT_DIR/cron-pipeline.sh"
LOCKFILE="/data/data/com.termux/files/home/.cache/cron-daemon.lock"
LOGFILE="/data/data/com.termux/files/home/.cache/cron-daemon.log"
LAST_RUN_FILE="/data/data/com.termux/files/home/.cache/cron-daemon.last"
SLEEP_SEC=900  # 15 分钟

# 防重复启动
exec 200>"$LOCKFILE"
flock -n 200 || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已有实例运行，退出"
    exit 1
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🟢 cron-daemon 启动 (UTC 00:30/07:30 = 北京 8:30/15:30)" >> "$LOGFILE"

while true; do
    # 取 HHMM 做数字比较（忽略秒）
    NOW_NUM=$((10#$(date -u +%H%M)))
    TODAY=$(date -u +%Y-%m-%d)
    LAST_RUN=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "never")

    # 北京时间 8:30 = UTC 00:30 → HHMM 0030, 窗口 0015~0045
    # 北京时间 15:30 = UTC 07:30 → HHMM 0730, 窗口 0715~0745
    if { [ "$NOW_NUM" -ge 15 ] && [ "$NOW_NUM" -le 45 ]; } || \
       { [ "$NOW_NUM" -ge 715 ] && [ "$NOW_NUM" -le 745 ]; }; then

        # 防重复：同一天同一时段只跑一次
        if [ "$NOW_NUM" -ge 15 ] && [ "$NOW_NUM" -le 45 ]; then
            RUN_KEY="${TODAY}_0030"
        else
            RUN_KEY="${TODAY}_0730"
        fi

        if [ "$LAST_RUN" == "$RUN_KEY" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⏭️  $RUN_KEY 已跑过，跳过" >> "$LOGFILE"
            sleep "$SLEEP_SEC"
            continue
        fi

        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🔔 $RUN_KEY — 启动 pipeline" >> "$LOGFILE"
        cd "$SCRIPT_DIR"
        bash "$PIPELINE_SCRIPT" >> "$LOGFILE" 2>&1
        echo "$RUN_KEY" > "$LAST_RUN_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ $RUN_KEY 完成" >> "$LOGFILE"

        # 跑完后跳过半小时内的重复检查
        sleep 1800
        continue
    fi

    sleep "$SLEEP_SEC"
done
