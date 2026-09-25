# ADR-0215: GIS Dataset Semantic Contract（GISDatasetDescriptor vNext）

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
   data fabric（descriptor fingerprint 计算点旁路 build）、map（mapspec source_profile
   产出点 attach `descriptor_fingerprint`）、replay/restore（evaluate_reuse 诚实披露）。
5. **消费面**：data_qualification 增可选 descriptor 入参（freshness guard →
   `DESCRIPTOR_STALE_<CLASS>` 降级码；投影供给既有事实路径，缺席时现状不变）；
   gis_memory harvest 的 version_token 优先取 descriptor_fingerprint（失效对账闭合）；
   context_layers data 域携带 descriptor fingerprints。
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
  inputs 证据）。
