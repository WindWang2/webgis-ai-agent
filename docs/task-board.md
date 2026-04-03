# 任务看板

**最后更新时间**: 2026-04-03 11:12 (Asia/Shanghai)

**当前Coder状态**: 🟢 正在执行M002 Issue检查与修复流程，当前处理#22/#23/#24/#25

## 前端任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| T001 | 技术方案阅读与确认 | ✅ 已完成 | frontend-dev | 2026-03-23 |
| T002 | 搭建前端项目结构 | ✅ 已完成 | frontend-dev | 2026-03-23 |
| T003 | 实现自然语言对话界面 | ✅ 已完成 | frontend-dev | 2026-03-28 合并 |
| T004 | 集成 MapLibre 地图功能 | ✅ 已完成 | frontend-dev | 2026-03-28 提交 |
| T005 | 实现报告生成与预览 | ⏳ 待开始 | frontend-dev | - |

## 后端任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| B001 | 搭建 FastAPI 基础框架 | ✅ 已完成 | backend-dev | 2026-03-23 |
| B002 | 实现 Agent 编排层 | ✅ 已完成 | backend-dev | 2026-03-28 合并 |
| B003 | 数据获取工具开发 | 🔄 进行中 | backend-dev | 核心实现完成，待测试 |
| B004 | 空间分析引擎开发 | ⏳ 待开始 | backend-dev | - |

## 测试任务

| 任务 ID | 任务名称 | 状态 | 负责人 | 备注 |
|--------|---------|------|--------|------|
| TST001 | 编写测试用例 | ⏳ 待开始 | tester | - |
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

## Bug修复任务

| 优先级 | Issue ID | 描述 | 严重级别 | 状态 | 负责人 |
|--------|----------|------|----------|------|--------|
| 1 | #14 | 项目无法启动：缺失关键文件 user_model.py 和 task_queue_service.py | Critical | ⏳ 待分配 | coder |
| 2 | #15 | SECRET_KEY 未配置，JWT 认证完全失效 | Critical | ⏳ 待分配 | coder |
| 3 | #16 | 硬编码数据库凭据 postgres:postgres | Critical | ✅ 已完成 | coder | PR #26已合并 |
| 4 | #17 | 认证绕过：layer 路由使用硬编码假用户 | High | ✅ 已完成 | coder | 已修复合并
| 5 | #18 | CORS 配置 allow_origins=* 与 allow_credentials=True 冲突 | High | ✅ 已完成 | coder | 已修复合并
| 6 | #19 | 全局异常处理器泄露内部错误信息 | High | ⏳ 待分配 | coder |
| 7 | #20 | 模型重复定义导致 SQLAlchemy Base 冲突 | High | ⏳ 待分配 | coder |
| 8 | #21 | bare except 吞掉所有异常（含 KeyboardInterrupt） | High | ✅ 已完成 | WindWang2 | PR #29已合并：<https://github.com/WindWang2/webgis-ai-agent/pull/29> |
9 | #22 | requirements.txt 缺少 python-jose 和 passlib 依赖 | Medium | 🔄 处理中 | coder | 已分配到M002任务 |
10 | #23 | Celery 任务中数据库会话泄漏风险 | Medium | 🔄 处理中 | coder | 已分配到M002任务 |
11 | #24 | SSE 进度轮询无超时保护 | Medium | 🔄 处理中 | coder | 已分配到M002任务 |
12 | #25 | 密码策略过弱 + 测试覆盖极低 + health check 不做实际检查 | Low | 🔄 处理中 | coder | 已分配到M002任务 |
| 9 | #22 | requirements.txt 缺少 python-jose 和 passlib 依赖 | Medium | ⏳ 待分配 | coder |
| 10 | #23 | Celery 任务中数据库会话泄漏风险 | Medium | ⏳ 待分配 | coder |
| 11 | #24 | SSE 进度轮询无超时保护 | Medium | ⏳ 待分配 | coder |
| 12 | #25 | 密码策略过弱 + 测试覆盖极低 + health check 不做实际检查 | Low | ⏳ 待分配 | coder |

## 待办任务

| 优先级 | 任务 ID | 描述 | 状态 | 负责人 |
|--------|--------|------|------|--------|
| 1 | M001 | PR审核流程 | ✅ 已完成 | coder | PR #29已合并：<https://github.com/WindWang2/webgis-ai-agent/pull/29>
| 2 | M002 | Issue检查与修复流程 | 🔄 进行中 | coder | 处理Issue #19/#20/#22/#23/#24/#25
| 3 | T005 | 实现报告生成与预览 | ⏳ 待开始 | frontend-dev |
| 4 | B004 | 空间分析引擎开发 | ⏳ 待开始 | backend-dev |
