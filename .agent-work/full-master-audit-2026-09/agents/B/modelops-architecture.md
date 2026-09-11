# ModelOps 架构评估 — "Model 作为一等能力实体"的现状与 V8 投影链缺口

Repo: master@2aabdc43（含 feat/spatial-modelops-v2、feat/science-v5-scalable-geoai、feat/geocompute-v7 合并）。
本文件回答任务书的核心架构问题：**Harness capability projection 是否真正把 "Model" 当作可检索、可资格
判断、可成本评估的能力实体？Intent → capability → candidate algorithms → candidate models →
compatibility → resource estimate → execution backend → tool invocation 链是否存在缺口？**

结论先行：**链在后半段（compatibility → … → provenance/evaluation）是完整且高质量的；在前半段
（Intent → capability → model）完全断裂。Model 是一个被良好治理的"执行域实体"，但不是"能力域实体"。**

---

## 1. 现有链路的逐环评估

### 1.1 Model Descriptor → Registry → Package（完整，质量高）

- `GeoModelDescriptor`（app/lib/modelops/descriptor.py，ADR-0119 "ModelDescriptor V2"）：
  frozen + strict(extra=forbid) + 封闭词表（task_types/modalities/output_types/provider/padding/resampling）
  + secret 键扫描（`_reject_secret_keys`）+ 跨字段校验（band_order 长度、分割任务必须有 class_schema）。
  身份 = (model_id, model_version) + checksum(sha256)，`fingerprint_payload()` 排除 provenance——
  复用键语义干净。
- `ModelRegistryStore`（app/services/modelops/registry.py）：身份 = (owner_scope, model_id, version)；
  跨 owner 隔离（R1-C6）；同 identity 异 checksum = typed 碰撞；`seq` 全局单调承载"最新版本"（R1-m3，
  禁字典序）；原子写（pid+uuid tmp → os.replace）+ index parity 校验 + O_EXCL 跨进程锁。
  ⚠ 缺口见 findings B-8（多副本内存陈旧）与 B-11（文件名 sanitize 碰撞）——身份语义的两个边角。
- Package gate（app/lib/modelops/package_security.py）：checksum 双验、zip/tar 路径穿越/符号链接/嵌套
  archive/可执行后缀黑名单、zip-bomb 解压上限、manifest strict 解析、`load_weights_array` 强制
  `allow_pickle=False`（object array = 代码 → typed 拒绝）。**"包只校验不执行"的信任模型成立**，
  唯一合法权重入口收敛到 npy/npz。

### 1.2 Registry → Provider → Compatibility → Resource Estimate（完整）

- ProviderRegistry + `provider_ref` 硬门（R1-C1）：descriptor 只能引用进程内已注册 provider 实例，
  无 importlib/字符串路径动态加载。五种 provider 形态词表中 onnx_adapter/torch_adapter 是**显式
  前向声明**（WIRED_PROVIDER_TYPES 不含）——optional 依赖没有被偷偷变成 mandatory，torch/onnx
  在核心进程零 import（grep 验证：providers 目录无 torch/onnxruntime import）。
- Compatibility Qualifier（app/lib/modelops/compatibility.py）：纯函数，InputProfile（metadata+采样
  nodata）×descriptor，typed 失败码封闭词表；R1-C2 显式重投影裁决（allow_reproject + resampling_policy
  同时存在才产 ReprojectStage 计划，否则 CRS/RESOLUTION typed 失败）——度/米混淆在引擎主路径被
  R1-C3 守住（`_meters_per_pixel` 对 geographic CRS 返回 0=未知）。
  ⚠ 服务侧预检口径漂移（findings B-6）。
- Resource estimate → device plan（resources.py + providers/base.resolve_device_plan）：
  descriptor 要求 × provider 能力 → cpu/cuda + fallback；`batch_for_budget` 纯数学；VRAM 记账分
  provider_visible / externally_enforced（extension/remote 不可观测时如实标注）。

### 1.3 Device/GPU Plan → Inference → Postprocess → Artifact（完整，资源纪律突出）

- 引擎（app/services/modelops/engine.py）：有界信号量（默认 2，上限 32）、墙钟 deadline、协作取消
  （checkpoint + provider 探针）、OOM 降批（≤2 次）、tile 数守门在物化之前（estimated_tile_count，
  ≤65536）、loaded cache single-flight + refcount + 负缓存分流（permanent/transient）、
  merge 缓冲 RAM 256MiB → memmap 磁盘硬上限 64GiB、行带化 finalize（全尺寸中间量永不物化）。
  ⚠ 并发 accumulator 全局（B-3）、模板 reader 泄漏（B-7）、检测 pad 映射（B-5）、实例 memmap
  polygonize（B-4）——执行域的四个实现级缺陷，不动摇架构。
- Postprocess/stitching：概率空间融合（禁标签平均）、nodata 权重置零、确定性 NMS tie-break、
  instance 确定性 id。
- Artifact → Layer Delivery：raster → `publish_cog_data_object`（kind=cog_raster，renderable，
  merkle 身份）；JSON → modelops_artifact；owner scope 恰一维跨 owner 不可见。
  georeferencing 有 window_origin 平移（R1-M2）——窗口产物 georef 正确。

### 1.4 Provenance → Evaluation → Reuse（完整）

- manifest 全字段（descriptor/provider/input/preprocess/tile_plan/postprocess/reproject/device_plan/
  perf/compatibility）+ secret redaction；`inspect_provenance` 分段读取。
- Evaluation：segmentation IoU/F1/混淆、detection P/R/AP(11pt)、classification、ECE、
  spatial blocked split + leakage_guard（防地理泄漏，确定性 hash fold）。
- Reuse：InferenceFingerprint 全字段精确匹配（含 software_env、prompt 几何、时序声明、
  owner_scope）；禁止 RasterMetadata.fingerprint（角块摘要）参与 key；unseeded/caller_seeded
  不进 reuse（随机性资格门）。
  ⚠ evict 失效（B-2）使这套精妙的 key 体系在磁盘侧无界。

**小结：ModelOps 自身的纵切（descriptor→…→reuse）是这个仓库工程质量最高的域之一。**

---

## 2. 断裂点：capability / algorithm 域对 Model 零投影

证据（均为 grep/结构验证）：

1. `grep -rn modelops app/lib/gis/` → **0 命中**。capability_registry、algorithm_registry、
   algorithm_resolver、backend_selection、pattern_projection、cost_model 均无模型维度。
2. `image_segmentation` capability（app/lib/gis/capabilities/raster.py:216）的 description 明确绑定
   k-means 统计分割语义；其唯一算法 `remote.segmentation`（algorithms/remote_sensing.py:753+，
   tool_candidates=["segment_image"]）。modelops 的 6 个种子模型（landcover-seg/sar-detector/
   promptable-seg/temporal-forecast/instance-seg/chip-embedder）**不在任何 algorithm 的
   tool_candidates 里**。
3. AlgorithmResolver.resolve() 的候选集只来自 `algorithms_for_capability(capability)`——解析
   `image_segmentation` 永远得到 k-means 路径；"用深度学习模型做土地覆盖分类"的意图在
   capability→algorithm→tool 的正式链上无路可走。
4. ModelOps 工具唯一的可发现性通道是 ToolRegistry 元数据 `capabilities=["image_segmentation"]`
   （工具标签，非 capability 投影）+ tool_catalog.py:68 的关键词表（"模型/推理/GeoAI/modelops"）。
   即：**模型能力 = 检索关键词，不是能力词表成员**。
5. 任务书链路中 "candidate algorithms → candidate models" 这一跳在代码里不存在；
   "compatibility → resource estimate" 存在（modelops_check_compatibility / modelops_estimate_resources
   工具）但只能被**点名调用**，不能被 planner 在裁决算法候选时并行调用。
6. 合并后还引入了语义同名异义：`image_segmentation`（capability：k-means 统计分割）与
   modelops 工具的同名 `capabilities` 标签（学习模型推理）共享一个词、两种语义——正是任务书
   "schema 同名异义"检查项的实例。

### 2.1 造成的具体后果

- **可见性**：除非 LLM 碰巧检索到 modelops_* 工具名，模型推理功能对规划层隐身；
  capability 驱动的 recipe/plan 无法表达"模型推理"这一步。
- **资格/成本裁决旁路**：`modelops_check_compatibility`（band/CRS/分辨率/时序资格）与
  `modelops_estimate_resources`（VRAM/tile 数/批尺寸）是 plan-time 决策的完美输入，但因为不在
  resolver 链上，只能变成运行时失败（用户跑起来才被告知 BAND_COUNT 不符）。
- **fallback 语义缺失**：AlgorithmDescriptor 有 fallback_algorithms + fallback_semantics
  （equivalent/approximation/proxy/degraded）；模型推理没有对应的"模型不可用→统计基座降级"
  声明——两类实现之间的科学等价性无法表达。
- **backend_selection 的变体词表**（pure_python…external）没有 model/provider 维度；
  ResourceEnvelope 有 bytes_per_cell 等，但没有 VRAM/device 维——geocompute 的
  cluster ResourceRequest（gpu/profiles）与 modelops 的 DevicePlan 是两套不相通的resource语言
  （geocompute 图里无法表达"这个节点需要 cuda/4GiB VRAM 的模型 X"——resource_request 是
  1KB 的自由 dict 投影，cluster placement 不认识 modelops 语义）。

### 2.2 为什么会这样（根因）

- ADR-0099（GIS 方法语义域）与 ADR-0119（ModelOps）并行演进，各自完成度高；合并提交
  （a66215da 等）解决了迁移/测试基线，但没有（也不被要求）建立域间桥接契约。
- ModelOps 侧刻意声明了边界（registry.py docstring："不是 AlgorithmRegistry（ADR-0099 方法
  语义域）"）——防 duplicated truth 的同时把"投影"也一并拒了：**正确的防重是"一个事实源 +
  派生投影"（如 tool_to_capability 之于 tool_candidates），而不是两个互不知情的平行域**。

---

## 3. V8 设计建议（capability → algorithm → model → resource → execution 投影链）

1. **Model → capability 的派生投影（不是第二真相）**：
   在 ModelRegistryStore（或其派生层）实现 `capability_projection(descriptor) -> set[capability_id]`，
   按 task_types × input_modalities 映射到既有 capability 词表（新增成员走 capability_registry
   注册门）。模式完全复制 `AlgorithmRegistry.tool_to_capability()` 的"注册表静态后按内容缓存、
   register/unregister 失效"先例。
2. **capability 词表拆分**：`image_segmentation`（统计/k-means）与 `model_image_segmentation`
   （学习模型推理）分立，互为 fallback_candidates 并各带 fallback_semantics
   （model→statistical 是 "approximation"，方向要如实声明）。
3. **AlgorithmResolver 增加 model 分支**：resolve(capability) 的候选集 = algorithms ∪ models
   （模型候选带 provider 可用性门：ProviderRegistry.has + capabilities 白名单）。排序键复用
   (priority, cost score, id)；模型的 priority/cost 由 descriptor 派生（memory_estimate、
   est_seconds_per_chip）。裁决证据里带 model_id/checksum。
4. **plan-time 资格与成本前置**：resolver 选中模型候选时同步调用 qualify()（纯函数，
   InputProfile 来自 DatasetProfile——与现有 profile 管道衔接）与 estimate_resources；
   失败/超预算 → 转 fallback_trail（science semantics 披露）。
5. **资源语言统一**：cluster ResourceRequest 增加 modelops 投影字段（device、vram_bytes、
   provider_ref），placement/workers 的能力声明（GPU）与 DevicePlan 对齐——使 geocompute
   分布式图能调度模型推理节点（当前唯一执行面是 engine 的进程内信号量）。
6. **修复工具断裂（B-1）后**，把 `modelops_run_inference` 纳入对应 capability 的
   tool_candidates（作为模型路径的执行通道），使 ToolDispatchService 的复用/回填
   （tool_to_algorithms）对模型产物同样生效。
7. **owner_scope 语义对齐**：geocompute 的 owner_scope（"u:<sha1>"/"s:<sha1>"/anonymous）与
   modelops 的 owner_scope（session_id/project_id 恰一维）是两套域键；V8 需要一个单向映射函数
   并写进 provenance，否则跨域 lineage（模型产物进 workflow 节点）无法归属。

## 4. 与 findings 的对应

- B-1（工具 import 断裂）：链路最后一环的现实断点——V8 之前必须先修。
- B-10（架构 finding）：本文件第 2 节的正式化。
- B-2/B-3/B-5/B-7：执行域实现缺陷，不改变架构结论，但 V8 扩大模型使用面之前应先修
  （并发/资源纪律会被更高负载放大）。
