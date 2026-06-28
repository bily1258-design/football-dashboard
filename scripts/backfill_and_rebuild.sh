#!/bin/bash
# backfill_and_rebuild.sh — 手动回填赛果后一键重建看板
# 用法:
#   ./scripts/backfill_and_rebuild.sh                    # 默认: 重建+push
#   ./scripts/backfill_and_rebuild.sh --no-push          # 只重建不push
#   ./scripts/backfill_and_rebuild.sh --upload-release   # 重建+上传DB到Release+push

set -e
cd "$(dirname "$0")/.."

PUSH=true
UPLOAD_RELEASE=false

for arg in "$@"; do
    case $arg in
        --no-push) PUSH=false ;;
        --upload-release) UPLOAD_RELEASE=true ;;
    esac
done

echo "🔨 [1/3] merge_and_build.py — 从DB生成results.json + index.html"
python3 scripts/merge_and_build.py

if [ "$UPLOAD_RELEASE" = true ]; then
    echo "📦 [2/3] 上传DB到GitHub Release (db-latest)"
    gh release upload db-latest data/football.db --clobber
else
    echo "⏭️  [2/3] 跳过Release上传"
fi

if [ "$PUSH" = true ]; then
    echo "🚀 [3/3] git push — 部署到看板"
    git add docs/data/results.json docs/index.html
    if git diff --cached --quiet; then
        echo "✅ 无变化，无需push"
    else
        git commit -m "rebuild: merge_and_build with latest DB"
        git push origin main
        echo "✅ 已push，看板即将更新"
    fi
else
    echo "⏭️  [3/3] 跳过push（--no-push）"
fi

echo ""
echo "📌 以后手动回填赛果后，跑这个脚本即可一步到位。"
