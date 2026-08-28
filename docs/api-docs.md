# WebGIS AI Agent API 参考

后端 FastAPI 服务的 HTTP/SSE/WebSocket 接口参考，按资源分组；端点与字段均对齐 `app/api/routes/` 下的实现。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17

## 目录

- [概述与通用约定](#概述与通用约定)
- [认证 Auth](#认证-auth)
- [对话 Chat（SSE 流式）](#对话-chatsse-流式)
- [统一任务中心 /tasks/jobs（ADR-0052）](#统一任务中心-tasksjobsadr-0052)
- [内存任务与 Celery 任务 /tasks](#内存任务与-celery-任务-tasks)
- [图层数据零拷贝提取 /layers](#图层数据零拷贝提取-layers)
- [栅格图片 /sessions/{sid}/raster](#栅格图片-sessionssidraster)
- [探索引擎 /explorer](#探索引擎-explorer)
- [数据上传 /upload](#数据上传-upload)
- [报告 /reports](#报告-reports)
- [地图导出 /export](#地图导出-export)
- [制图模板 /templates](#制图模板-templates)
- [知识库 /knowledge（RAG）](#知识库-knowledgerag)
- [项目工作区 /projects](#项目工作区-projects)
- [数据织网 /data-fabric](#数据织网-data-fabric)
- [系统配置 /config](#系统配置-config)
- [静态文件 /static](#静态文件-static)
- [性能遥测 /metrics](#性能遥测-metrics)
- [Pi 工具回调 /pi-tools](#pi-工具回调-pi-tools)
- [SSE 事件目录](#sse-事件目录)
- [WebSocket /ws](#websocket-ws)
- [健康与监控](#健康与监控)
- [附录：地图指令目录契约](#附录地图指令目录契约)

## 概述与通用约定

- **前缀**：除 Prometheus `/metrics` 与 Pi 工具回调 `/pi-tools` 外，所有 API 挂载在 `/api/v1` 下（见 `app/main.py`）。
- **服务端口**：uvicorn 默认 `8000`；生产镜像内同容器还运行 Next.js standalone（端口 `3000`），由 nginx 分流 `/api/` 与 `/`。
- **交互式文档**：非生产环境（`ENV != production`）启动后，Swagger UI 在 `/docs`、ReDoc 在 `/redoc`。生产环境关闭这两个端点。字段级 schema 以 OpenAPI 为准，本文只覆盖关键语义。
- **认证**（两级）：
  - **JWT**：`Authorization: Bearer <access_token>`。access token 30 分钟、refresh token 7 天；payload 携带 `ver` claim 与 `User.token_version` 绑定，logout 后旧 token 立即失效。
  - **匿名会话**：新建匿名会话时后端铸造 `owner_token`，客户端通过 `X-Session-Token` 请求头回传。匿名数据（会话、ref 数据、上传）按 owner_token 分桶隔离，归属校验失败一律 404（不泄露存在性）。
- **错误格式**（两套并存，消费方需兼容）：
  1. 路由内 `HTTPException`（最常见）→ FastAPI 原生体 `{"detail": "..."}`，状态码即 HTTP 码。
  2. 未捕获异常 → 全局处理器（`app/core/exception.py`）返回 `{"code", "success": false, "message", "data": null}`；生产环境 message 固定为通用文案，非生产环境附带 `error_type / traceback` 等调试字段。部分资源（knowledge、reports 等）正常响应也使用 `ApiResponse` 信封 `{code, success, message, data}`。
- **限流**（Redis 后端、内存兜底）：
  - 全局：每客户端 IP 60 次 / 60 秒（`/docs`、`/redoc`、`/openapi.json` 豁免），超限 429。
  - 登录失败：每 IP 5 次 / 5 分钟；注册：每 IP 5 次 / 小时；refresh：每用户 30 次 / 5 分钟；WebSocket 连接：每 IP 5 次 / 60 秒。
- **CORS**：允许的请求头包含 `Authorization`、`Content-Type`、`X-Session-Token`；来源由 `CORS_ORIGINS` 配置（JSON 数组格式）。

## 认证 Auth

注册默认关闭（`ALLOW_PUBLIC_REGISTER` 未设为 `true` 时返回 503）；生产用 `manage.py create-admin` CLI 建号。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/auth/register` | 无 | 注册并返回 token 对（默认 503 关闭；开放时每 IP 5 次/小时） |
| POST | `/api/v1/auth/login` | 无 | 用户名或邮箱 + 密码；返回 access + refresh token |
| POST | `/api/v1/auth/refresh` | 无 | 用 refresh token 换新 token 对（拒绝 access token 冒用；校验 `ver`） |
| POST | `/api/v1/auth/logout` | Bearer | bump `token_version`，该用户全部旧 token 失效（logout-everywhere 语义） |
| GET | `/api/v1/auth/me` | Bearer | 当前用户信息（实时校验 token 版本） |

登录请求体与响应示例：

```json
// POST /api/v1/auth/login
{"identifier": "admin", "password": "********"}

// 200
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {"id": "...", "username": "admin", "email": "...", "full_name": null, "role": "admin"}
}
```

## 对话 Chat（SSE 流式）

### 会话与地图状态

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/chat/sessions` | optional | 当前用户会话列表（匿名返回空）；`limit`（默认 50，≤200）+ `offset` 分页 |
| GET | `/api/v1/chat/sessions/{session_id}` | optional（校验归属） | 会话详情含 user/assistant 消息 |
| DELETE | `/api/v1/chat/sessions/{session_id}` | optional（校验归属） | 删除会话（DB + 会话缓存 + 盘上 mapspec/raster 状态，并 abort Pi 子进程在途 turn） |
| GET | `/api/v1/chat/sessions/{session_id}/map-state` | optional（校验归属） | 读取持久化地图状态（viewport / layers / base_layer） |
| POST | `/api/v1/chat/sessions/{session_id}/map-state` | optional（校验归属） | 推送地图状态，成功 204 |
| POST | `/api/v1/chat/sessions/{session_id}/cartographic-observation` | optional（校验归属） | 上报制图运行时观测证据（MapSpec 对账后触发，载荷上限 256KB） |
| POST | `/api/v1/chat/sessions/{session_id}/map-action-ack` | optional（校验归属） | 地图动作终态 ACK（按 `action_id` 幂等，每会话上限 200 条） |
| GET | `/api/v1/chat/skills` | optional | 列出可用 `.md` 技能（仅 name/description 元数据） |
| GET | `/api/v1/chat/tools` | required | 列出全部工具 schema（含 tier-3 参数，需认证） |
| POST | `/api/v1/chat/tools/execute` | admin | 直接执行单个工具；tier-3 危险工具需 `confirm_destructive: true` |

### 流式 / 非流式对话

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/chat/stream` | optional | SSE 流式对话（核心交互入口，见下） |
| POST | `/api/v1/chat/completions` | optional | 非流式对话，返回 `ChatResponse{session_id, content}` |

`ChatRequest` 请求体（两端点共用）：

```json
{
  "message": "分析北京五环内的公园分布",
  "session_id": "可选；省略时由服务端创建",
  "map_state": {"可选：当前视角/图层级快照"},
  "skill_name": "可选：激活的技能名",
  "project_id": "可选：关联项目工作区"
}
```

`/chat/stream` 响应为 `text/event-stream`（响应头含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`）。事件目录见[下文](#sse-事件目录)。**断线续传（DUP-1）**：每个事件携带单调递增的 SSE `id:`；客户端重连时以 `Last-Event-ID` 请求头或 `last_event_id` 查询参数带回最后收到的 id，服务端重放该 turn 缓冲的缺失事件后终止（不重新执行 prompt）。

请求示例：

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "上海人口热力图", "session_id": "my-session"}'
```

流式输出示意（节选）：

```text
event: session
data: {"session_id": "my-session", "owner_token": "…仅新建会话时下发一次…"}

event: token
id: 1
data: {"content": "正在", "is_reasoning": false, "session_id": "my-session"}

event: tool_call
id: 2
data: {"name": "analyze_population", "arguments": {"city": "上海"}, "session_id": "my-session"}

event: done
id: 7
data: {"session_id": "my-session"}
```

## 统一任务中心 /tasks/jobs（ADR-0052）

durable GIS job（数据库行）与内存 agent task（`task-xxxx` id）的**统一视图**：同一列表、同一 `JobView` 形状，浏览器刷新 / API 重启后任务中心仍可恢复。路由文件 `app/api/routes/jobs.py`；该 router 必须先于 `/tasks/{task_id}` 注册（否则字面量 `jobs` 会被当成 task_id）。

**归属证明**（三条链任一命中）：已认证 user_id / 匿名 owner_token（`X-Session-Token`）/ 已验证归属的 `session_id` 查询参数。三者皆无则看不到任何 job；不存在与无权一律 404。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/tasks/jobs` | optional | 统一任务列表 |
| GET | `/api/v1/tasks/jobs/{job_id}` | optional | 查询单个 job；durable 数字 id 与 agent `task-xxxx` id 都接受 |
| DELETE | `/api/v1/tasks/jobs/{job_id}` | optional | 请求取消（幂等） |
| POST | `/api/v1/tasks/jobs/{job_id}/retry` | optional | 重试 failed/stale 的 durable job（agent task 重试语义是重发消息，返回 400） |

**GET /api/v1/tasks/jobs 查询参数**：

- `session_id`：按会话过滤；匿名调用方必须提供（否则无归属证明，返回空列表）。
- `active_only`（默认 false）：只返回未终结 job。
- `limit`（1–200，默认 50）。

响应（`JobListResponse`）：

```json
{
  "jobs": [
    {
      "id": "1042",
      "kind": "analysis",
      "name": "NDVI 分析 · chunk_0012.tif",
      "status": "running",
      "progress": 45,
      "message": "正在计算植被指数",
      "cancellable": true,
      "retryable": false,
      "active": true,
      "attempt": 1,
      "session_id": "my-session",
      "project_id": null,
      "agent_task_id": "task-1a2b3c4d",
      "agent_step_id": null,
      "background_job_ids": [],
      "error": null,
      "result_ref": null,
      "step_count": 0,
      "created_at": "2026-08-17T08:00:00+00:00",
      "started_at": "2026-08-17T08:00:01+00:00",
      "finished_at": null,
      "cancel_requested_at": null
    }
  ],
  "has_active": true,
  "poll_after_ms": 3000
}
```

语义要点（规范 §5/§17/§32/§35，实现见 `app/services/jobs/`）：

- `status` 取值：`pending | queued | running | cancelling | completed | failed | cancelled | stale`。
- `error` 是单行脱敏摘要（首行截断 500 字符），**绝不**包含 traceback、worker 内部信息或用户原文。
- `has_active` 基于全量归属范围计算（不被 limit 截断影响）；`poll_after_ms=3000` 表示建议继续轮询，`null` 表示停止。
- 取消是两段式：先把 `cancel_requested_at` 落库（进程重启不丢），再点燃本进程取消 token；跨进程 worker 靠 DB 探针感知。重复取消返回 200 且 `cancel_requested=false`；终态 job 取消是 no-op。
- 重试创建**新 attempt**（`attempt` 递增），保留首次失败的 `error_trace` 证据；被取消的 job 永不重试。

## 内存任务与 Celery 任务 /tasks

旧任务端点（expand-contract 中保留的 contract 侧），与 `/tasks/jobs` 并存、语义不变。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/tasks/{task_id}` | required | 内存 agent task 状态与步骤详情（校验归属） |
| GET | `/api/v1/tasks` | required | 按 `session_id`（必填）列任务，校验归属 |
| DELETE | `/api/v1/tasks/{task_id}` | required | 取消 agent task，并级联取消该 turn 派生的 durable job |
| GET | `/api/v1/tasks/status/{task_id}` | required | Celery 原生任务状态；存在 durable 行时补充 `job_id / durable_status / progress` |
| DELETE | `/api/v1/tasks/status/{task_id}` | required | 撤销 Celery 任务（先协作取消、落库，再 revoke 兜底） |

注意：`GET /api/v1/tasks/{task_id}` 与 `/tasks/jobs` 的路由顺序由 main.py 注册顺序保证，`jobs` 不会被误当作 task_id。

## 图层数据零拷贝提取 /layers

Agent 工具产生的大 GeoJSON / 栅格数据不随聊天响应下发，而是存入会话存储并返回 `ref_id` 游标，前端按需提货（Fetch-on-Demand）。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/layers/data/{ref_id}` | optional（会话归属） | 提取 ref 数据原始 JSON payload |
| GET | `/api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt` | optional（会话归属） | 矢量 MVT 瓦片（z 0–20；gzip + ETag，支持 If-None-Match 304） |
| GET | `/api/v1/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png` | optional（会话归属） | 栅格 XYZ PNG 瓦片 |
| GET | `/api/v1/layers/descriptor/{ref_id}` | optional（会话归属） | 轻量元数据（feature_count / geometry_types / bbox / mvt_capable / estimated_bytes） |
| GET | `/api/v1/layer-types` | 无 | 图层与分析类型枚举 |

公共约定：`session_id` 为必填 query（8–128 字符）；匿名会话用 `X-Session-Token` 头证明归属；`ref_id` 也接受已注册的别名。鉴权失败 403（PermissionDenied）、数据不存在或已逐出 404。`descriptor` 端点读取 store 时预计算的元数据（O(1)），不回读全量 payload。

请求示例：

```bash
curl "http://localhost:8000/api/v1/layers/data/ref:data-1a2b3c4d?session_id=my-session" \
  -H "X-Session-Token: $OWNER_TOKEN"
```

### 浏览器原生请求的凭据契约（瓦片 / 下载 / 图片）

浏览器原生请求（MapLibre 瓦片拉取、`<a>` 下载、`<img>`/`<iframe>` 嵌入）**无法携带请求头**，而这些端点只做 header 鉴权（`Authorization: Bearer` 或 `X-Session-Token`）。前端已统一解决，API 直连消费者需遵守同一契约：

- **MVT/栅格瓦片**：前端经 MapLibre `transformRequest` 对 first-party 请求注入与 `apiFetch` 相同的凭据（逐请求读取最新 token）。
- **导出/报告下载与聊天内嵌图片**：一律走认证 blob 传输层（`apiFetchBlob`，401 自动 refresh + Content-Disposition 文件名），不使用裸 `<a href>`。
- **匿名会话图片类 URL**：可附 `?token=<owner_token>`（如 `/sessions/{sid}/raster/{id}.png`；仅限图片类资源，owner_token 不进入聊天文本）。

## 栅格图片 /sessions/{sid}/raster

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/sessions/{session_id}/raster/{raster_id}.png` | optional（会话归属） | 提供 MapSpec `type:"raster"` 源的渲染 PNG |

MapLibre `image` source 无法携带请求头，因此除 `X-Session-Token` 外还接受 `?token=<owner_token>` 查询参数兜底。`session_id` / `raster_id` 校验为纯标识符字符集（防路径穿越）。

## 探索引擎 /explorer

深度探索链：意图 → 发现 → 抓取 → 解析 → 验证 → 地理编码。Agent 工具 `deep_explore` 内部走同一链路。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/explorer/start` | **required** | 启动探索链；body `{query, session_id?, expected_data_type="poi_list", source_hint=[], auto_threshold=0.7}` |
| GET | `/api/v1/explorer/status/{task_id}` | required | 链状态 `{task_id, status, progress, result?}`（属主校验跨重启持久） |
| POST | `/api/v1/explorer/abort/{task_id}` | required | 中止链（撤销全部阶段任务，重启后仍有效） |
| GET | `/api/v1/explorer/stream/{task_id}` | required | 独立 SSE 进度流（`explorer_progress` 事件 + `heartbeat`） |

登录会话可经独立流获取进度；匿名会话没有属主，进度由聊天流桥接（`explorer_progress` 终态事件显式发出，流不会悬挂）。带 `session_id` 启动时校验归属（防消耗他人配额）。

## 数据上传 /upload

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/upload` | required | multipart 上传 GIS 文件 |
| GET | `/api/v1/uploads` | required | 按 `session_id`（必填）列上传记录 |
| GET | `/api/v1/uploads/{upload_id}` | required | 单条记录详情（校验归属） |
| GET | `/api/v1/uploads/{upload_id}/geojson` | required | 取矢量 GeoJSON（≤50MB，超出提示走 `/layers/data/{ref_id}`） |
| DELETE | `/api/v1/uploads/{upload_id}` | required | 删除记录与关联文件（校验归属） |

上传格式与限额：矢量 `.geojson / .json / .shp(zip) / .kml / .gpkg / .csv(含经纬度列)` ≤ **50MB**；栅格 `.tif / .tiff` ≤ **200MB**。请求为 `multipart/form-data`：`files`（当前处理第一个）、可选 `session_id` 表单域；匿名归属用 `X-Session-Token`。解析在 worker 线程执行，响应含 `upload_id / original_name / format / crs / geometry_type / feature_count / bbox / file_size`。

## 报告 /reports

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/reports` | required | 从会话历史生成报告；body `{session_id, format: pdf|html|markdown, title?}`（会话归属校验） |
| GET | `/api/v1/reports` | required | 列出报告；`session_id` 必填（否则拒绝，防跨租户泄漏） |
| GET | `/api/v1/reports/{report_id}` | required | 报告详情（经 session 归属链校验） |
| GET | `/api/v1/reports/{report_id}/download` | required | 下载报告文件 |
| POST | `/api/v1/reports/{report_id}/share` | required | 生成分享码 |
| GET | `/api/v1/reports/shared/{share_code}` | 无 | 分享码查报告信息（校验过期） |
| GET | `/api/v1/reports/shared/{share_code}/view` | 无 | 分享码查看/下载文件（html 内联渲染，其余 FileResponse） |

正常与失败响应均走 `ApiResponse` 信封。

## 地图导出 /export

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/export` | required | 接收前端 Canvas 合成 PNG 并持久化，返回下载链接 |
| POST | `/api/v1/export/pdf` | required | 合成 A4 横向 PDF（reportlab 渲染在 worker 线程执行） |
| POST | `/api/v1/export/geojson` | required | 持久化 GeoJSON 为可下载文件 |
| GET | `/api/v1/export/download/{filename}` | required | 下载导出产物（校验文件所有权） |

## 制图模板 /templates

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/api/v1/templates` | optional | 模板列表（basemap / symbology / layout / thematic 四类） |
| GET | `/api/v1/templates/{template_id}` | optional | 模板详情（含 payload） |
| POST | `/api/v1/templates` | optional | 另存为新模板（Save as Template，201） |
| DELETE | `/api/v1/templates/{template_id}` | optional | 删除用户模板（内置模板不可删） |

## 知识库 /knowledge（RAG）

基于本地向量库（sentence-transformers + FAISS）的文档管理与检索，按 user/org 隔离。

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/api/v1/knowledge/documents` | required | 上传文档并做向量嵌入；body `{title, content, file_type="text"}` |
| GET | `/api/v1/knowledge/documents` | required | 文档列表；`limit`（1–100，默认 50）/ `offset` |
| GET | `/api/v1/knowledge/search` | required | 语义搜索；`q` 必填、`top_k`（1–20，默认 5）、可选 `document_id` |
| DELETE | `/api/v1/knowledge/document/{document_id}` | required | 删除文档（仅限本人/本组织） |
| POST | `/api/v1/knowledge/retrieve-context` | required | 供对话引擎使用的上下文检索；body `{query, top_k=3, document_id?}` |

## 项目工作区 /projects

项目 / 数据集 / 工作流 / 产物 / 血缘的 CRUD 与执行（路由文件 `app/api/routes/project.py`，分页统一 `Page[T]`）。

| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST | `/api/v1/projects` | 创建项目（201） |
| GET | `/api/v1/projects` | 项目分页列表 |
| GET / PUT | `/api/v1/projects/{project_id}` | 项目详情 / 更新 |
| POST / GET | `/api/v1/projects/{project_id}/datasets` | 添加 / 列出数据集 |
| DELETE | `/api/v1/projects/{project_id}/datasets/{dataset_id}` | 移除数据集（软删除，保留血缘可解析） |
| GET | `/api/v1/projects/{project_id}/artifacts` | 产物分页列表 |
| POST / GET | `/api/v1/projects/{project_id}/workflows` | 创建 / 列出工作流 |
| GET | `/api/v1/projects/{project_id}/workflows/{workflow_id}/revisions` | 工作流不可变修订列表 |
| GET | `/api/v1/projects/{project_id}/workflows/{workflow_id}/revisions/{revision_id}` | 单个修订详情 |
| POST | `/api/v1/projects/{project_id}/workflows/{workflow_id}/run` | 触发工作流执行 |
| GET | `/api/v1/projects/{project_id}/runs` | 运行历史分页 |
| GET | `/api/v1/projects/{project_id}/runs/{run_id}` | 运行详情 |
| POST | `/api/v1/projects/{project_id}/runs/compare` | 对比两次运行 |
| POST | `/api/v1/projects/{project_id}/runs/{run_id}/replay` | 重放运行 |
| POST | `/api/v1/projects/{project_id}/runs/{run_id}/resume` | 断点续跑（对比输入指纹检测过期） |
| POST | `/api/v1/projects/{project_id}/quality-audit` | 数据质量审计 |
| POST | `/api/v1/projects/{project_id}/repair` | 数据修复 |
| GET | `/api/v1/projects/artifacts/{artifact_id}/lineage` | 产物血缘追溯 |

## 数据织网 /data-fabric

外部空间数据源的注册、编目、查询与按需物化（路由文件 `app/api/routes/data_fabric.py`；数据源按 org/owner 租户隔离，匿名仅见无主全局源）。

| 方法 | 路径 | 说明 |
|------|-----|-----|
| POST / GET | `/api/v1/data-fabric/sources` | 注册 / 列出数据源 |
| GET / DELETE | `/api/v1/data-fabric/sources/{source_id}` | 数据源详情 / 删除 |
| POST | `/api/v1/data-fabric/sources/{source_id}/probe` | 探测连通性与能力 |
| POST | `/api/v1/data-fabric/sources/{source_id}/sync` | 同步目录到 spatial catalog |
| GET | `/api/v1/data-fabric/catalog` | 目录项检索（bbox / 类型 / 关键词过滤） |
| GET | `/api/v1/data-fabric/catalog/{item_id}` | 目录项详情 |
| GET | `/api/v1/data-fabric/catalog/{item_id}/descriptor` | 轻量元数据描述符 |
| GET | `/api/v1/data-fabric/catalog/{item_id}/preview` | 小样本预览 |
| POST | `/api/v1/data-fabric/catalog/{item_id}/query` | 服务端过滤查询（结果超限 413 并附缩减提示） |
| POST | `/api/v1/data-fabric/materialize` | 物化目录项到会话存储并返回 `ref_id`（需认证；store 不可用时 503） |

## 系统配置 /config

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET / POST | `/api/v1/config/llm` | admin | 读取 / 更新 LLM 配置（运行时改 `base_url` 会重跑 SSRF 校验） |
| POST | `/api/v1/config/llm/test` | admin | LLM 连通性测试 |
| POST | `/api/v1/config/rag/test` | admin | 知识库连通性测试 |
| GET | `/api/v1/config/skills` | admin | 列出已加载技能 |
| POST | `/api/v1/config/skills/upload` | admin | 上传技能脚本（AST 校验） |
| POST | `/api/v1/config/skills/refresh` | admin | 热重载技能 |

## 静态文件 /static

| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | `/api/v1/static/{file_path:path}` | DATA_DIR 下文件的受控下发 |

三条放行通道（任一）：`public/` 前缀白名单、**admin** JWT（普通用户 Bearer 不放行任意私有文件）、HMAC 签名 URL（`?sig=&exp=`）。其余一律 404（不暴露存在性）；路径穿越 / 隐藏文件直接 400。

## 性能遥测 /metrics

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| GET | `/metrics`（**无 /api/v1 前缀**） | 无（需网络层隔离） | Prometheus 抓取端点（`http_requests_total`、`http_request_duration_seconds` 等；不进 OpenAPI schema） |
| GET | `/api/v1/metrics/digest` | admin | 工具调用指标、空间分析缓存、Pi harness 遥测摘要 |

`/metrics` 暴露内部流量与延迟分布，部署时必须至少满足其一：NetworkPolicy 限制到监控 namespace、反代 IP 白名单/mTLS、或与 Prometheus 同 namespace 走 ClusterIP。

## Pi 工具回调 /pi-tools

| 方法 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| POST | `/pi-tools/execute`（**无 /api/v1 前缀**） | `X-Pi-Bridge-Secret` 共享密钥 | Pi agent 子进程代为执行 GIS 工具的 HTTP 回调 |

密钥来自 `WEBGIS_BRIDGE_SECRET` 环境变量（或 DATA_DIR 下共享 secret 文件，多 worker 一致）；`hmac.compare_digest` 防时序侧信道。请求还须携带有效 turn token（一次性的 turn 上下文能力令牌），过期/非活跃 turn 返回 401/409。**该端点不面向公网消费方。**

## SSE 事件目录

`/chat/stream`（以及被桥接的 explorer 进度）的事件命名与载荷。事件格式由 `app/utils/sse.py::sse_event` 生成：`event: <type>\n[id: <n>\n]data: <json>\n\n`。

### 流程与结构事件

| 事件名 | 载荷要点 | 说明 |
|--------|---------|-----|
| `session` | `{session_id, owner_token}` | Pi 路径新建会话时下发一次（仅当该会话铸造了 owner_token 时才发） |
| `token` | `{content, is_reasoning, session_id}` | 流式文本增量；`is_reasoning=true` 为思维链 |
| `content` | `{content, session_id}` | Pi 路径的正文增量（不带 reasoning 标记） |
| `tool_call` | `{name, arguments, session_id}` | Agent 发起工具调用 |
| `task_start` | `{task_id, …}` | 内存任务总线开启 |
| `step_start` | `{step_id, tool, …}` | 算子/步骤开始 |
| `step_result` | `{result, has_geojson, …}` | 步骤成功（大结果以 ref_id 引用） |
| `step_error` | `{error, …}` | 步骤失败 |
| `step_cancelled` | `{…}` | 步骤被取消 |
| `tool_result` | `{…}` | 工具执行结果回执 |
| `task_complete` | `{summary, …}` | 任务链完成（终态） |
| `task_error` | `{task_id, error, …}` | 任务异常（终态） |
| `task_cancelled` | `{task_id, …}` | 任务被取消（终态） |
| `done` | `{session_id, resumed?}` | 流结束（终态；resume 重放结束时带 `resumed: true`） |
| `error` | `{error, …}` | 流级错误（终态） |
| `keep_alive` | `{message: "ping"}` 或透传进度 | 长耗时工具执行期间的显式心跳事件 |

### Plan-First 模式事件

| 事件名 | 载荷 | 说明 |
|--------|------|-----|
| `plan_ready` | `{session_id, task_id, intent, domains, steps[]}` | 生成执行计划 |
| `plan_step_done` | `{session_id, task_id, step_n}` | 计划步骤完成 |
| `plan_finalized` | `{session_id, task_id, skipped[]}` | 计划终态（含被跳过步骤） |

### SessionPlan 事件（Pi 路径，仅流式）

Pi host 的计划真相是 SessionPlan 信封（ADR-0076）。三个事件名**只在流式 `/chat/stream` 上、每个工具执行后**发射；`plan_ready`/`plan_step_done`/`plan_finalized` 这三个 CanonicalPlan 名字在 Pi 路径**永不出现**（非流式消费者请直接读 SessionStore 的 `session-plan` 别名）。

| 事件名 | 载荷 | 说明 |
|--------|------|-----|
| `session_plan_updated` | `{session_id, envelope_id, plan_id, recipe_id, query, replaced}` | GIS 章节写入/替换（新目标为 supersede + 新信封） |
| `session_plan_progress` | `{session_id, envelope_id, capability, status, bound_ref}` | 能力完成/置空（能力形状，非工具步骤） |
| `session_plan_superseded` | `{session_id, old_envelope_id, envelope_id, previous_query, query}` | 用户目标变更，旧信封归档 |

### 探索引擎事件

| 事件名 | 载荷 | 说明 |
|--------|------|-----|
| `explorer_progress` | `{task_id, stage, progress, message, …}` | 探索链各阶段进度与终态（登录会话亦可经 `/explorer/stream/{task_id}` 独立流获取） |
| `heartbeat` | `{ts}` | explorer 独立流的周期心跳 |

### 传输细节

- **心跳**：Agent 后台调用 Celery 长计算时，SSE 网关周期性推送 `keep_alive` 事件或 SSE 注释行（`: keep-alive`）；前端解析流时需跳过纯注释行。
- **事件 id 与断线续传**：`/chat/stream` 的事件带每 turn 单调递增的 `id:`；重连以 `Last-Event-ID` 头或 `last_event_id` query 恢复（见上文）。
- **批处理**：高频事件（token/content）经 SSEBatcher 合并下发（约 32 条或 80ms 窗口）；终态事件（`done / task_complete / task_error / task_cancelled`）总是立即 flush。
- **nginx**：生产反代对 `/api/v1/chat/stream` 与 `/api/v1/explorer/stream/` 关闭 `proxy_buffering` 并拉长读超时（3600s）。

## WebSocket /ws

| 协议 | 路径 | 认证 | 说明 |
|------|-----|------|-----|
| WS | `/api/v1/ws/{session_id}?token=<access_token>` | **必须 access token + 会话归属** | 双向实时感知通道 |

连接建立：校验 token（拒绝 refresh token 冒用）与 session 归属；每 IP 连接限速 5 次/60 秒。失败关闭码：`4001`（token 缺失/无效/类型错误）、`4029`（限流）。

客户端 → 服务端感知事件（`app/services/ws_service.py::PERCEPTION_HANDLERS`）：

| 事件名 | 数据 | 说明 |
|--------|------|-----|
| `viewport_change` | `{center, zoom, bearing, pitch}` | 拖拽/缩放 |
| `layer_toggled` | `{layer_id, visible}` | 图层显隐 |
| `layer_opacity_changed` | `{layer_id, opacity}` | 透明度 |
| `layer_removed` | `{layer_id}` | 移除图层 |
| `base_layer_changed` | `{name}` | 底图切换 |
| `layers_changed` | `{layers: [...]}` | 图层列表更新 |
| `layers_reordered` | `{order: [...]}` | 图层排序 |
| `state_snapshot` | `{...full state}` | 完整状态快照 |
| `upload_completed` | `{original_name, feature_count, …}` | 上传完成 |
| `ping` | `{}` | 心跳；服务端回 `{"event": "pong"}` |

## 健康与监控

| 方法 | 路径 | 说明 |
|------|-----|-----|
| GET | `/api/v1/health` | 基本信息：`{status, timestamp, service, version, agent_runtime}`；`agent_runtime` 为 `"pi"`/`"chatengine"`，fail-closed：bridge 探测失败或进程已死时如实报 `chatengine`，绝不按 flag 推报 `pi`（#1032） |
| GET | `/api/v1/health/live` | liveness：仅确认进程可响应 `{status: "alive"}`；k8s livenessProbe / Docker HEALTHCHECK 使用，不检查依赖 |
| GET | `/api/v1/ready` | readiness：检查 **DB + LLM + Redis + Celery** 四项连通；全通 200，任一失败 503，body 仅 `{"ready": bool}`（不泄露内部依赖拓扑，SEC-11） |

部署矩阵对这三个端点的引用：Dockerfile.prod HEALTHCHECK 与 compose healthcheck 探 `/api/v1/health/live`（外加前端 :3000）；k8s 探针与 CI 部署后验证同样使用 live 端点。Prometheus 抓 `/metrics`（无前缀，见[性能遥测](#性能遥测-metrics)）。

## 附录：地图指令目录契约

后端通过 SSE `tool_result` / 工具结果下发的地图动作指令，必须存在于前端 `COMMAND_CATALOGUE`（`frontend/lib/map-commands/catalogue.ts`）；`catalogue-contract.test.ts` 强制扫描后端 `app/**/*.py` 的 command 字面量，新增指令需两侧同步落地。

当前目录中的指令（按域）：视角 `fly_to`、`zoom_to_bbox`、`set_map_view`；图层 `add_layer`、`add_raster_layer`、`remove_layer`、`reorder_layer`、`base_layer_change`、`layer_visibility_update`、`layer_style_update`、`apply_layer_filter`、`cartographic_runtime_repair`；热力/制图 `add_heatmap_raster`、`add_native_heatmap`、`create_thematic_map`；标注 `add_marker`、`draw_measurement`、`clear_annotations`；查询/导出 `query_features`、`export_map`。

指令参数 schema 以前端目录定义为单一事实源，此处不逐一展开。
