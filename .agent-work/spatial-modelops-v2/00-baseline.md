# ModelOps V2 — 00 Baseline Audit

- 日期: 2026-09-10
- 分支: `feat/spatial-modelops-v2`
- Baseline commit: `8a33e3a5`（origin/master）
- Worktree: `../webgis-ai-agent-modelops-v2`
- 并行 epic 痕迹: master 近期合并 workflow-v4 / harness-v5 / lakehouse-v6 / query-v6；共享文件冲突注意 `app/tools/__init__.py`、`pyproject.toml`、`tests/conftest.py`、`.env.example`

## A. 14 个审计问题的答案（file:line 级证据）

### Q1 当前 model/provider registry 与 extension `model_provider` 的真实生产入口
- `app/extensions_platform/sdk/model.py:33` — `ModelProviderSpec`（provider_id/invoke_fn/capabilities⊆{streaming,cancellation,batch}/credentials_ref/parameters）。LLM chat transport 明确排除（docstring：模型 provider = GIS 域推理模型接入面）。
- 生产调用面：`app/extensions_platform/host.py:1007 invoke_model_provider(projected_tool, request, stream=False)`。in-process：返回事件迭代器（协作式取消=提前 close）或聚合 dict；worker：单帧 RPC（`record.worker.call(...)`，`WORKER_MODE_INVALID` typed 拒绝 streaming）。
- 投影为类型化工具：`host.py:806/898`（`namespaced_model_provider_tool`）；manifest 声明/激活一致性校验在 `sdk/model.py:60 validate()`。
- 示例 pack：`extensions/examples/extdemo-ml-pack/main.py`。
- **无模型身份语义**：spec 只有 provider_id，没有 model_id/version/checksum/compatibility —— 这是本 Epic 的核心缺口。

### Q2 是否已有 image/segmentation/detection/embedding tools
- **没有**。`app/tools/remote_sensing.py` 全部是经典遥感代数（NDVI/spectral index/SAR 滤波/GLCM/PCA/tasseled cap，`register_rs_tools` 在 `app/tools/__init__.py:17` 注册）。全仓 grep segmentation/detection/embedding/SAM/onnx/torch 命中的是 cartography `model_packs`（制图模板）与 evaluation 语料文本，无 learned-model 推理路径。

### Q3 模型 ID/version/checksum/provider 是否已有 canonical contract
- 无。相近真相源：LLM 侧 `ModelDescriptorRegistry`（ADR-0102，config-driven、严格拒绝未知来源）——领域不同（chat transport ≠ GeoAI 推理模型），不得复用/合并。DataObject 侧有 content-root merkle（`app/services/lakehouse/data_object.py:128 compute_content_root`）与 `app/lib/data/fingerprints.py`（canonical_dumps/sha256_hex）可复用作 checksum 基元。

### Q4 inference 运行位置 / event loop 阻塞
- 当前无 ML 推理运行时。工具层全 async（`@tool` + `async def`，`app/tools/registry.py:1847`）；GeoCompute 用 ThreadPoolExecutor（`app/services/geocompute/executor.py:682`）+ 持久线程事件循环桥（`_async_bridge.py:32 run_coro_sync`）。
- extension worker `record.worker.call(..., timeout=...)` 是**同步阻塞**管道往返 —— 若在 async 工具里直接调用会阻塞 event loop；ModelOps 接入必须经线程池 offload。
- Celery 侧：`app/services/jobs/worker.py:72 DurableJobHandle`（checkpoint/progress/cancel watchdog/临时文件清理）。

### Q5 GeoCompute resource contract 能否表达 GPU/VRAM
- **不能**。`app/services/geocompute/budgets.py:43 BudgetLimits` = {max_rows, max_bytes, max_nodes, max_concurrency}；ScopeKind = GLOBAL/TENANT/PROJECT/SESSION/EXECUTION。无 device/VRAM 维度（NODE 成员已被审计删除）。ModelOps 需要自己的 ResourceEstimate/DevicePlan 契约，GeoCompute 侧只用 bytes/concurrency 记账对齐（不改动 budgets.py，避免跨 epic 冲突）。

### Q6 DataObject/COG/Zarr 如何提供 lazy window
- `app/lib/geo_raster/reader.py:80 RasterReader` — 唯一 sanctioned 打开方式；`read_window`（:229）有界读、`_budgeted_read`（:320）、512MiB 默认全读上限、SSRF 门禁（`env.validate_remote_href`，reader.py:90-94 注释）、GDAL knobs（RASTER_PROCESSING_MEMORY_MB/RASTER_GDAL_CACHE_MAX_MB）。
- `app/lib/geo_raster/chunk.py:78 RasterChunkDescriptor`（chunk_id=grid-identity digest）、`iter_chunk_descriptors`（:296）、opt-in `ChunkCacheBackend`（:356，key=source+operation+window+fn_fingerprint）、`raster_runtime_capabilities`（:435）。
- `app/lib/geo_raster/zarr.py` — typed stub（lazy import + ZarrUnavailable + write_zarr_cube(time,y,x)），无生产调用方。
- `app/services/lakehouse/raster_object.py:81 publish_cog_data_object`；`app/lib/geo_raster/cog.py`（GDAL COG driver 真实写）。

### Q7 Science algorithms 与 learned models 的 registry 边界
- ADR-0099：Science Platform 只持有**方法语义**（参数契约/前置条件/不确定性/出处/fallback 语义），不执行不调度（`docs/adr/0099-spatial-science-geoai-platform-vnext.md` 决策 1）。`app/lib/gis/algorithm_registry.py:242 AlgorithmDescriptor`（crs_class/scientific_preconditions/backend_variants 等）+ `capabilities/` 目录能力词表。
- 边界结论：ModelOps 是独立推理平面（ learned-model 语义），执行请求可投影为 ToolRegistry 工具；不把 ModelDescriptor 塞进 AlgorithmRegistry（那是方法语义），但 artifact 类型可注册进 `app/lib/gis/artifacts.py ArtifactTypeRegistry`。

### Q8 Extensions worker model provider 能否真正 stream/cancel
- stream：worker 模式**不行**（单帧 RPC，`host.py:1029` typed 拒绝；frame 上限 68MiB `worker/protocol.py` FRAME_MAX_BYTES）。
- cancel：无协作取消 —— worker 死亡 = WORKER_CRASHED（`host.py:1046-1054`），超时 = WORKER_CALL_TIMEOUT；资源强制靠 RLIMIT_AS/RLIMIT_CPU（`worker/spawn.py:25 apply_resource_limits`，子进程自施，非 POSIX typed 降级告警）。
- in-process provider 的取消 = 生成器提前 close（协作式）。

### Q9 当前是否存在 unsafe pickle/arbitrary code loading
- 无（grep pickle.load/torch.load/joblib.load 在 app/ extensions/ 零命中）。**保持现状**：ModelOps 禁止 core 进程加载任意第三方 Python/pickle 模型包（Non-goal 与本审计一致）。reference provider 是仓库内可信代码；包安全只做 checksum/结构/元数据校验，不执行包内代码。

### Q10 remote model endpoint 如何做 SSRF/secret policy
- 现成可复用：`app/services/data_fabric/security.py:97 validate_url`（私网/回环/云元数据/解析 IP 复检/redirect per-hop 重校验 :299-353 bounded_get）；`app/lib/geo_raster/env.py:28 validate_remote_href`（/vsi 组合白名单）。
- secrets：extensions credentials_ref 通道（供给即授权，不进结果/日志）。provenance 有 `redact_provenance_args`（`app/services/provenance/manifest.py:86`）。

### Q11 推理结果 provenance 是否记录 model/preprocess/tile 参数
- 现有 provenance 是工具运行级别（`RunManifestBuilder`，`app/services/provenance/manifest.py:135/263`；`compute_run_fingerprint` :258）。无推理专用 manifest（model checksum/preprocess/tile plan/thresholds/postprocess 均无处记录）→ 新建 InferenceManifest，字段进入 reuse eligibility。

### Q12 artifact/result cache 是否可能跨 owner/project 泄漏
- DataObject：`owner_scope_allows`（`data_object.py:103`，跨 owner 一律 False，不泄漏存在性）✅。
- GeoCompute `NodeResultStore`（executor.py:194，进程内 LRU max 256 entries/128MiB）按 node fingerprint 键控，**无 owner 隔离** —— 但它是 run 内节点输出暂存（同进程 run 生命周期），非跨请求结果缓存；ModelOps 的 reuse cache 必须显式 owner-scope（复用 `reuse_index.py:100 find_result(owner_scope, node_fingerprint)` 的 per-owner prune 模式）。

### Q13 模型升级如何影响 cache/reuse
- 无任何机制（无模型版本概念）。本 Epic 的 fingerprint 必须含 model checksum+version+provider semantic version → 升级自动失效（精确匹配复用，绝不按 model_id 复用）。

### Q14 测试是否真正验证切片/边缘/拼接/取消/OOM/provider crash
- 经典栅格：`app/lib/geo_raster/chunk.py` 有 chunk 描述符测试先例；`tests/science_oracles/` 提供 oracle 模式。
- ML 推理的 tiling/edge/stitch/cancel/OOM/provider crash：**零覆盖**（无被测对象）。本 Epic 用 tiny deterministic providers + synthetic rasters 补齐。

## B. Findings 分级

| 级别 | Finding | 证据 | Epic 内处置 |
|---|---|---|---|
| P0 | 无 GeoAI 模型注册表/身份契约（version/checksum/compatibility） | Q1/Q3 | Wave 2-3 ModelDescriptor V2 + Registry V2 |
| P0 | 无 tile 推理运行时、merge/postprocess、learned artifact 语义 | Q2/Q14 | Wave 12-18 |
| P0 | 无 provider 生命周期/资源契约（load/unload/estimate/cancel） | Q1/Q8 | Wave 5-7 Typed Provider Protocol |
| P1 | GeoCompute BudgetLimits 无 GPU/VRAM 维度 | Q5 | ModelOps 自建 DevicePlan，GeoCompute 仅 bytes/concurrency 对齐（不改 budgets.py） |
| P1 | extension worker `call` 同步阻塞；async 接入需 offload | Q4 | 引擎统一线程池 + cancellation token |
| P1 | 无模型包安全校验（checksum/traversal/size/metadata） | Q9 | Wave 4 包安全门（校验不执行） |
| P1 | 无 remote inference client（SSRF/timeout/bounded） | Q10 | Wave 8 fake remote + SSRF 门复用 data_fabric/security |
| P1 | 无模型评估平台（IoU/F1/AP/泄漏防护） | Q14 | Wave 27-28 |
| P2 | reuse cache 需要 fingerprint 精确匹配 + owner scope | Q12/Q13 | Wave 29-30 |
| P2 | provenance 无推理专用 manifest | Q11 | Wave 29 InferenceManifest |
| P3 | Zarr 仅 typed stub | Q6 | 时序契约以 COG/内存栈为主，Zarr 探测降级 |
| P3 | `.agent-work` 文档先例（lakehouse-v6）格式沿用 | — | 本目录 |

## C. 关键约定（必须遵守）
- pytest: timeout=60(thread)；markers heavy/perf/cartography/real_services；`tests/conftest.py:18 _ENV_BASELINE` —— 新 env var 必须同步 `.env.example`（parity 锁）。
- 依赖已可用：python>=3.12、numpy<2.6、rasterio、shapely、pydantic v2、httpx、scipy（pyproject.toml:6-34）。**禁止新增重依赖**（无 torch/onnx —— reference providers 用 numpy 确定性实现）。
- 工具注册：`app/tools/__init__.py` 的注册表元组列表 + `@tool(registry, name=...)`。
- 错误约定：typed error（ValueError 子类）+ code/correction_hint（ scientific_errors.py 先例）；provenance redaction 单一口径。
- 存储：content-addressed blob（durable_blob_store.py）+ DataObject manifest（merkle content root + owner_scope 恰好一维）。
- ADR 目录 `docs/adr/`；`.agent-work/<epic>/` 文档先例已存在。
