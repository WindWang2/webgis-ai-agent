# 任务看板

## 前端任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| T001 | 技术方案阅读与确认 | ✅ 已完成 | frontend-dev | 2026-03-23 |
| T002 | 搭建前端项目结构 | ✅ 已完成 | frontend-dev | 2026-03-23 |
| T003 | 实现自然语言对话界面 | ⏳ 待开始 | frontend-dev | - |
| T004 | 集成 MapLibre 地图功能 | ⏳ 待开始 | frontend-dev | - |
| T005 | 实现报告生成与预览 | ⏳ 待开始 | frontend-dev | - |

## 后端任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| B001 | 搭建 FastAPI 基础框架 | ✅ 已完成 | backend-dev | 2026-03-23 |
| T002 | 地图图层管理 API | ✅ 已完成 | backend-dev | PR #4 |
| T003 | 空间分析任务队列 | ✅ 已完成 | backend-dev | PR #5 |
| B002 | 实现 Agent 编排层 | ⏳ 待开始 | backend-dev | - |
| B003 | 数据获取工具开发 | ⏳ 待开始 | backend-dev | - |
| B004 | 空间分析引擎开发 | ⏳ 待开始 | backend-dev | - |

## 测试任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| TST001 | 第一轮基础测试 | ✅ 已完成 | tester | 2026-03-23 发现 3 个问题 |
| TST002 | 端到端测试 | ⏳ 待开始 | tester | - |

## 实验任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| EXP001 | RAG 效果评估 | ⏳ 待开始 | experimenter | - |
| EXP002 | Agent 任务拆解准确率测试 | ⏳ 待开始 | experimenter | - |

---

## 任务详情

### T002 - 搭建前端项目结构

**状态**: ✅ 已完成

**完成时间**: 2026-03-23 17:30

**完成内容**:
- ✅ 创建 Next.js 14 + TypeScript 项目
- ✅ 配置 Tailwind CSS
- ✅ 配置 shadcn/ui 基础结构
- ✅ 集成 MapLibre GL JS
- ✅ 实现三栏布局基础框架（对话面板 + 地图面板 + 结果面板）
- ✅ 编写 Dockerfile（支持生产构建和开发热重载）
- ✅ 创建 git 仓库并推送到 feature/frontend-scaffold 分支

**技术栈**:
- Next.js 14 + React 18
- TypeScript
- Tailwind CSS
- shadcn/ui
- MapLibre GL JS + react-map-gl
- Docker

**Git 信息**:
- 分支：`feature/frontend-scaffold`
- 仓库：https://github.com/WindWang2/webgis-ai-agent
- PR: https://github.com/WindWang2/webgis-ai-agent/pull/2 ✅ 已创建

**验证方式**:
```bash
cd ~/projects/webgis-ai-agent/frontend
npm run dev  # 访问 http://localhost:3000
```

---

### B001 - 搭建后端项目结构

**状态**: ✅ 已完成

**描述**: 创建 FastAPI 脚手架，包含基础路由、健康检查接口，Docker 配置

**完成时间**: 2026-03-23

**提交**: feature/backend-scaffold 分支

---

## 测试任务详情

### TST001 - 第一轮基础测试

**状态**: ✅ 已完成

**完成时间**: 2026-03-23 20:22

**测试内容**:
- ✅ 后端服务健康检查 - 通过
- ✅ /health 接口验证 - 通过
- ❌ 项目目录结构验证 - 发现问题
- ❌ Docker 镜像构建 - 权限不足

**发现问题**:
| 编号 | 问题 | 严重程度 |
|------|------|----------|
| BUG-001 | 项目目录结构混乱 | 高 |
| BUG-002 | Dockerfile 引用错误 | 高 |
| BUG-003 | Docker 构建权限不足 | 中 |

**测试报告**: docs/task-logs/tester/base-test-001.md

---

## 待办任务

| 任务 ID | 描述 | 状态 | 负责人 |
|--------|------|------|--------|
| T003 | 实现自然语言对话界面 | ⏳ 待开始 | frontend-dev |
| T004 | 集成 MapLibre 地图功能 | ⏳ 待开始 | frontend-dev |
| B002 | 实现 Agent 编排层 | ⏳ 待开始 | backend-dev |
| B003 | 数据获取工具开发 | ⏳ 待开始 | backend-dev |
