# WebGIS AI Agent - 开发者本地调试手册 (V2.1)

本项目经历了底层的核心演变，当前架构深度结合了**一切皆 Agent (Everything is Agent)** 的哲学。系统通过 **Agent CNS (中枢神经系统)** 实现感官同步与全称异步计算。请仔细遵循以下 V2.1 特别启动流程。

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

## 2. 三轨并行启动方案 (Agent CNS Core)

有别于以往单体应用，本地联调必须保证**这三个终端处于并发在线状态**，缺一不可！

### 终端一: Redis 与 Celery 超算后台 (必须)
所有 GIS 的坐标切割运算都被下放在此，不启动本服务系统会假死卡顿。
```bash
# 请确保你的机器装有本机 Redis Server 并处于启动态
redis-server & 

# 激活 Python 虚拟空间
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 拉起超算节点 (执行肌肉)
celery -A main.celery_app worker --loglevel=info
```

### 终端二: FastAPI 中枢神经系统 (路由与大模型流中转)
```bash
# (同在前文的虚拟空间内)
python main.py
# 或者使用 uvicorn 直接启动 (默认端口 8001)
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

### 终端三: Next.js 原生渲染极客桌面
```bash
cd frontend
npm install

# 拉起包含 MapLibre GPU 组件的前台
npm run dev
# -> Local: http://localhost:3000
```

## 3. 开发校验与提交铁律

1. **绝对禁传大数**: 在编写新的 Python Backend Tool 时，绝不允许把十万个地图图元当结果直接打包塞给 LLM 返回通道。必须使用系统的 `session_data` 转存为引用 ID 后流转。
2. **强制使用并发锁**: 前端由于 MapLibre 的 Style 刷新极度频密，切忌引发无条件的 React Rendering Loop，操作图层栈必须有 `try-catch` 包裹。

## 代码分支协作说明 

- `master`： 经历过严格审核的线上唯一真理源（V2.0 Core 已锁闭入此）。
- `feature/*`： 新算子与面板开发分支（请由 master 剥离）。

欢迎进入新时代的 GIS 大规模智能演算领域！探索愉快！
