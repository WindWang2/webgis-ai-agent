#!/bin/bash
# 灰度环境测试启动脚本
set -e
cd "$(dirname "$0")/.."
echo "🚀 启动 WebGIS AI Agent 灰度测试环境..."
echo ""
echo "📦 准备环境配置..."
export ENV=staging
export DEBUG=false
export DISABLE_DB_PWD_WARN=true
export PYTHONUNBUFFERED=1
echo "  ✓ ENV=$ENV"
echo "  ✓ DEBUG=$DEBUG"
echo ""
echo "🐍 启动 FastAPI 服务 (端口 8888)..."
python3 -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8888 \
  --reload \
  --log-level info