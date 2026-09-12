# ADR-0138: API 契约与版本化基石（V9 foundation/api-contract-v9）

日期：2026-09-11
状态：已接受（随 foundation/api-contract-v9 PR 交付）
关联：#1217（字段级契约闸）、#901（ApiResponse 泛型，已修复并作为本 ADR 基础）、
#123（A5 会话分页 + A4 response_model，本线扩展其范围）

## 背景

后端约 129K 行、32 个路由文件、220 个端点（P0 实测）。内核质量纪律高，但对外契约层存在六类结构性短板：

1. ~56% 端点无 `response_model`（123/220，P0 实测；任务书估算 163 基于较早 master）；
2. 双错误信封并存：FastAPI `{"detail"}` 与 `ApiResponse{code,success,message,data}`（docs/api-docs.md 同时记载两套）；
3. 分页形态混杂：`Page[T]`/`clamp_pagination` 仅部分子系统采用，其余 ad-hoc `limit`；
4. HTTP 层无幂等：durable jobs 有 `idempotency_key`，POST 端点不认 `Idempotency-Key`；
5. 文档漂移：api-docs.md 限流 60/min（文档）vs 240/min（代码），缺 lakehouse / geocompute / workflow-runtime 三章节；
6. 版本策略缺失：V5–V8 特性全部堆在 `/api/v1`，无弃用机制。

## 决策

### D1 响应模型全覆盖

- 全部 JSON 端点必须挂 `response_model`；流式（SSE）/二进制/静态文件/定制 media-type
  端点不可能挂（响应体非 JSON schema 可描述），进入豁免清单
  （`tests/unit/api_contract/_contract_util.py::EXCLUSIONS`，16 项，附理由，PR 附表镜像）；
  无返回体的端点用 204 状态码显式表达。
- 最终态：204/220 挂模 + 16 豁免；67 个内联 BaseModel 归零（69 - PR#123 已迁 4，master 演进差值 2 个为 auth 计数口径）。
- 命名规范 `<Resource><Action>Request/Response`；pydantic v2 `ConfigDict`；关键模型补 `json_schema_extra` 示例。
- 服务层返回动态投影的端点（workflow-runtime 投影、quality report、GC 计划等）采用
  「开放对象（extra=allow）+ 锚定键」模型：字段增删不裁剪、OpenAPI 有诚实 schema；
  严格字段集由子系统服务测试保证。此为任务书「Any 兜底零容忍」的例外面，逐条见交付台账。
- 类型不准处先修实现再挂模型（如 `VersionResponse.extensions_api`：CORE_API_VERSION
  实为 "1.2.0" 字符串，模型从 int 修为 str）。

### D2 统一错误信封

- `ApiResponse{code, success, message, data}` 为唯一信封（Generic[T]，#901 修复保留）；
  错误分类学（`app/core/errors.py`）以 `category` / `retryable` / `degraded` 附加字段透出。
- HTTPException（fastapi 与 starlette 两层——starlette 层的 body 解析 400 之前绕过全局
  handler，注册键必须覆盖基类）与 RequestValidationError(422) 接入统一信封。
- 状态码→code 映射扩充：503=SERVICE_UNAVAILABLE、502=UPSTREAM_ERROR、504=TIMEOUT
  （503 是"显式不可用"业务态，此前误标 SERVER_ERROR —— schemathesis 模糊测试发现）。
- 兼容开关（回滚面）：`settings.LEGACY_DETAIL_ENVELOPE=true` 全局回退旧体；
  请求头 `X-Error-Envelope: detail` 按请求回退（未迁移 v1 客户端灰度）。

### D3 分页

- 存量契约 `Page[T]{items,total,limit,offset,has_more}`（`app/schemas/pagination.py`）
  为唯一分页信封，保持不变（改字段集会破坏既有 2 个采用方与 PR#123）。
- 列表端点分页元数据 additive 补齐（如 chat/sessions 补 `has_more`；`total==limit`
  语义下不做全表 count，与 #618-9 DB 分页纪律一致）。
- ad-hoc `limit` alias 的 OpenAPI `deprecated` 标注**推迟到 v2**：v1 上无替换信封的
  情况下弃用是虚假弃用；§8「接口不 breaking」优先级高于任务书 P4 的字面要求。
  v2 挂载即统一分页语义的启用点（见 D5）。

### D4 HTTP 幂等

- `IdempotencyMiddleware`（`app/core/idempotency.py`）：仅对携带 `Idempotency-Key` 的
  JSON POST 生效；key = hash(header + method + path + body-hash)；TTL 24h；
  Redis SET NX 单飞（借鉴 `tool_cache`）；锁 60s 过期退化为有界重复；Redis 故障
  fail-open（C-F10 同源纪律）；SSE/文件流不缓冲不重放。
- 显式排除清单：全部 SSE 端点、二进制下发端点、非 JSON POST（multipart 上传）。

### D5 版本策略

- 引入 `/api/v2` 挂载层：**同一 router 对象复用**（零逻辑复制），V7/V8 三个子系统
  （lakehouse / geocompute / workflow-runtime，59 条路径）为示范迁移。
- v2 语义承诺：默认统一错误信封、统一 Page 分页、ad-hoc limit alias 自 v2 起
  可标 deprecated、operation_id 带 `v2_` 前缀防冲突。
- v1 全量响应加 `Deprecation: true` / `Sunset: Wed, 30 Jun 2027 00:00:00 GMT` /
  `Link rel="successor-version"` 头；`webgis_api_version_requests_total{version}` 计数器
  为未来下线提供数据。**不下线任何 v1 端点**；Sunset 日期为候选值，实际下线另行公告。

### D6 文档生成与漂移闸

- `scripts/gen_api_docs.py` 从 `app.openapi()` 生成端点目录（按 tag 章节），只重写
  marker 区间，手写导言保留；限流等运行时常数从代码读取。
- `tests/test_api_docs_drift.py`：生成物 vs 提交物逐字比对 + 限流数值闸 + V7/V8
  子系统章节在场闸 —— 60/min vs 240/min 类漂移在 CI 必死。

### D7 字段级契约闸 + 模糊测试

- 字段级闸（#1217 后端半边）：`tests/unit/api_contract/test_field_contract.py`
  每端点 200 响应 schema 的字段签名（字段名+类型+一层 $ref 展开）快照比对，
  删除/改名/类型变化 fail 并给出类型化 diff；故意变更需显式刷新快照 + PR 说明
  （前端 TS 类型需同步镜像）。
- Schemathesis（4.26）：ASGI 内联，`max_examples=30`，allowlist 限定无外部依赖面
  （health/version/auth 校验层）；检查 not_a_server_error + status_code_conformance；
  例外：`/auth/register` 的 503 为已声明业务态，仅受 conformance 约束。
  DB/Redis/Celery 依赖端点不在模糊面（无真实 2xx 可达性），由覆盖率门禁 +
  子系统服务测试保证。已修复的发现：auth 三端点补 401/403/429/400 状态声明、
  503 code 误标（见 D2）、starlette 层异常绕过（见 D2）。
- 独立 CI lane：`.github/workflows/contract.yml`（production.yml 主 lane 零改动）。

## 方法异常项裁定（任务书 §2 P0.4）

- 12 个 DELETE：均为资源删除/解除语义，REST 语义正确，无需动作。
- 1 个 PUT（`PUT /projects/{project_id}`）：项目全量更新，幂等全量替换语义成立，保留。
- 0 个 PATCH：部分更新由 `/chat/sessions/{id}/mapspec/mutations` 动作族（POST 承载）
  与 `PATCH /mapspec` 变异管线覆盖；不引入 PATCH 方法。理由：变更语义是「带
  expected_revision CAS 的意图型 mutation」而非 REST 字段级部分更新，PATCH 无法表达。

## 迁移计划（v1 → v2）

1. **现在（本 PR）**：v1 默认新信封；LEGACY 开关 + 请求头覆盖可回退；v1 打点上线。
2. **+1 个迭代**：统计 `webgis_api_version_requests_total`，识别零流量 v1 端点；
   TS 侧 `describeApiError` 已双信封兼容（本次交付），旧客户端无需行动。
3. **+3 个月**：评估 Sunset（2027-06-30）可行性；v2 面扩量（modelops/templates 候选）。

## 风险与回滚

- 统一信封是 v1 响应体**破坏性变更**（error 路径）。回滚面 = `LEGACY_DETAIL_ENVELOPE`
  settings 开关（部署级，一行环境变量），无需回滚代码。
- response_model 挂模对返回体做「校验 + 重序列化」：开放对象（extra=allow）不裁剪
  字段；严格模型处曾出现 `render_diagnostics: null` 多键 —— 已用
  `response_model_exclude_none` 保持历史 wire 形态（map.py、map-action-ack）。
- 幂等层故障不影响可用性（fail-open）；Redis 恢复后重放语义自动生效。

## 证据

- 覆盖率门禁：`tests/unit/api_contract/test_response_model_coverage.py`
- 字段级闸：`tests/unit/api_contract/test_field_contract.py`
- 信封测试：`tests/unit/api_contract/test_error_envelope.py`
- 幂等测试：`tests/unit/api_contract/test_idempotency.py`
- 分页测试：`tests/unit/api_contract/test_pagination_contract.py`
- v2 测试：`tests/unit/api_contract/test_api_v2.py`
- 模糊 shard：`tests/unit/api_contract/test_schemathesis.py`
- 勘察报告：`docs/dev/api-contract-recon.md` + `docs/dev/api-contract-matrix.csv`
