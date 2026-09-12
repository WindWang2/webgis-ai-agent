# 知识库 / 扩展市场 / ModelOps 三面 UI — P0 勘察报告（V9，feat/knowledge-market-ui-v9）

> 基线：origin/master @ `8b5b8375`（2026-09-12 fetch）。所有路径相对仓库根。
> 结论先行：三族后端能力呈「**知识库：可用但偏窄 / 市场：只读 / ModelOps：零 HTTP**」梯度。
> 本线 UI 严格按真实契约建设，缺口一律诚实空态 + PR 协调点，不臆造端点。

## 0. 通用约定

- API 前缀 `/api/v1`（`app/main.py:641-682` 挂载）。
- 知识库族响应走 `ApiResponse<T>` 信封 `{ code, success, message, data }`（`app/models/api_response.py:25-28`）；市场族与 RAG 配置测试为**裸 dict**（无信封）——前端类型按调用点逐个对齐。
- 前端传输层 `frontend/lib/api/transport.ts`：`apiFetch<T>` / `apiFetchBlob` / `openStream`；自动 Bearer + 401 刷新重试 + `X-Session-Token`；`describeApiError` 产出中文文案。POST 永不自动重试。
- 数据获取无 react-query/SWR；惯例是 zustand + 自制 hook（轮询范例 `use-job-center.ts`，requestSeq 防陈旧范例 `analysis-graph-panel.tsx`）。

## 1. 知识库 RAG 契约表

路由：`app/api/routes/knowledge.py`（前缀 `/knowledge`）。

| # | 端点 | 方法 | 请求 | 响应 data | 备注 |
|---|---|---|---|---|---|
| K1 | `/knowledge/documents` | POST | `{ title: str, content: str (≤100MiB), file_type: "text"\|"markdown"\|"json" }` | `{ document_id, chunk_count, status }` | **同步索引**，status 恒为 `"completed"`（服务层覆写，engine 内部 `"indexed"` 不外露）；content 为空 → VALIDATE_ERROR；向量落库后元数据失败 → `orphan_possible: true`（#485 补偿语境） |
| K2 | `/knowledge/documents` | GET | `?limit=50 (1..100)&offset=0` | `{ total, items: [{ id, title, file_type\|null, chunk_count, status, created_at\|null }] }` | offset 分页，created_at desc；租户过滤 org_id 优先、creator_id 兜底（#484） |
| K3 | `/knowledge/search` | GET | `?q (必填)&top_k=5 (1..20)&document_id?` | `{ results: [{ id (chk_*), document_id (doc_*), title, content(全文), file_type, user_id?, org_id?, score }] }` | **score 是 FAISS L2 距离，越小越相关，非归一化相似度** —— UI 不得伪装成百分比；租户 fail-closed 二次过滤（engine.py:142-167） |
| K4 | `/knowledge/document/{document_id}` | DELETE | 路径参数 | `ApiResponse.ok("文档已删除")` | 仅 creator 可删；先向量后 DB 行，软删 + 删除比 ≥20% 触发压实 |
| K5 | `/knowledge/retrieve-context` | POST | `{ query, top_k, document_id? }` | `{ context: str }` | 拼接 `"[{score:.2f}] {content}"`，`---` 分隔 —— 天然的注入对话素材 |
| K6 | `/config/rag/test` | POST | `{ address, collection }`（**后端忽略，仅展示**） | `{ status: "ok", store: "local-faiss", detail }`（裸 dict） | **admin only**；失败 HTTP 502 |

**状态机事实**：`Document.status` 词表 `pending/indexing/completed/failed`（`app/models/knowledge_base.py:30`），但**没有任何代码路径写入 pending/indexing** —— 索引同步完成后直接 completed。UI 的「解析中」态只能来自**请求在途**（前端视角），不是后端状态轮询。

### 知识库缺口（→ 诚实空态 / 协调点）

| 缺口 | UI 处理 |
|---|---|
| 无 multipart 文件上传（PDF/DOCX），仅 raw text JSON | 前端客户端 FileReader 读取 .txt/.md/.json 文本文件 → POST JSON；其余类型诚实拒收并说明 |
| 无索引进度事件/SSE/可轮询 job | 上传按钮在途 spinner = 全部「进度」；文案明示「同步索引」 |
| 无文档详情/分块级端点（GET /documents/{id}、/chunks/{id}） | 文档行不装作可展开；分块内容仅在搜索命中时展示（K3 自带全文） |
| 无嵌入模型读写端点（硬编码于 FaissVectorStore） | rag-config 中嵌入模型以「后端固定/仅供展示」呈现（仓库 `hint="仅供展示：…"` 惯例） |

## 2. Extensions marketplace 契约表

路由：`app/api/routes/extensions_marketplace.py`（无子前缀，全路径 `/api/v1/extensions/marketplace/...`）。模块 docstring 明示设计意图：**HTTP 只读；一切写路径是运维 CLI**（`python -m app.extensions_platform ...`）。

| # | 端点 | 方法 | 请求 | 响应 | 备注 |
|---|---|---|---|---|---|
| M1 | `/extensions/marketplace/packages` | GET | `?q&tag&publisher&include_revoked=false&offset&limit=20 (1..100)` | `{ total, offset, limit, items: PackageSummary[] }`（裸 dict） | q 为 id/title/description 子串；PackageSummary = `{ id, title, description, publisher, status: active\|deprecated\|revoked, deprecation_note, latest_version\|null, versions[], tags[] }` |
| M2 | `/extensions/marketplace/packages/{package_id}` | GET | — | PackageSummary（versions 不截断） | 404 = 未知包 |
| M3 | `.../versions/{version}` | GET | — | `{ package_id, ...VersionRecord, download: url }`；VersionRecord = `{ version, digest(sha256), size_bytes, publisher, key_id, signature, fingerprint, sbom_digest, permissions[], api_version, min_core_version, dependencies[], yanked, created_at }` | **权限声明在此层**（每版本） |
| M4 | `.../versions/{version}/download` | GET | — | StreamingResponse `application/gzip`，`X-Content-Digest` 头 | 410 已吊销 / 503 blob 缺失 |

**404 语义特殊**：`EXTENSION_REGISTRY_DIR` 未配置 → 列表 404 `detail="extension marketplace is not configured..."` —— UI 必须把 404 渲染为「市场未启用」而非错误。

### 市场缺口（→ 诚实空态 / 协调点）

| 缺口 | UI 处理 |
|---|---|
| 安装/卸载/启用/停用/已安装列表**无 HTTP 端点**（CLI only） | 浏览/搜索/详情/版本/下载为真实功能；安装按钮以「下载产物 + 说明安装需运维 CLI」诚实呈现，不出现假安装进度 |
| certification 报告无 HTTP（`certify_extension` 仅 Python，`app/extensions_platform/certification.py`） | 详情页不渲染认证徽章；以「后端未暴露认证端点」占位说明 |
| trust store 无 HTTP（`trust_store.py`） | 同上，签名/指纹字段（M3）如实展示即是最接近的信任信号 |
| SBOM 内容无 HTTP（仅 `sbom_digest`） | SBOM 摘要只显示 digest + 依赖列表（M3 dependencies） + 大小；许可分布/依赖计数聚合无数据源，不伪造 |
| 无下载量/评分/作者展示名 | 列表不渲染这些列 |
| 与 skills-hub 的「已装扩展」联动 | 无已安装状态源 → 不做假联动，PR 记协调点 |

## 3. ModelOps 契约表

**关键事实：ModelOps 没有任何 HTTP 路由**（`grep modelops app/api/` 零命中）。全部能力是 agent 工具（`app/tools/modelops_tools.py`，经 chat SSE 或 **`POST /api/v1/chat/tools/execute`** 直连执行）。

前端可驱动的工具（经 executeToolDirect，契约=工具返回 dict）：

| 工具 | 参数 | 返回要点 |
|---|---|---|
| `modelops_list_models` | `{ task_type?, project_id?/session_id? }` | `{ models: [{ model_id, model_version, task_types[], provider_type, checksum(截断12+"…"), owner_scope, license }], count }` |
| `modelops_inspect_model` | `{ model_id, ... }` | `{ descriptor(GeoModelDescriptor 全字段), provider_capabilities, owner_scope, revision, package_report?, lineage{deployment_state, latest_metrics, event_count} }` |
| `modelops_check_compatibility` | `{ model_id, input_profile }` | `{ model_id, input_profile, compatibility }` —— 地理配准/分辨率要求在此 |
| `modelops_model_history` | `{ model_id }` | `{ model_id, versions: { v: { events[], deployment_state } }, event_count }` |
| `modelops_run_inference` / `modelops_run_promptable` | `{ model_id, source_uri, roi_bbox?, vectorize?, ... }` | `{ run_id, status, reused, task_type, outputs{role:{path,…}}, performance, manifest }` |
| `modelops_cancel_inference` | `{ run_id }` | — |

descriptor 关键段：`spatial{crs_requirements, resolution_range{min,max}_m_per_px, allow_reproject, min_valid_data_ratio}`、`device_requirements`、`random_seed_policy`（确定性语义：`deterministic|fixed_seed|caller_seeded|unseeded`，无扁平 verdict 字段）。

### ModelOps 缺口（→ 诚实空态 / 协调点）

| 缺口 | UI 处理 |
|---|---|
| 无 HTTP 注册表/inspect/run 路由 | 面板经 `POST /api/v1/chat/tools/execute` 驱动工具（真实数据，不改后端）；工具不可达时诚实报错 |
| **无持久化运行历史端点**（run_id 仅 cancel 可用） | 运行历史 = **本会话**从 chat SSE 工具事件观察到的 modelops 调用（含 run_id），空态明示「后端未提供运行历史查询端点，仅显示本会话」→ PR 协调点 |
| #1212 能力来源无后端字段 | 面板呈现 `registered_by`/`descriptor.provenance` 原文 + 固定说明「模型经工具关键词发现（#1212），能力覆盖以实际 descriptor 为准」，不美化 |
| 产物预览：栅格经 lakehouse COG DataObject（`GET /api/v1/lakehouse/objects/{id}`），表格入 PostGIS 无独立服务端点 | 栅格产物链接 lakehouse；表格产物显示表名 + 说明；发布失败时 outputs.path 仍在（后端诚实降级），UI 如实显示 path |
| provider_capabilities 无独立端点 | 随 inspect_model 展示 |

## 4. 前端注入点

- **chat 发送**：`streamChat` body `{ message: string, ... }` —— **message 是纯字符串，无 attachments/annotations 字段**（`frontend/lib/api/chat.ts:151-195`）。→ 注入对话 = 把带 citation 标记的文本拼入 chat 输入框草稿，由用户确认发送（ADR-0145 决策，见下）。
- **citation 渲染**：`frontend/components/chat/story-markdown.tsx` 仅 15 行，`ReactMarkdown + remarkGfm + safeUrlTransform`，无 components map —— 增加 `sup`/`a` 定制渲染 `[n]` 角标 + 悬浮卡片，≤60 行约束可行。
- **tool-call-card**：`frontend/components/chat/tool-call-card.tsx` 已有按工具名分发定制卡片的先例（CARTO_TOOLS/LISA_TOOLS/ISOCHRONE_TOOLS，170-182 行）；modelops 工具结果 `call.result.run_id` 可达。`TOOL_NAMES` 中文标签表需补 modelops 条目。
- **面板挂载**：`frontend/app/page.tsx:36`（dynamic import）+ `:394`（`open={ragPanelOpen}`）；状态链 `hud-types.ts:291` → `uiSlice.ts:157-158` → `tweaks-panel.tsx:247` ToggleRow。**替换存根保持 props `{ open, onClose }` 不变，page.tsx 零改动。**
- **侧栏注册**：`LeftTab` union（`hud-types.ts:96`）→ `RAIL_GROUPS`（`nav-rail.tsx:54-68`，append-only）→ `context-panel.tsx:366-421` 条件渲染。
- **对话框惯例**：`useDialogFocus`（Escape/焦点陷阱/焦点归还）、`role="dialog" aria-modal`、tailwind 语义 token（`bg-surface-panel`/`text-ink`/`border-edge-subtle`）、无 backdrop-blur（性能决定）。
- **诚实空态惯例**：`rag-config.tsx` `hint="仅供展示：…"`；`analysis-graph-panel.tsx` `data-state="empty"` + 首载失败整面板隐藏 + 刷新失败保留旧数据 + `role="status"` 横幅；共享 `EmptyState` 组件（`tasks-tab.tsx:253` 用例）；无功能按钮直接不渲染。
- **可复用组件**：`upload-zone.tsx`（GIS 专用 accept 列表，知识库需自定义变体）、`tabular-data-grid.tsx`、`chart-core.tsx`、`analysis-graph-panel.tsx`（refreshKey + requestSeq 范式）。

## 5. 测试基建事实（对任务书「msw」的修正）

**仓库无 msw**（package.json 无依赖，无 handlers/fixtures 目录）。既有 fixture 驱动模式：

1. 单元/组件测试：`vi.stubGlobal('fetch', mockFetch)` + 本地 `jsonOk(body)` 假 Response 工厂（`frontend/lib/api/templates.test.ts:12-20` 范例）。
2. 视觉/旅程：`frontend/test/visual/capture.mjs` Playwright 路由拦截 `API_BASE`，内联 fixtures，后端零接触。

本线遵循仓库惯例（等效于 msw 的 fixture 驱动），不新增 msw 依赖 —— 避免 10 线并发下 lockfile 冲突。覆盖率门槛已配置 lines 75 / functions 70 / statements 75 / branches 60（`vitest.config.ts`）。

## 6. 设计决策（录入 ADR-0145）

1. **替换存根而非另起挂载点**：`rag-independent-panel.tsx` 保持 `{ open, onClose }` 契约，page.tsx/tweaks 零改动；#607 的诚实性原则升级为「有真实端点才有面板」，所有数据来自 K1–K6 真实调用。
2. **注入对话走草稿**：chat API 无结构化附件字段 → citation 文本拼入输入框草稿（zustand `pendingChatInjection` + nonce），用户可见可改再发送；不静默代发。
3. **score 原样呈现**：FAISS L2 距离标注「距离（越小越相关）」，不归一化、不伪装百分比。
4. **ModelOps 经 executeToolDirect**：不改后端拿到真实注册表/详情/兼容性数据；运行历史限于本会话并明示。
5. **市场只读诚实化**：下载真实、安装态诚实空态；404=未启用。
