# ModelOps V2 — 01 Architecture（冻结稿，经 Subagent-A 挑战后修订）

- 状态: FROZEN（修订记录见文末）
- 上游: `00-baseline.md`（8a33e3a5 audit）
- 本 Epic 的 ADR 落点: `docs/adr/0119-spatial-modelops-geoai-inference-v2.md`

## 1. 定位与不变式

Spatial ModelOps 是** learned-model 推理平面**，与既有平面正交：

```text
Capability（做什么）  = app/lib/gis/capabilities（不动）
Algorithm（方法语义） = app/lib/gis/algorithm_registry（ADR-0099，不动）
Model（learned 推理） = app/lib/modelops + app/services/modelops（本 Epic 新建）
Execution Runtime     = 自持 bounded ThreadPool + 复用 lib/cancellation；
                        与 GeoCompute 只做 resource 记账 seam（不改 budgets.py）
Artifact/Provenance   = 复用 lakehouse DataObject + provenance RunManifest 口径
```

不变式（全部可在代码中静态验证）：
1. master 上无第二 LLM 真相源：不碰 `ModelDescriptorRegistry`（ADR-0102 域）；契约类命名**刻意不撞名**（`GeoModelDescriptor`，R1-M1）。
2. （R1-M6 修订）**两个信任域，两句话**：(a) ModelOps 绝不在 core 进程加载/执行**模型包内容**（校验不执行；权重只经 `np.load(allow_pickle=False)` 合法口）；(b) **extension 代码**的执行域（core in-process / worker 子进程）完全由 extension platform 激活策略决定，`extension_adapter` 不得放宽——静态测试保证 adapter 不 import worker/client 内部件、不触达 ExtensionHost 私有记录。
3. 任何推理路径不整幅加载大 raster：唯一读通道 `RasterReader.read_window`；planner 只依赖 metadata；read window 永远 clamp 在栅格内，pad 只在 numpy 层施加（R1-C3）。
4. 所有缓存（registry、loaded model、reuse）有界 + 驱逐 + owner scope；fingerprint 不含 secret；日志/provenance 经 `redact_provenance_args` 单一口径。
5. 一切失败 typed（`ModelOpsError` 家族），禁止 bool + 字符串拼凑。
6. 复用 = InferenceFingerprint 全字段精确匹配；模型升级/checksum 变化自动失效；`RasterMetadata.fingerprint` **禁止**参与 reuse key（R1-B1）。

## 2. 模块布局

```text
app/lib/modelops/                 # 纯契约与确定性算法（叶子层，禁 import services）
  errors.py                       # ModelOpsError 家族（typed codes + correction_hint）
  descriptor.py                   # ModelDescriptor V2（frozen pydantic，strict）
  capabilities.py                 # task/modal/prompt/device 词表 + ProviderCapabilities
  resources.py                    # DeviceProfile/ResourceEstimate/DevicePlan + batch 数学
  compatibility.py                # CompatibilityQualifier：descriptor×input profile → typed failures
  planning.py                     # TilePlanner：TilePlan/TileSpec（确定性、metadata-only）
  preprocess.py                   # PreprocessPlan + 纯变换（band select/order、归一化、nodata mask）
  stitching.py                    # segmentation blend/argmax、detection NMS、instance merge、embedding 收集
  fingerprint.py                  # InferenceFingerprint（canonical json → sha256）
  promptable.py                   # PromptSpec（point/box/mask/text-prior，multi-object）
  temporal.py                     # TemporalStack 契约（时间维/缺失观测/质量掩膜/极化）
  package_security.py             # 包校验门（checksum/traversal/symlink/size/metadata；不执行）
  evaluation.py                   # IoU/F1/confusion/AP/PR/ECE + spatial-blocked/temporal 泄漏防护
  metrics.py                      # PerfCounters（结构化性能计数，禁"感觉更快"）

app/services/modelops/            # 有状态运行时
  config.py                       # env 旋钮（geo_raster/env.py 模式；.env.example parity）
  registry.py                     # ModelRegistryStore：持久化、owner scope、revision、碰撞检测、parity
  seeds.py                        # 内置 tiny 模型种子（descriptor + 确定性权重规格）
  providers/base.py               # Provider Protocol + ProviderRegistry（typed，无绕过 broker）
  providers/tiny_reference.py     # 确定性 segmentation/embedding/classification reference
  providers/tiny_detection.py     # 确定性 detection reference
  providers/promptable_reference.py # SAM 类能力参考实现（capability 声明，不硬编码版本）
  providers/temporal_reference.py # 时序栈参考实现
  providers/mock_gpu.py           # 资源感知 mock（VRAM/OOM/延迟可编程）
  providers/extension_adapter.py  # host.invoke_model_provider → Provider Protocol（线程 offload）
  providers/remote_client.py      # SSRF/redirect/timeout/bounded 的 remote endpoint provider
  loaded_cache.py                 # LoadedModelCache（refcount/LRU/TTL/negative cache/安全 unload）
  resource_plan.py                # 资源规划器（GeoCompute seam：bytes/concurrency 记账对齐）
  engine.py                       # InferenceEngine：编排/有界线程池/取消/OOM 降批/进度/计数
  reuse.py                        # 推理结果 reuse cache（fingerprint 精确匹配 + owner 隔离）
  manifest.py                     # InferenceManifest（进入 reuse eligibility；provenance 集成）
  artifacts.py                    # 输出发布（COG/GeoJSON → publish_cog_data_object / publish_data_object）
  service.py                      # ModelOpsService facade（工具层唯一入口）
  evaluation_service.py           # 评估运行器（拼 evaluation.py + 计数 + manifest）

app/tools/modelops_tools.py       # 10 个 agent 工具（registry 注册 + __init__.py 挂表）
tests/unit/modelops/              # 契约/纯函数级
tests/integration/modelops/       # 引擎/provider/安全/垂直切片
```

依赖方向：tools → services.modelops → lib.modelops → lib（geo_raster/cancellation/data）+ 既有 services（lakehouse/provenance/data_fabric.security）只允许出现在 services 层。

## 3. 核心契约

### 3.1 ModelDescriptor V2（immutable，identity=(model_id, model_version)）
字段 = Epic 规格全集（model_id/version/provider_type/provider_ref/task_types/input_modalities/input_bands/band_order/normalization/spatial_resolution_range/chip_size/context_size/stride/output_types/class_schema/crs_requirements/resampling_policy/device_requirements/memory_estimate/license/checksum/artifact_format/provenance）+ `schema_version`（"modelops.descriptor/v1"）+ `semantic_version`（provider 语义版本，进 reuse key）。规则：frozen、extra=forbid、版本碰撞（同 id+version 不同 checksum）注册即 typed 拒绝；secret 与 descriptor 分离（credentials 只以 credentials_ref 名字出现）。

### 3.2 Provider Protocol（typed，不可绕过 broker）
```text
capabilities() -> ProviderCapabilities        # tasks/prompt_modes/batch/streaming/cancellation/devices/semantic_version
load(descriptor, device) -> LoadedModel       # 校验 checksum；失败进 negative cache
warmup(handle) -> WarmupReport
estimate_resources(descriptor, plan) -> ResourceEstimate
infer(handle, TileBatch, InferenceContext) -> TileOutput   # batch∈[1..max_batch]，ctx 可取消
cancel(handle, run_id) -> bool                # 协作式；引擎另有墙钟 deadline
health() -> ProviderHealth
unload(handle) -> None                        # refcount=0 才真正卸载
```
注册进 ProviderRegistry 时校验 capabilities ⊆ registry 白名单；extension_adapter 只投影已过 host broker 的 provider（不新建信任面）。

### 3.3 兼容性资格（CompatibilityQualifier）
输入 profile 来自 `RasterReader.metadata` + 采样 band 统计；输出 `CompatibilityReport{verdict, failures[], warnings[]}`，失败类型封闭词表（BAND_COUNT/BAND_ORDER/MODALITY/DTYPE/RESOLUTION/CRS/TEMPORAL_LENGTH/CHIP_SIZE/CLASS_SCHEMA/NODATA_HEAVY(warn)…）。"能跑"≠兼容：qualifier 在 planner 之前，失败即 typed 拒绝。

### 3.4 Tile/Batch 运行时
TilePlanner(metadata, chip, stride, overlap, context, padding_mode) → TilePlan（row-major 确定序；edge chip pad 按声明模式；virtual huge raster 只读 metadata）。引擎逐批（bytes 预算内）读取→预处理→infer；每批 cancellation checkpoint + 进度事件；读取字节数/窗口数/批尺寸全部进 PerfCounters。**可复用 chunk.py 的 window 语义但不引入 ChunkCache 依赖**（推理 chunk 缓存交给 reuse 层）。

### 3.5 资源规划与 GeoCompute seam
ModelOps 自持 `DevicePlan{device, vram_bytes, ram_bytes, batch, est_tiles, est_time_s}` + 进程内资源台账（total VRAM 模拟上界 + 并发推理槽位），OOM → 降批重试（≤2 次，min batch=1，typed ProviderOOM 触发）。GeoCompute 对齐：bytes/concurrency 沿 ResourceGovernor 语义记账（adapter 只消费其公开接口形状，不改 budgets.py）。

### 3.6 Cache 三层
- **Registry store**：JSON 文档 + 原子写 + checksum，data dir 分 scope 目录；revision 单调；parity 校验（index vs docs）。
- **LoadedModelCache**：key=sha256(checksum+provider+device+runtime)；refcount + LRU(last-use) + TTL + max_entries/max_vram；load 失败 negative cache（TTL，防重试风暴）；unload 在锁内且 refcount=0；命中/驱逐计数。
- **Reuse cache**：`reuse.py`，key=InferenceFingerprint（descriptor checksum/version、provider semantic_version、input content_sha256/revision、preprocess、tile plan、thresholds、postprocess、output schema、policy scope）→ artifact ref；owner 隔离（复用 owner_scope 语义）；LRU+TTL+max bytes；hit 时 manifest 标 `reused=true`。

### 3.7 安全门
- 包安全：checksum（sha256，注册时+load 时双验）、zip/tar member 路径穿越（resolved path 必须落在根内）、symlink 拒绝、max package size/max files、metadata strict schema、可执行扩展名黑名单（.py/.pyc/.so/.pkl/.pickle/.joblib/.pth）——包**只校验不执行**。
- Remote：默认拒绝；operator 显式 allowlist（scheme+host+port 精确）；redirect 禁止自动跟随（逐跳重校验）；connect/read timeout；响应字节上限（output bomb）；错误体 sanitize（复用 ADR-0102 §7 口径）；secret 只经 credentials_ref 通道，绝不入日志/provenance。
- Extension：只经既有 host broker（worker RLIMIT/帧上限/WORKER_CRASHED 语义原样复用）。

### 3.8 评估平台
纯函数 metrics（IoU/F1/per-class/confusion/ECE；detection precision/recall/AP@IoU [小巧实现，非 COCO 全量]）+ **spatial blocked** 划分（网格 block → fold，确定性 hash）+ temporal split guard（按时间切，禁随机）+ leakage report 显式输出（禁止隐藏 train/test 地理泄漏）。runtime/VRAM 指标从 PerfCounters 注入。产出 evaluation manifest artifact（含 provenance）。

### 3.9 Promptable / Spatiotemporal
PromptSpec{points[], boxes[], prior_masks[], text?（仅 provider 声明 text_prompt 才允许），multi-object}；qualifier 校验 prompt 模式 ⊆ provider capability。TemporalStack{times[], max_len, missing_policy(mask|flag), quality_masks, modalities(optical/SAR+polarization), output_time_semantics}；超长截断/缺失观测按声明策略，不静默。

## 4. 与既有系统的接缝（显式清单）

| 接缝 | 方向 | 机制 |
|---|---|---|
| RasterReader | 消费 | read_window/metadata/read_mask（唯一读通道） |
| lakehouse DataObject | 生产 | publish_cog_data_object / publish_data_object（owner scope/merkle/producer） |
| provenance | 消费 | redact_provenance_args 单一口径；manifest 字段对齐 RunManifest 风格 |
| lib/cancellation | 消费 | CancellationToken/CancellationRegistry/checkpoint |
| data_fabric.security | 消费 | validate_url 语义（remote allowlist 在其上加显式白名单策略） |
| extensions host | 消费 | invoke_model_provider（worker/in-process 双模；线程 offload） |
| ToolRegistry | 生产 | 10 个工具 + descriptor capabilities |
| GeoCompute | 对齐 | bytes/concurrency 记账语义；不 import executor |
| ADR | 生产 | docs/adr/0119 |

## 5. 垂直切片（完成证明）

- **A 语义分割**：COG DataObject → modelops_run_inference(tiny seg seed) → qualifier → tile plan → bounded inference → overlap blend → 分类+置信度 COG → publish_cog_data_object（renderable）→ InferenceManifest（provenance）→ reuse 命中验证。
- **B Promptable 分割**：point/box prompt → capability qualification → promptable reference → mask COG/GeoJSON → artifact + provenance。
- **C Remote/Extension provider**：registry 模型 → remote（fake 本地 server，allowlist 显式）/extension worker adapter → resource plan → **可取消**推理 → artifact → 评估 + provenance。
三条全部：typed errors、bounded resources、cancellation、owner isolation、真实 production wiring（工具层入口）。

## 6. 测试矩阵 → `04-test-matrix.md`（骨架在此冻结）

Compatibility 10 类 / Tiling 9 类（含 virtual huge lazy 证明、mid-batch cancel、无接缝 oracle）/ GPU-resource 8 类（无 GPU、VRAM 不足、模拟 OOM、降批、驱逐、双模型竞争、unload 失败、worker 丢失、取消）/ Security 10 类 / Provenance-cache 7 类（版本/预处理/阈值/provider 语义/输入 revision 失效 + owner 隔离）。

## 7. 冻结修订记录

### R1（Subagent-A 架构挑战，2026-09-10；verdict=revise，全部采纳）

**BLOCKER**
- **B1 cache identity**：`input_content_sha256` 唯一口径 = DataObject merkle `content_sha256`（data_object.py:128）或全内容流式 sha256（chunk_digest 口径）。**禁止 `RasterMetadata.fingerprint`**（reader.py 自注 "identity, NOT a cache-invalidation key"）。引擎 `compute_input_content_identity` 是唯一合法构造点。→ 已写入 fingerprint.py 契约 + Wave 16 验收。

**CRITICAL**
- **C1 provider_ref 解析**：`provider_ref` 只能解析为 ProviderRegistry **进程内已注册实例 id**；禁止 importlib/字符串路径加载。registry 文档出现未注册 provider_ref → typed `ProviderError` 拒绝（Wave 4 验收）。
- **C2 显式 ReprojectStage**：位于 Qualifier 之后、TilePlanner 之前。仅当 CRS ∉ descriptor.crs_requirements 或分辨率 ∉ resolution_range **且** resampling_policy 显式允许时执行；warp 目标（CRS/分辨率/方法）进 fingerprint.preprocess 具名字段，warp 后中间栅格全内容 sha256 成为 input 内容身份；匹配时 no-op 且记录 `"reproject": null`。warp 中间体尺寸上限（像素/字节）超限 typed `ResourceUnavailable`。
- **C3 edge pad 与读通道**：TilePlan 携带 `(core_window, read_window, pad_left/top/right/bottom, fill_value)`；read_window 永远 clamp（reader 硬拒越界），pad 只在 numpy `np.pad` 层；fill 值是 preprocess 参数、进 fingerprint。与 raster_windowed.py 的 core/read halo 语义对齐。

**MAJOR**
- **M1 命名**：契约类定名 `GeoModelDescriptor`（避免与 chat 域 ModelDescriptor 撞名；ADR-0119 记录理由）。
- **M2 artifact kind**：`build_object_manifest` kind 封闭集（"vector_parquet","cog_raster","zarr_cube"）不足以承载 ModelOps 产物——**显式跨文件契约改动**：新增 `"modelops_artifact"` 成员（JSON 检测结果/评估 manifest/instance GeoJSON）；分类/置信度栅格继续 `cog_raster`。改动单行、rebase 冲突面最小；ADR-0119 声明。
- **M3 fingerprint 字段表（补五项）**：(1) provider 身份 = provider_ref + semantic_version + capabilities 投影；(2) descriptor.random_seed_policy（unseeded/caller_seeded 不进 reuse；caller seed 值进 key）；(3) software_env = python+numpy+rasterio 版本；(4) nodata 语义（fill 值/mask 模式，随 C3）；(5) 归一化统计**必须** descriptor 固定声明——逐景采样统计是隐藏参数，引擎拒绝。每字段失效性单测列入 Wave 16 验收。
- **M4 LoadedModelCache**：per-key single-flight 锁（并发首载只 load 一次）；TTL/LRU 驱逐仅限 refcount==0；last-use 在 **release** 时更新；负缓存分流——permanent（checksum/结构/描述符）TTL 缓存，transient（OOM/VRAM）不缓存（驱逐后可重试，与降批设计一致）。
- **M5 worker 通道约束**：extension provider 的批输出受 FRAME_MAX_BYTES(68MiB) 约束——引擎对 extension_provider 的批字节预算取 `min(预算, 64MiB)`；adapter 用独立有界线程池 + in-flight 信号量；worker cancel 延迟上界 = call_timeout（记入 PerfCounters.cancel_latency_s 语义）。
- **M6 不变式 2** 措辞：两个信任域（已改 §不变式 2）+ adapter 静态架构测试。
- **M7 分区权威**：TilePlanner 是**推理专属**分区（唯一允许 stride<chip），禁止回馈经典 windowed/ChunkCache 路径；回归断言经典迭代器不受影响。几何：纯像素空间（窗口序号→像素框），georef 只经 `reader.window_transform`。
- **M8 remote/SSRF**：allowlist 匹配（scheme+host+port 精确）→ **定义性权力**：跳过私网门（operator 显式配置，非代码路径放宽）；未匹配一律 `DataFabricSecurity.validate_url`。client = **httpx + follow_redirects=False** + 手动逐跳重校验（声明选型）。测试用显式 allowlist 对象注入（不经 env，避开 conftest parity 锁）。

**MINOR**
- **m1**：工具 capability id 复用既有词表（`image_segmentation` 已存在于 capabilities/raster.py:216）；新工具声明保守复用，不新增 id（ADR 记录）。
- **m2**：PerfCounters 记录 est vs observed VRAM 漂移（字段已有）；extension/remote 内存标 `externally_enforced`（不虚构记账）；unload 抛异常 → 条目仍移除并标 poisoned。
- **m3 Windows 门**：win32 不断言 worker stderr tail（select 不支持管道）；模拟 OOM/降批仅在 in-process mock_gpu 测；worker 用例依赖 call_timeout 而非 pytest timeout；RLIMIT 缺失是既有 typed 告警路径（不算本 Epic 回归）。
- **m4 包门残余**：权重载荷唯一消费口 `load_weights_array`（np.load allow_pickle=False + object array 拒绝）；嵌套 archive 成员拒绝；member 膨胀（声明 size vs 实际）拒绝。→ 已实现。
- **m5 blend 契约**：argmax 在**概率空间**累加（禁标签平均）；重叠融合权重对 nodata 像元置零；输出 nodata 掩膜 = 输入 nodata 掩膜（或 descriptor 声明策略）；context 边距在融合前裁掉、只融合 core。nodata 接缝 oracle 列入 Wave 12。

**维度结论**：duplicated registry 边界 pass（修命名/kind/词表）；extension broker 复用 pass；owner-scope×reuse 兼容 pass（owner 参与 key = data_object.py:465 既有口径）。
