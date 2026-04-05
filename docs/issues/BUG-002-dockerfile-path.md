# BUG-002: Dockerfile 引用不存在的 backend/目录

## 问题描述

Dockerfile 中引用了 `backend/` 目录，但该目录不存在，导致 Docker 构建失败。

## 错误信息

Dockerfile 中的引用:
```dockerfile
# Stage 3: Backend Dependencies
FROM python:3.11-slim AS backend-deps
WORKDIR /app/backend
COPY backend/requirements.txt ./
```

实际目录结构:
```
webgis-ai-agent/
├── frontend/app/    # 实际后端代码在这里
└── backend/         # ❌ 不存在
```

## 影响

- Docker 镜像无法构建
- 无法进行容器化部署
- 阻塞 CI/CD 流程

## 建议修复

1. 创建 `backend/` 目录结构
2. 移动后端代码到正确位置
3. 或更新 Dockerfile 引用路径

## 优先级

🔴 高 - 阻塞部署流程

## 关联

- BUG-001: 项目目录结构混乱
- 测试任务：TST001

---
**报告人**: Tester-Agent
**日期**: 2026-03-23
