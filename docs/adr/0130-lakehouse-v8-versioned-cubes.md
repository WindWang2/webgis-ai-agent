# ADR-0130: Lakehouse V8 — Versioned Geospatial Lakehouse & N-D Cube

- status: Accepted
- date: 2026-09-10
- relates-to: ADR-0118 (Spatial Data Lakehouse & Cube V6), ADR-0122 (Spatial Lakehouse V7), ADR-0119 (ModelOps / dataset versions), ADR-0101 (Data Fabric V4)

## Context

V6/V7 建立了 DataObject 身份（manifest CAS）、BlobStore FS/S3 双后端、
N-D labeled cube（time/band/polarization/vertical/y/x）、硬链接 CoW
修订与 dereference-based GC，但版本语义仍停留在对象层：

1. **没有 dataset 级版本指针**：cube 的 `revision_of` 链只存在于
   registry metadata；`artifact_revisions` 是 per-artifact 线性账本。
   snapshot / branch / tag / rollback 均不存在 —— 实验无法命名、
   无法回退、无法复现引用；
2. **维度白名单缺 model/scenario**：多模型 × 多情景的遥感/模拟产物
   （`x × y × time × band × polarization × model × scenario`）无法在
   单一 cube 内表达；
3. **provenance 无契约**：manifest `producer` 只有自由 dict，无
   algorithm/params/code_version/model_version 固定键；lakehouse 对象
   与 workflow run 之间断链（catalog 列存在但发布路径从不填）；
4. **retention 缺失**：GC 是全局 dereference-based，没有 per-dataset
   的保留策略（max versions / min age / tag pin）。

## Decision

**全部 additive，字节真相仍只有 BlobStore，manifest 确定性纪律不变。**

### 1. Dataset 版本层（`app/services/lakehouse/dataset_registry.py`）

两类新 manifest（与 DataObject 同纪律：canonical JSON → CAS，确定性，
无 wall-clock/随机，64KiB 闸）：

- **dataset 描述符**（`kind="dataset_descriptor"`）：id = canonical
  sha256 = dataset_id。**owner_scope 参与身份**（跨 owner 同名 = 不同
  逻辑数据集，字节 blob 共享仍安全 —— DataObject 同款）。描述符不可变：
  改名/改契约 = 新 dataset；
- **版本 commit record**（`kind="dataset_commit"`）：id = canonical
  sha256 = version_id。字段 = (dataset_id, parent, data_object_id,
  content_sha256, action, provenance)。**branch 不参与身份**（git
  语义 —— 分支是指针层落点注记）：同 (parent, content, provenance)
  经任意分支重放 = 同一版本，实验复现不依赖分支命名。

durable 台账（0035 additive 三表；事实源仍是 manifest，台账承担
索引与指针状态）：

- `lakehouse_datasets`：注册行（dataset_id 唯一）；
- `lakehouse_dataset_versions`：append-only 版本账本，parent 链 =
  版本 DAG；幂等键 `(dataset_row_id, version_id)`；
- `lakehouse_dataset_refs`：命名指针 —— **branch 可变**（generation
  单调，UPDATE ... WHERE generation = observed 乐观并发；撞号 =
  typed `DATASET_REF_CONFLICT`），**tag 不可变**（唯一约束拒绝改写）。

### 2. 原子提交协议（中断安全）

commit = ① 内容 DataObject 解析 + owner 校验（本层绝不发明内容，
跨 owner 内容 = `DATASET_CONTENT_UNRESOLVED`）→ ② commit manifest
CAS 发布 → ③ 版本行 savepoint 插入（唯一键撞 = 复用已落地行）→
④ branch 指针 generation CAS 前移。任意步中断：分支 head 不动 →
**没有可见的半成品版本**；已发布 manifest 成为 GC 可回收孤儿；
重试同提交 → 同 version_id → 幂等收敛。回滚（rollback = revert，
非 reset）：以目标版本内容创建 `action="rollback"` 新 commit
（parent = 当前 head）+ 指针前移 —— **历史只增不减**，审计可追溯。

### 3. N-D Cube v3（model × scenario）

`ALLOWED_DIMS` 扩至 `{time, band, polarization, vertical, model,
scenario, y, x}`。**契约版本按需升级**：维度集 ⊆ v2 六维且无逐变量
nodata → 仍写 schema v2（既有发布路径 id 零漂移）；出现
model/scenario 或携带 nodata_per_variable → 写 schema v3（版本字段
恒判别投影形状）。v3 同步支持**逐变量 nodata**（root attr
`nodata_per_variable`）。选择层（`labeled_selection`）与 REST
`LabeledWindowRequest` additive 扩展 model/scenario 选择子。单元格
预算（8M/64M cells）不变 —— 多维轴长计入乘积，超界 typed 拒绝
（诚实边界，不静默分摊）。

### 4. Provenance 契约

commit record `provenance` 固定键集合：`action / algorithm /
parameters / code_version / model_id / model_version / sources
(dataset@version) / inputs (data-object ids) / coverage (bbox,
time) / quality_flags / workflow_run_id / workflow_step` —— 写入前
统一 redact（secret 不入身份），键数 ≤24，尺寸随 64KiB commit 闸。
**不改 `build_object_manifest` 字段集**（MANIFEST_SCHEMA_VERSION
保持 1）：provenance 富化住在 commit record（新对象，零身份冲断）；
`workflow_run_id` 直通台账列与索引。

### 5. Retention / GC

- **GC root 扩展**：`_protected_references` 增加 dataset 描述符 id、
  版本 commit manifest id、版本内容 data_object_id（union 规则与
  token 重验不变式保持）—— 仅被版本历史引用的 manifest/blob 绝不
  回收；retention 裁剪版本行后内容自然回到候选；
- **retention = 元数据级**：per-dataset 策略（max_versions / min_age
  hours / keep_tags 恒真）只 prune 版本台账行 + 指针失效候选，字节
  删除唯一入口仍是 `execute_gc`（dereference-based）—— 两层组合
  排除"retention 误删被引用 blob"整类事故；
- 修复 `_scan_manifests` 的 **Windows 路径分隔符 bug**（FS 后端
  `iter_objects` 相对键含 `\`，扫描恒空 —— Linux CI 不可见）。

### 6. Formats / 集成

- chunked 局部读：zarr v3 chunk 窗口读 + GeoParquet row-group 剪枝
  （V6/V7 既有），V8 补 **Arrow IPC adapter**（pyarrow 既有依赖，
  零新增栈）——向量通道的流式局部读；
- **workflow 原子发布**：run 产物 → DataObject → dataset commit
  （action=`workflow_publish`，provenance.workflow_run_id + step +
  algorithm + parameters 全量入账）—— run → version 可追踪；
- **Data Fabric**：lakehouse dataset version 可注册为 fabric
  DatasetDescriptor（只读 adapter，不侵入 planner）。

## Consequences

- 版本层三表随 `create_all` / 0035 additive 迁移落地（单 head 保持，
  up/down/up 实测）；SQLite（测试方言）与 PG 双兼容。
- 提交重放语义：显式 `parent_version_id` 重放历史提交 → 同
  version_id（复现验证接口）；不指定 parent 的提交永远前移 ——
  账本 append-only 不变量优先于"去重最大化"。
- v2/v3 cube 共存：v2 发布路径（含既有测试基线）字节级不变；v3 仅
  在 model/scenario 出现时启用；旧 reader 按 attrs 透传不受影响。
- GC 对 commit/descriptor manifest 无特判：它们是普通 manifest
  （无 content_blobs），保护完全经由版本台账 root 传递。
- retention 不删字节 —— 孤儿内容由 GC 收割；两者执行序自由
  （先 retention 后 GC 收敛最快，逆序也安全）。

## Non-goals / Deferred

- 分支级 merge（三路合并需要内容语义；DAG 已支持多 parent 扩展点，
  语义后续单独裁决）；
- v2 labeled cube 的 chunk 级 CoW fork（V7 typed 拒绝保持 —— 本轮
  仅 dataset 版本层 + 引用；chunk 级 delta 归 cube store 后续）；
- catalog 投影对 dataset/version 的 STAC 化（STAC Item 需 bbox+
  datetime，cube 契约外的 dataset 暂不入 STAC）；
- 对象存储 bucket lifecycle 自动检测（沿 ADR-0122 deferred）。
