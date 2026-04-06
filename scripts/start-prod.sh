#!/bin/bash
# ============================================================
# WebGIS AI Agent 生产环境启动脚本
# 使用方式: ./scripts/start-prod.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=============================================="
echo "  WebGIS AI Agent - 生产环境启动"
echo "=============================================="
echo ""

# 检查 .env.prod 文件
if [ ! -f "$PROJECT_DIR/.env.prod" ]; then
    echo "❌ 错误: .env.prod 文件不存在！"
    echo "   请复制 .env.prod.example 为 .env.prod 并配置"
    exit 1
fi

# 检查敏感配置
source "$PROJECT_DIR/.env.prod"
if [ "$JWT_SECRET_KEY" == "[SET_YOUR_SECURE_RANDOM_STRING_HERE]" ] || [ -z "$JWT_SECRET_KEY" ]; then
    echo "❌ 错误: JWT_SECRET_KEY 未正确配置！"
    exit 1
fi

if [ "$DATABASE_URL" == "[SET_YOUR_DATABASE_URL_HERE]" ] || [ -z "$DATABASE_URL" ]; then
    echo "❌ 错误: DATABASE_URL 未正确配置！"
    exit 1
fi

echo "✅ 环境配置检查通过"

# 切换到项目目录
cd "$PROJECT_DIR"

# 停止已存在的容器
echo "🛑 停止已有容器..."
docker-compose -f docker-compose.prod.yml down 2>/dev/null || true

# 清理未使用的网络
docker network prune -f 2>/dev/null || true

# 构建并启动所有服务
echo "🚀 构建并启动服务..."
docker-compose -f docker-compose.prod.yml up --build -d

# 等待服务启动
echo "⏳ 等待服务健康检查..."
sleep 15

# 检查服务状态
echo ""
echo "=============================================="
echo "  服务状态检查"
echo "=============================================="

# 检查 API
if curl -sf http://localhost:8/api/v1/health/live > /dev/null 2>&1; then
    echo "✅ API 服务 (端口 8000)"
else
    echo "❌ API 服务 - 健康检查失败"
fi

# 检查前端
if curl -sf http://localhost:3 > /dev/null 2>&1; then
    echo "✅ 前端服务 (端口 3000)"
else
    echo "❌ 前端服务 - 健康检查失败"
fi

# 检查数据库
if docker exec webgis-prod-db pg_isready -U webgis_prod > /dev/null 2>&1; then
    echo "✅ PostgreSQL 数据库"
else
    echo "❌ PostgreSQL 数据库 - 未就绪"
fi

# 检查 Redis
if docker exec webgis-prod-redis redis-cli --raw ping > /dev/null 2>&1; then
    echo "✅ Redis 缓存"
else
    echo "❌ Redis - 未就绪"
fi

# 检查 Celery
if docker ps | grep -q webgis-prod-celery; then
    echo "✅ Celery Worker"
else
    echo "❌ Celery Worker - 未运行"
fi

echo ""
echo "=============================================="
echo "  启动完成！"
echo "=============================================="
echo ""
echo "📍 访问地址："
echo "   - HTTP:  http://localhost/"
echo "   - API:   http://localhost:8000/api/v1/"
echo "   - 前端:  http://localhost:3000/"
echo ""
echo "📝 管理命令："
echo "   - 查看日志: docker-compose -f docker-compose.prod.yml logs -f"
echo "   - 停止服务: ./scripts/stop-prod.sh"
echo "   - 重启服务: docker-compose -f docker-compose.prod.yml restart"
echo ""
echo "🛑 如需停止服务，运行: ./scripts/stop-prod.sh"
echo ""