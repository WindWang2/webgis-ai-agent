# ADR-0124: Spatial ModelOps & GeoAI Inference Platform V2

**Date:** 2026-09-10
**Status:** Accepted
**Branch:** `feat/spatial-modelops-v2`

## Context

仓库已有 Science（ADR-0099 方法语义）、GeoCompute（执行平面）、Data
Fabric/Lakehouse（数据身份）、Extensions（ADR-0105 provider 扩展面）、
Harness（规划）。但 learned-model（分割/检测/嵌入/promptable/时序）
推理缺少生产平台：模型从哪来、适合什么数据、需要什么预处理、在哪个
worker 跑、如何切片拼接、如何版本化/评估/追溯/缓存/安全地让 agent 调用。

基线审计（`.agent-work/spatial-modelops-v2/00-baseline.md`）：GeoAI
模型注册表、tile 推理运行时、provider 生命周期契约、模型评估平台全部
缺失；extension worker `call` 为同步阻塞单帧 RPC；BudgetLimits 无
GPU 维度；无 unsafe pickle 路径（保持现状为约束）。

## Decisions

1. **独立推理平面，与既有平面正交**。Science 管方法语义，GeoCompute 管
   执行调度，ModelOps 管 learned-model 身份/推理/评估。契约类命名
   `GeoModelDescriptor`（刻意不与 chat 域 ModelDescriptor 撞名）。
2. **provider_ref 只解析进程内已注册 provider 实例 id**（禁止动态
   import）。模型包**只校验不执行**（checksum/结构/成员黑名单/嵌套
   archive 拒绝；权重唯一消费口 `load_weights_array` =
   `np.load(allow_pickle=False)`）。extension 代码执行域由 extension
   platform 决定，adapter 不放宽。
3. **TilePlanner 是推理专属分区权威**（唯一允许 stride<chip）：core/read
   窗口分离（read 永远 clamp，pad 只在 numpy 层）；metadata-only 规划；
   纯像素空间，georef 只经 `reader.window_transform`；经典 chunk/windowed
   路径不回馈。
4. **显式 ReprojectStage**（Qualifier 后、Planner 前）：CRS/分辨率失配
   仅在 descriptor 声明 resampling_policy 时执行，warp 参数 + 产物
   sha256 进指纹；否则 typed 兼容失败。无隐藏重投影。
5. **输入内容身份唯一口径**（reuse key）：DataObject merkle
   content_sha256 或全内容流式 sha256；禁止 `RasterMetadata.fingerprint`
   （identity ≠ cache-invalidation key）。归一化统计必须 descriptor
   固定声明（逐景统计 = 隐藏参数，禁止）。
6. **复用资格**：fingerprint 全字段精确匹配（模型 checksum/版本、
   provider 身份/语义、输入内容、预处理、tile plan、阈值、后处理、
   输出 schema、owner scope、software env）；unseeded/caller_seeded
   模型不进 reuse；owner 隔离（跨 owner 永不可见）。
7. **LoadedModelCache**：per-key single-flight；refcount 保护 in-flight；
   LRU(idle-TTL) 驱逐仅限 refcount==0（last-use 在 release 更新）；
   负缓存分流（permanent 缓存 / transient 不缓存）。
8. **资源有界**：批字节预算（extension provider 受 64MiB 帧上限约束）、
   merge RAM 上限 + memmap 兜底（超硬上限 typed 拒绝）、墙钟 deadline、
   OOM 降批 ≤2 次、并发信号量。GPU 维度在本平面自持（BudgetLimits 不改），
   extension/remote 内存标 externally_enforced。
9. **remote endpoint 默认拒绝**：operator allowlist（scheme://host:port
   精确）命中才跳过私网门（定义性权力，仅配置可授）；未命中走
   DataFabricSecurity.validate_url；httpx `follow_redirects=False` +
   逐跳重校验（≤3 跳）；响应字节上限；错误体 sanitize；secret 只经
   credentials_ref 通道。
10. **artifact kind 扩展**（显式跨文件契约改动）：`data_object.py` 的
    kind 封闭集新增 `modelops_artifact`（检测/实例 GeoJSON、评估报告、
    推理 manifest）；栅格产物继续 `cog_raster`（renderable）。
11. **评估平台**：IoU/F1/混淆/检测 P/R/AP（11 点插值，如实声明非 COCO
    全量）/ECE；spatial blocked 划分（确定性 hash fold）+ temporal
    split + 泄漏审计报告显式输出。
12. **性能结构化**：PerfCounters 全字段（pixels/s、tiles/s、load/warm/
    cancel 延迟、bytes_read、窗口数、批尺寸、OOM 降批、cache 命中、
    merge 工作量、RTT、queue wait、est/observed VRAM 漂移）进 manifest；
    禁"感觉更快"。
13. **能力词表封闭**：task/modal/prompt/device 词表 + ProviderRegistry
    白名单校验；reference providers 用 numpy 确定性实现（无 torch/onnx
    重依赖）；时序/空间 leakage 防护在评估层强制。
14. **agent 工具面**：10 个 `modelops_*` 工具（list/inspect/compat/
    estimate/run/promptable/evaluate/compare/provenance/cancel），
    Harness 只见 typed capabilities。

## Non-goals

不训练 foundation model；不做 AutoML；不强制 ONNX；不重写 GeoCompute
scheduler；不允许 core 进程执行任意第三方模型代码；不虚称 remote
provider 完全可复现（software env 进 key，跨环境复用需显式豁免）；
不把 extension marketplace 逻辑复制进本平面。

## Consequences

- 新目录 `app/lib/modelops/`（纯契约）+ `app/services/modelops/`（运行时）
  + `app/tools/modelops_tools.py`；测试 `tests/{unit,integration}/modelops/`。
- 新 env 旋钮（`MODELOPS_*`）已同步 `.env.example`；conftest parity 锁
  由各测试自带 env 注入满足（不进全局 `_ENV_BASELINE`，避免跨 epic 冲突）。
- Windows 本地跑 extension worker 用例受既有平台限制（无 RLIMIT、select
  stderr 限制）——相关用例按基线 m3 平台门标注。
