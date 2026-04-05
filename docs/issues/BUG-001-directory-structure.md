# BUG-001: 项目目录结构混乱，后端代码错误放置在 frontend/app

## 问题描述

项目目录结构不符合前后端分离规范，后端 FastAPI 代码错误地放置在 `frontend/app/` 目录中，导致：

1. Dockerfile 引用的 `backend/` 目录不存在
2. 前后端代码混在一起，不符合项目架构规范
3. 根目录 `main.py` 为空骨架文件

## 当前结构

```
webgis-ai-agent/
├── app/              # 空目录
├── frontend/         # ❌ 后端代码错误放在这里
│   └── app/         # Python 后端代码 (main.py, api/, core/)
├── Dockerfile       # 引用 backend/ 但不存在
└── main.py          # 空骨架
```

## 期望结构

```
webgis-ai-agent/
├── backend/         # ✅ FastAPI 后端代码
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   └── core/
│   └── requirements.txt
├── frontend/        # Next.js 前端代码
│   ├── app/
│   ├── components/
│   └── package.json
├── Dockerfile
└── main.py
```

## 影响

- Docker 构建失败
- 项目结构混乱，不利于维护
- 前后端职责不清

## 建议修复

1. 创建 `backend/` 目录
2. 将 `frontend/app/` 中的 Python 代码移至 `backend/app/`
3. 更新 Dockerfile 路径引用
4. 更新所有 import 路径

## 优先级

🔴 高 - 阻塞 Docker 构建和后续开发

## 关联

- 测试任务：TST001
- 测试报告：docs/task-logs/tester/base-test-001.md

---
**报告人**: Tester-Agent
**日期**: 2026-03-23
