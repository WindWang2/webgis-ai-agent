#!/bin/bash
# ============================================================
# WebGIS AI Agent 生产环境停止脚本
# 使用方式: ./scripts/stop-prod.sh
# ============================================================
set -e

PROJECT_DIR="$(dirname "$(dirname "${BASH_SOURCE[0]}")")"

echo "=============================================="
echo "  WebGIS AI Agent - 生产环境停止"
echo "=============================================="
echo ""

cd "$PROJECT_DIR"

echo "🛑 正在停止所有服务..."
docker-compose-f docker-compose. prod.yml down

echo ""
echo "✅ 所有服务已停止"
echo ""
echo "📝 如需重新启动，运行: ./scripts/start-prod.sh"
echo ""