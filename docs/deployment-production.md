# WebGIS AI Agent 生产环境部署操作手册

## 概述

本文档描述如何将 WebGIS AI Agent 部署到生产环境。

---

## 一、环境准备

### 1.1 硬件要求

| 资源配置 | 最低要求 | 推荐配置 |
|---------|----------|------------|
| CPU | 2 核 | 4+ 核 |
| 内存 | 4 GB | 8+ GB |
| 磁盘 | 20 GB | 50+ GB SSD |
| 系统 | Ubuntu 22.04 LTS / Debian 12 | - |

### 1.2 软件要求

- Docker Engine ≥ 24.0
- Docker Compose ≥ 2.24
- Git ≥ 2.34

---

## 二、安全配置

### 2.1 创建生产环境配置

```bash
# 1. 复制生产环境配置模板
cp .env.prod.example .env.prod

# 2. 编辑生产环境配置，修改以下敏感项：
nano .env.prod
```

必需修改的项目：

```
JWT_SECRET_KEY=      # 必须设置至少 32 位随机字符串
DATABASE_URL=       # 生产数据库连接地址
```

### 2.2 生成安全的 JWT 密钥

```bash
# 方法 1: openssl 生成
openssl rand -base64 32

# 方法 2: python 生成
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2.3 配置数据库（如使用外部数据库）

建议使用云服务商托管的 PostgreSQL（AWS RDS、Google Cloud SQL 等）。

如自行托管：

```bash
# 建议的安全配置
ALTER SYSTEM SET max_connections = 100;
ALTER SYSTEM SET shared_buffers = '128MB';
```

---

## 三、Docker 部署

### 3.1 构建生产镜像

```bash
# 方式 A: 本地构建
docker build -f Dockerfile.prod -t webgis-prod:latest .

# 方式 B: 加载 CI 产出的镜像
docker load < webgis-image.tar
```

### 3.2 启动服务

```bash
# 确保端口未被占用
# API: 18000（前）8000（容器）
# Frontend: 13000（前）3000（容器）

# 启动所有服务
docker-compose -f docker-compose.prod.yml up -d

# 查看服务状态
docker-compose -f docker-compose.prod.yml ps

# 查看日志
docker-compose -f docker-compose.prod.yml logs -f
```

### 3.3 健康检查

```bash
# API 健康检查
curl http://localhost:18000/api/v1/health/live

# 预期返回:
{
  "status": "operational",
  "database": "connected",
  "redis": "connected"
}
```

---

## 四、可选：监控配置

### 4.1 Prometheus + Grafana（如启用了监控服务）

访问：http://your-server-ip:13000

- 默认管理员：admin/admin123

### 4.2 添加自定义 AlertManager

Prometheus 告警规则位于：`deploy/alert-rules.json`

可以集成以下告警渠道：
- Email（SMTP）
- 钉钉/飞书 Webhook
- PagerDuty/OpsGenie

---

## 五、日常运维

### 5.1 常用命令

```bash
# 重启服务
docker-compose -f docker-compose.prod.yml restart api

# 查看实时日志
docker-compose -f docker-compose.prod.yml logs -f api

# 进入容器
docker exec -it webgis-prod-api sh

# 重新构建并部署
docker-compose -f docker-compose.prod.yml build --no-cache
docker-compose -f docker-compose.prod.yml up -d

# 停机维护
docker-compose -f docker-compose.prod.yml down

# 完全清除（包括数据卷）
docker-compose -f docker-compose.prod.yml down -v
```

### 5.2 数据备份

```bash
# PostgreSQL 备份
docker exec webgis-prod-db pg_dump -U webgis_prod -d webgis_prod > backup_$(date +%Y%m%d).sql

# Redis 备份（RDB 快照）
docker exec webgis-prod-redis redis-cli BGSAVE
docker cp webgis-prod-redis:/data/dump.rbd ./redis_backup/
```

### 5.3 日志管理

日志保存在 `./logs/` 目录：

- `app.log` - 主应用日志
- `app_api.log` - API 请求日志  
- `app_db.log` - 数据库操作日志

日志文件每日轮转，最多保留 14 天。

---

## 六、回滚操作

### 6.1 手动回滚

```bash
# 停止当前版本
docker-compose -f docker-compose.prod.yml down

# 检出上一个版本
git checkout <previous-tag>

# 重新构建
docker build -f Dockerfile.prod -t webgis-prod:latest .
docker-compose -f docker-compose.prod.yml up -d
```

### 6.2 自动回滚（CI 触发）

```bash
# GitHub Actions 手动触发回滚
gh run run <workflow-id> --job-name "Rollback Deployment"
```

---

## 七、安全加固

### 7.1 防火墙配置

```bash
# 仅开放必要端口
sudo ufw allow 18000/tcp  # API
sudo ufw allow 13000/tcp  # Frontend
sudo ufw enable
```

### 7.2 定期更新

```bash
# 更新基础镜像（每月一次）
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d

# 运行安全扫描
# 见下方安全命令
```

### 7.3 安全最佳实践

1. **永不提交敏感文件**：
   - `.env.prod`
   - SSL 证书
   - JWT 密钥
   
2. **定期轮换密钥**：
   ```bash
   # 每年轮换一次 JWT 密钥
   # 更新 .env.prod 中的 JWT_SECRET_KEY
   ```

3. **审计日志**：
   ```bash
   # 查看失败登录尝试
   grep -i "failed\|invalid\|unauthorized" ./logs/app.log | tail -50
   ```

---

## 八、故障排查

### 8.1 服务启动失败

```bash
# 1. 查看详细日志
docker-compose -f docker-compose.prod.yml logs api

# 2. 检查端口占用
netstat -tlnp | grep -E '18000|13000'

# 3. 检查数据库连接
docker exec webgis-prod-api nc -zv db 5432
```

### 8.2 性能问题

```bash
# 查看资源使用
docker stats

# 如发现内存不足，增加
# docker-compose.override.yml 中修改 mem_limit
```

### 8.3 数据恢复

```bash
# 1. 停止服务
docker-compose -f docker-compose.prod.yml down

# 2. 恢复数据库
docker exec -i webgis-prod-db psql -U webgis_prod < backup_file.sql

# 3. 重启服务
docker-compose -f docker-compose.prod.yml up -d
```

---

## 九、CI/CD 流水线说明

### 9.1 工作流触发条件

| 触发事件 | 执行的 Job |
|-----------|-------------|
| Push 到 main 分支 | 构建 → 生产部署 |
| Pull Request | 构建 → 预览部署 |
| 手动 Workflow Dispatch | 回滚 |
| 定时（每日凌晨 2 点） | 构建检查 |

### 9.2 保护分支

确保 main 分支开启了 Protection Rules：
- Require pull request reviews
- Require status checks to pass
- Block force pushes

---

## 十、快速参考

```bash
# === 最简部署 ===
git clone <repo>
cp .env..prod.example .env.prod
# 编辑 .env.prod 设置敏感值
docker-compose -f docker-compose.prod.yml up -d

# === 验证部署 ===
curl http://localhost:18000/api/v1/health/live

# === 停止服务 ===
docker-compose -f docker-compose.prod.yml down

# === 更新部署 ===
git pull
docker-compose -f docker-compose.prod.yml build --no-cache
docker-compose -f docker-compose.prod.yml up -d
```

---

## 附录

### 相关文件

| 文件 | 说明 |
|-------|-----|
| `.env.prod` | 生产环境变量（含敏感信息，不提交） |
| `.env.prod.example` | 生产环境变量模板 |
| `Dockerfile.prod` | 生产环境 Docker 镜像配置 |
| `docker-compose.prod.yml` | 生产环境服务编排 |
| `deploy/prometheus.yml` | 监控系统配置 |
| `deploy/alert-rules.json` | 告警规则 |
| `.github/workflows/production.yml` | CI/CD 流水线 |

### 联系支持

如有疑问，请提交 Issue 至项目仓库。