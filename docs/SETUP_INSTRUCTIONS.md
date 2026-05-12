# WebGIS AI Agent - 开发者本地调试手册 (V3.2)

本项目经历了底层的核心演变，当前架构深度结合了**一切皆 Agent (Everything is Agent)** 的哲学。系统通过 **Agent CNS (中枢神经系统)** 实现感官同步与全称异步计算。请仔细遵循以下 V3.2 特别启动流程。

## 1. 代码拉取与配置挂载

```bash
git clone https://github.com/WindWang2/webgis-ai-agent.git
cd webgis-ai-agent

# 基于母带构建自己的凭证档案
cp .env.example .env
```

打开 `.env` 文件补充以下必须的核心密码：
- `CLAUDE_API_KEY`: 支持体系化工具调用的 Anthropic 密钥
- `REDIS_URL`: 连接凭证 (默认 `redis://localhost:6379/0`)
- `HTTP_PROXY` / `HTTPS_PROXY`: (可选) 如果 Nominatim 或 OSM 服务连接不稳定，可配置代理

> [!TIP]
> 如果你在使用地理编码 (Geocoding) 或 OSM 搜索时遇到 SSL 报错或无法连接，建议正确配置 `HTTPS_PROXY`。系统已内置跨平台 SSL 证书修复机制 (Certifi)。

## 2. 基础设施一键诊断 (Agent CNS Health Check)

在启动任何服务之前，强烈建议运行诊断工具确保你的 Redis、数据库和 LLM API 处于就绪状态：

```bash
python manage.py check
```

该工具会输出一个专业的状态面板。如果 Redis 或 Celery Worker 离线，面板会给出明确的红色警示。

## 3. 全栈一键启动方案 (NEW!)

为了简化开发流程，V3.2 引入了统一的开发指令。你不再需要手动开启三个终端，只需一个指令即可拉起整个 CNS 生态位：

```bash
python manage.py dev
```

该指令会同时启动：
- **FastAPI Backend** (Port 8001, with hot-reload)
- **Celery Worker** (Background spatial compute)
- **Next.js Frontend** (Port 3000)

> [!NOTE]
> 如果你更喜欢手动控制每个组件，依然可以使用 `python manage.py server` 和 `python manage.py worker`。

## 3. 开发校验与提交铁律

1. **绝对禁传大数**: 在编写新的 Python Backend Tool 时，绝不允许把十万个地图图元当结果直接打包塞给 LLM 返回通道。必须使用系统的 `session_data` 转存为引用 ID 后流转。
2. **强制使用并发锁**: 前端由于 MapLibre 的 Style 刷新极度频密，切忌引发无条件的 React Rendering Loop，操作图层栈必须有 `try-catch` 包裹。

## 代码分支协作说明 

- `master`： 经历过严格审核的线上唯一真理源（V2.0 Core 已锁闭入此）。
- `feature/*`： 新算子与面板开发分支（请由 master 剥离）。

欢迎进入新时代的 GIS 大规模智能演算领域！探索愉快！
