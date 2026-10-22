# ADR-0265: GIS Dataset Semantic Contract（GISDatasetDescriptor vNext）

- 状态：Accepted
- 日期：2026-09-26
- 方向：F01（数据语义从推导结果升级为全链稳定契约）
- 前作：ADR-0207（measurement semantics，#1488）、ADR-0170（D1 contract）、
  ADR-0092（semantic profile roles）、V3 fingerprints/versioning 契约层

## Context

#1488 补齐了字段级 measurement/unit/field resolver 的**推导能力**，但其 Out of Scope
明确留下：D1DatasetDescriptor 迁移、语义持久化、字段级 ontology 在
MapSpec/前端/context-reuse 链的传播。当前仓库对"数据语义"有四个结构
（DatasetDescriptor/D1、DatasetProfile、SemanticDatasetProfile、DatasetMeasurementProfile）、
六套指纹机制、三处字段角色推导、三链 profile 生成，彼此无版本绑定：同一数据集在
ingest / query / map / replay 四条路径上语义身份不可对账，"数据变了"只能靠局部机制
（ads schema_fingerprint 列、ref content_hash、mapspec profile_fingerprint 双前缀格式）
各自感知；gis_memory 的 dataset_version 失效规则因生产链从未写入 version_token 而
空转。

## Decision

1. **新增 `app/lib/gis/dataset_descriptor.py`：`GISDatasetDescriptor`（v1）** ——
   数据语义的权威契约记录：versioned、有界（fields≤64、refs≤8、signals≤16、载荷
   ≤96KiB）、确定性（canonical JSON，derived_at 不入指纹）、可序列化、fail-closed
   反序列化。指纹复用 `app/lib/data/fingerprints.py` 原语，不新增哈希实现。
2. **单向映射**：descriptor → {DatasetProfile, DatasetMeasurementProfile, 语义视图,
   D1 兼容视图} 四个投影全部委托既有实现；禁止任何模块反向"猜"语义或另立 camelCase
   词表（resolver 词表唯一出口仍是 `DatasetProfile.to_resolver_profile`）。
3. **`app/services/dataset_semantics/`**：builder（有界 first-N 采样 ≤200 要素，
   正向路径零新增全表扫描）、store（session 内 content-addressed 版本链，每 key ≤8
   版本，原子指针，损坏/未知版本 fail-closed 显式 reason codes）、reuse（指纹对账 →
   valid/stale/recompute/unknown + 稳定 reason codes）。
4. **四条生产路径接同一指纹**：ingest（profile 后 build + artifact metadata 键）、
   data fabric（explain_query 证据零扫描投影指纹，只算不入 store —— catalog
   项是目录级身份域）、map（mapspec source_profile 产出点 attach
   `descriptor_fingerprint`，ref 路径解析 ingest 铸造的同一指纹）、replay
   （`evaluate_reuse` 库面 + MapSpec source 已携带指纹；**重放/恢复面的
   生产消费接线为后续工作**，见 Out of Scope）。同一证据域内指纹一致；
   跨证据域（ingest 富证据 vs fabric/inline 薄证据）指纹如实不同，比较必须
   经 `evaluate_reuse`/`compare_descriptors` 而非裸字符串相等。
5. **消费面**：data_qualification 增可选 descriptor 入参（freshness guard →
   `DESCRIPTOR_STALE_<CLASS>` 降级码；投影供给既有事实路径，缺席时现状不变；
   **生产调用方传入 descriptor 的接线为后续工作**）；gis_memory harvest 的
   version_token 优先取 descriptor_fingerprint（失效对账闭合）；
   context_layers data 域透传 descriptor fingerprints；context_bridge 提供
   recovery 面增补缝（生产写入方接线为后续工作）。
6. **双语高风险语义 corpus**（zh/en）：数量vs密度、比率vs总量、百分比vs分数、带符号
   变化、类别、时间字段、坐标字段、未知单位、冲突单位 —— 验收矩阵驱动
   derive→descriptor→compare 全链断言。

## Consequences

- 正向：语义身份全链可对账（同一数据集四路径同指纹）；任何 schema/类型/单位/CRS/
  时间变化产生可解释 stale/qualification 结果；context reuse 与地图重放获得诚实
  staleness 披露；不增加全表扫描，载荷硬上限。
- 负面/成本：新增一个契约模块 + 一个 service 包；消费方接线为 additive 小缝
  （mapspec_schema 2 个 source 类各 +1 可选字段、data_qualification 可选入参、
  harvest token 优先级、context_layers data 域 +≤16 键）。
- 非目标（Out of Scope）：不重写 #1488 推导内部；不改 data_fabric/contracts.py 冻结面、
  artifact_registry.py、governor/dispatch_adapter.py、replay/* 本体；不做遥感模型
  训练/百 G 栅格处理；不统一旧六套指纹机制（各自语义仍服务既有链，对账经 store 的
  inputs 证据）；**后续工作**：MapSpec 重放/恢复面调用 `evaluate_reuse` 的生产接线、
  qualification 生产调用方传入 descriptor、recovery durable facts 写入
  descriptor_fingerprint（context_bridge 已备缝）。

## 增补（G01 · 2026-10-21）：多角色绑定与恢复面接线

原 Out of Scope 的三项后续工作，G01 落地如下（全部 additive，缺省 = 基线行为）：

1. **多数据集 → 任务角色的权威绑定**（`dataset_semantics/role_binding.py`）：
   会话语证按 ref_id 排序（≤16、零数据扫描）→ 每角色六态绑定
   （bound/not_found/ambiguous/not_authorized/stale/partial）。同名/同证据
   多候选 → `ambiguous` 带候选清单，**绝不取首个**；显式绑定 ref 越出会话
   作用域 → `not_authorized` fail-closed（不泄漏指纹）；记录面指纹锚在场
   （显式或证据路径命中）→ 复用 `evaluate_reuse` 判 stale。角色词表复用
   `workflow_schema.DATA_ROLES`，绑定层与资格层状态词表显式分层不混用。
   `primary_session_descriptor` 保留为单 descriptor 兼容 shim。
2. **角色级资格供给**（`qualify_workflow_data_roles` 增 `role_evidence`）：
   `{role → {descriptor, expected_descriptor_fingerprint}}` 在场时命中角色用
   **各自**数据集事实裁决（多数据 session 第二数据集事实可达）；
   `compile_workflow` / `compile_workflow_v4` 全链透传；生产入口
   `semantic_tools.compile_workflow_semantics` 首编定 recipe → 绑定 →
   角色证据为权威重编译（不再传 primary descriptor），输出
   `data_role_bindings`（审计面）。
3. **恢复链语义身份**（`project_runtime/resume.py`
   `_reconcile_descriptor_semantics`）：durable resume 在载荷重水合后把旧
   session 语义 store 的 descriptor 迁入新 session（指针改写指纹不变，
   与 resume_anchor 同一模式），并按 MapSpec 快照 source 冻结指纹对账
   （evaluate_reuse → valid/recompute/unknown）；verdict 落
   `ResumeReceipt.descriptor_reconciliation`，漂移只披露不阻断（消费面
   按 verdict 重评，不静默续渲染）。
4. **metadata-only 兼容预检**（`dataset_semantics/preflight.py`）：
   多数据联接的 CRS/bbox 相交/时间覆盖重叠（月精度，open-ended 不外推）/
   同名字段单位/密度-分母/期望角色判据，typed gaps（Agent 可消费最小澄清）；
   unknown ≠ pass（证据不足不判 compatible）；接入 semantic_tools 输出
   `data_preflight`（advisory）。

诊断速查见 `docs/dataset-readiness-diagnostic-matrix.md`；验收矩阵见
`tests/unit/dataset_semantics/`（角色绑定/接线/恢复/预检/成都六场景）。
