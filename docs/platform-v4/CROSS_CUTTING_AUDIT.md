# Platform V4 横切审计（Phase A 交付物）

日期：2026-09-11
分支：`feat/platform-v4-production-control`
基线：master @ 2aabdc43（Extensions V3 与 Quality V3 合入后）

## 结论速览

前序方向（Extensions V1–V3、Quality V2–V3/SRE）已经把大量平台能力建成：
W3C traceparent 中间件、RuntimeContext 关联主干、结构化事件面
（vendor-neutral Sink + 事件词表）、SRE 有界指标 + staleness 告警、
4 端点健康面（live/ready/detailed SRE 分类学）、17 个确定性 chaos fault
（结构性生产隔离）、扩展平台全生命周期（签名/信任/marketplace/worker
隔离/认证语料）、quality lane 运行器（与 CI 逐字对齐）。

本方向（V4）**不重建**这些能力。审计定位出的真实缺口是五条"最后一公里"：

| # | 缺口 | 现状证据 | V4 处置 |
|---|------|----------|---------|
| 1 | 跨进程 trace 断裂 | `apply_async` 不携带 trace 头；`durable_job` 恢复 session/run/turn 但无 trace_id/span_id；worker 侧日志无法并回请求 trace | Celery headers 携带 traceparent（无 DB migration）；`durable_job` 恢复进 RuntimeContext |
| 2 | 无统一 span 模型 | trace 是"ID 传播"而非 span 树：无 parent/child、无阶段语义、无导出面 | `app/lib/observability/spans.py`：封闭阶段词表 + SpanExporter 协议（Ring/Logging）+ OTel-shaped 字段；不引入 OTel SDK 依赖 |
| 3 | 无全仓错误分类学 | core 层 8 个字符串码鸭子类型；jobs 状态机、DiagnosticCode、域错误码三套并存；普通 API/工具路径无 retryable 语义 | `app/core/errors.py`：封闭 ErrorCategory 词表 + PlatformError 基类 + classify() + retry/degraded 决策面；exception handler 接线 |
| 4 | worker 下线无主动注销 | Celery signal hooks 零注册；被动 stale sweep 60s+300s 才收敛；lifespan shutdown 无 deadline 兜底 | worker 生命周期注册面（worker_ready/shutdown 钩子 + 有界 drain）+ shutdown deadline 看门狗 + in-flight gauge |
| 5 | 观测配置与身份漂移 | /health version 硬编码 0.1.3 与 VERSION 文件 0.1.0.0 漂移；无 /version 端点；perf 预算三处真相不互通；扩展平台无 Prometheus 计量 | build_info + /api/v1/version；声明式 perf budget manifest + 观测面；扩展平台有界计量 |

诚实披露（审计确认、本方向**不做**的事）：

- **不引入 OpenTelemetry SDK**：spans.py 是 OTel-shaped 的 vendor-neutral
  面（字段名对齐），OTel Sink 是既有 Sink 协议上的一个 follow-up 适配器，
  本方向不新增重量级依赖。
- **不做依赖锁定（lock file）**：requirements 全 `>=` 是仓内既定纪律
  （DEPS-06 拆分 + #618-35 双维护注释），改钉版策略超出本方向边界。
- **不做浏览器 visual regression / hypothesis / mutation testing**：
  Quality V3 已显式披露为不引入（自研生成式 fixtures + 后端 golden）。
- **不扩 chaos 到网络级（toxiproxy）**：既有纪律"只用既有接缝、无生产
  chaos 分支"保持；新增 fault 全部走已验证的 monkeypatch/编排接缝。

## 各面清单（Phase A 审计记录）

### Extensions（B/C 现状）

- 契约：manifest（pydantic extra=forbid，schema_version 认知上限拒新）、
  api_version 门控、permissions、trust（Ed25519 + trust store + 吊销先于验签）。
- 生命周期：discover→validate→activate→enable/disable→deactivate→unload→
  reload→upgrade（版本 pin 预检）+ refresh_revocations（隔离传播）+
  refresh signal（main.py 300s tick）+ worker 连续崩溃 ≥2 → quarantine。
- 供应链：content-addressed marketplace（publish 验签+SBOM secret 扫描+
  claim-once）、HTTP 只读、distribution 原子换装、包级 SBOM。
- 隔离：worker mode（bubblewrap 适配器 + spawn/context/isolation）。
- 测试：tests/unit/extensions_platform/ 36 文件。
- **缺口（V4 补）**：平台级 Prometheus 计量（激活/隔离/crash/调用时长）
  缺失——可观测性是失败隔离之后的一环。

### Observability（D 现状）

- 已有：traceparent 全 scope 中间件、X-Request-ID、RuntimeContext
  （ContextVar，async/to_thread 自动传播）、日志关联 filter
  （req/sess/turn/run/proj）、events.py（词表 + Sink + digest）、
  sre_metrics（封闭组件词表 + staleness）、/metrics（instrumentator）。
- **缺口（V4 补）**：span 树（阶段语义 + parent/child + 导出）、
  Celery 跨进程传播、JSON 结构化日志选项。

### Error Taxonomy（E 现状）

- 已有：exception.py 环境分级 + sanitize_traceback + HTTP 码映射；
  jobs JobStatus 状态机（retryable/terminal/cancellable）；DiagnosticCode
  （扩展域 50+ 稳定码）；CancellationToken 协作式取消。
- **缺口（V4 补）**：跨系统封闭分类词表 + typed 基类 + classify() 单一
  生产路径 + retry/degraded 决策辅助；handler 输出结构化 category/retryable。

### Quality（F 现状）

- 已有：quality_runner lanes（quick/backend/frontend/science/cartography/
  data/security/quality/perf + impact/integration/full profile）、ci-local.sh
  （CI 逐字对齐 + 契约锁定）、生成物字节闸族、property/fuzz/chaos/契约
  套件、perf_budget helper（median+floor+ratio）。
- **缺口（V4 补）**：无——新增测试文件按域落位即可被 backend/quality
  lane 自动收编；本方向遵守既有 lane 纪律。

### Chaos（G 现状）

- 已有：17 fault 注册表（CACHE_*/LOCK_*/REGISTRY_*/INGEST_*/CANCEL_*/
  LLM_*/STORAGE_*/JOBS_*/DB_*）、结构测试锁定生产零 import、注册表文档
  字节闸、integration_harness --chaos、REAL_SERVICES 双 opt-in lane。
- **缺口（V4 补）**：partial upload（写一半的制品）、enqueue 重试风暴
  （入队连续失败 → 诚实 failed、无孤儿）、重复投递风暴（并发 claim
  恰好一个执行者）——均走既有接缝。

### Health/Shutdown（H 现状）

- 已有：/health、/health/live、/ready（SEC-11 极简 body）、
  /status/detailed（JWT + ok|degraded|down|not_configured + 延迟阈值 +
  TTL 缓存 + M-4 指标刷新点）、lifespan 启停序完整（drain_background_tasks
  等 11 步）、心跳 + stale sweep + orphan sweep。
- **缺口（V4 补）**：worker 主动注册/注销 + 有界 drain 编排、shutdown
  deadline 看门狗、in-flight 请求 gauge（drain 可观测性）。

### Performance（I 现状）

- 已有：tests/benchmarks perf harness（median-of-7 + baseline×4.0 +
  baselines.json）、perf_budget helper、结构量闸、routing latency class。
- **缺口（V4 补）**：生产侧声明式预算 manifest + 观测面（histogram +
  breach counter + 告警接缝）；与测试基线三处真相的关系显式文档化
  （不合并——各自口径不同是文档化决策）。

### Config/Supply-chain（J 现状）

- 已有：pydantic-settings 生产 fail-fast validators（secrets/SSRF/SQLite/
  CORS）、扩展包级 SBOM、docker/k8s 安全契约测试、.env 模板 parity 闸。
- **缺口（V4 补）**：/api/v1/version + build metadata（消除 version 漂移）、
  config JSON Schema 导出、应用依赖 inventory（可选 SBOM-lite，无新依赖）。
