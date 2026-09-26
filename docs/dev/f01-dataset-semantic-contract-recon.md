# F01 — GIS Dataset Semantic Contract vNext · Recon（2026-09-26）

基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +0800，
Merge PR #1494）。本地 master 工作目录落后 origin 187 commits 且带未提交改动 —— **本方向全部
工作只在 worktree `../wt-webgis-f01-dataset-semantic-contract-vnext-20260926-9e1ad229`（branch
`zcode/f01-dataset-semantic-contract-vnext-20260926-9e1ad229`）进行**，主工作目录零写入。

## 1. 最近 50 commits 摘要

- 功能大波次 #1479–#1488（2026-09-21 合入）：map verify/repair 闭环、cartographic grammar
  基座、Pi↔harness 边界收敛、ABI 2.0、产品→导出一致性、统一 cost/planning、canonical turn
  lifecycle、trace/replay 闭环 v2、layered GIS context scopes、**#1488 measurement semantics
  contracts（方向 2，本方向的直接前作）**；随后 b5187620 做 post-#1488 收敛（ADR 重编 0204×10、
  scope-matrix、ruff）。
- #1489–#1496 全部是 dependabot 依赖升级（pandas/pypdf/jsdom/actions），无功能语义变化。
- 因此 **#1488 的 Out of Scope 在最新 master 上仍然成立**：D1DatasetDescriptor 迁移、
  V9 持久化、字段级 ontology 传播、MapSpec/前端 fingerprint 传播均未实现。

## 2. Open PR（8 路，全部为并行方向，必须绕行热区）

| PR | 方向 | 与 F01 的重叠风险 |
|---|---|---|
| #1497 | F12 map plan compiler | decision_record.py（F09 也改）；plan compiler 消费 qualification 输出（只读，无冲突） |
| #1498 | F13 render runtime data plane | render/cache identity 面；与 descriptor fingerprint 无文件交集 |
| #1499 | F08 workflow resource scheduler | governor/estimation；`dispatch_adapter._df_cost_view` 注释明说 feature_count 事实待 profile 层供给 —— F01 提供 descriptor 投影但不改 dispatch_adapter（三 PR 共改该文件） |
| #1500 | F15 visual observation | 无文件交集 |
| #1501 | F11 composition contract | mapspec_schema.py、mapspec_store.py 热区 |
| #1502 | F10 cartographic grammar production | **semantic_inputs.py（新文件）+ mapspec_schema.py + mapspec_store.py**；F01 对 mapspec_schema 只做 additive 字段（每 source 类 1 行） |
| #1503 | F09 trace/replay oracle v3 | replay/roundtrip 面；F01 的重放语义只走新增 reuse 模块，不改 replay/* |
| #1504 | F14 publication/export parity | artifact_registry.py、export_lineage.py 热区；F01 **零改动** artifact_registry（metadata 键经生产方传入），其"语义 corpus"是导出渲染用例，与 F01 数据语义 corpus 不同域 |

## 3. 现状四结构（精确形状见设计文档 §2）

- `DatasetDescriptor`（`app/schemas/data_fabric_schema.py:72-104`）：诚实缺省红线；**无 version/fingerprint**。
- `D1DatasetDescriptor`（`app/services/data_fabric/contracts.py:91-116`）：contract_version="1.0"
  冻结规则（只许加可选字段）；有 version/version_pinned/TemporalCoverage/Freshness/QualitySignals/
  CostHint；**无 fingerprint**。生产者仅 `agent_swarm/data_scout.py:248` 与
  `data_fabric/retrieval/cards.py:68`。
- `DatasetProfile`（`app/lib/gis/dataset_profile.py`，V4/V5）：统一有界画像；`to_resolver_profile()`
  是 resolver camelCase 词表**唯一适配出口**；上限 MAX_PROFILE_FIELDS=64 / MAX_GEOMETRY_TYPES=8；
  五源零扫描构造器。
- `SemanticDatasetProfile`（`app/lib/gis/semantic_profile.py`，ADR-0092）：14 角色枚举 + 证据分级；
  自我声明为 DatasetProfile 的派生投影。
- `DatasetMeasurementProfile`（`app/lib/gis/measurement.py`，ADR-0207 = PR #1488）：
  MeasurementKind 11 值 + UnitDimension 10 值 + CANONICAL_UNITS 闭表 + 稳定检查码；
  from_dict 未知版本 fail-closed；自我声明"派生视图，不是第二数据真相"。

## 4. 指纹机制现状（6 套，F01 不新增第 7 套哈希原语）

1. `data_fabric/fingerprint.py` DatasetFingerprintService（descriptor 哈希）；
2. `app/lib/data/fingerprints.py` canonical_fingerprint / FingerprintSet / classify_change /
   staleness_verdict（**V3 契约层统一原语**）；
3. `app/lib/data/versioning.py` SourceRevision{schema_fingerprint} / compare_revisions / 版本链；
4. `app/schemas/ref_descriptor.py` content_hash（V6 默认 ON）；
5. `app/models/ads_fabric.py:44` schema_fingerprint DB 列 + ads5 golden 事件；
6. `data_ingest.compute_payload_fingerprint`（进程内 dedup 键）+ mapspec 的
   `profile_fingerprint`（两种前缀格式并存：mapspec_store `"profile-sha256:"` vs
   tool_dispatch `fingerprint_metadata`）。

F01 决策：descriptor 指纹一律复用 `app/lib/data/fingerprints.py` 的 canonical_fingerprint；
变更分类复用 ChangeClass/staleness_verdict；**不新增哈希原语**。

## 5. 漂移/重复风险清单（F01 收敛对象，均不改既有推导内部）

1. 字段角色推导三套：semantic_profile._NAME_RULES（14 角色）vs
   data_fabric/semantic/field_roles.py（time>geo>id>measure 单角色）vs
   workflow_schema DENOMINATOR/TIME_FIELD_HINTS。→ descriptor 把角色结果冻结为字段级
   evidence，消费面只读 descriptor 投影；三套推导保持各治（本方向不重写）。
2. 几何归约两处（dataset_profile.geometry_kind vs data_qualification._geometry_category）。
3. 数值字段归约两处；时间事实三处。
4. profile 生成三链（profile_geojson_source / data_quality build_profile_for_payload /
   semantic_tools）无 fingerprint 绑定。→ descriptor 是绑定点。
5. data_quality/profile.py:386 手动 `fields_status="explicit"` 绕过校验器（记录，不在本
   方向修 —— 非本方向 P0/P1）。

## 6. Context / invalidation 现状

- `gis_memory/contract.py`：RULE_DATASET_VERSION 已是四大失效规则；KIND_DATASET_SEMANTICS /
  KIND_FIELD_ROLE TTL=None 靠 dataset_version 规则失效。
- `gis_memory/harvest.py:123-131`：version_token 取自 `source.profile.version_token` ——
  **生产链从未写入该键 → version_tokens 几乎恒空，失效对账空转（确认缺口）**。
- `gis_memory/store.invalidate_for_dataset(dataset_key, version_token)`：同 token 保留、
  异 token 失效 —— 机制完备，缺生产触发。
- `context_layers.py` data 域已带 `recovery["source_fingerprints"]`（≤16），但无失效联动。
- `data_profile/unified.py:196` install_invalidation_hook（ref 失效钩子）存在。

## 7. 五列表

**Overlap（与本方向文件重叠的 open PR 热区）**：mapspec_schema.py（#1502/#1504）、
mapspec_store.py（#1501/#1502）、governor/dispatch_adapter.py（#1498/#1499/#1503）、
artifact_registry.py（#1504）、completion/pipeline.py（#1500/#1504）、
runtime/decision_record.py（#1497/#1503 新文件）。

**Already Done（不重做）**：measurement/unit/field resolver 推导（#1488）；layered context +
invalidation 引擎（#1487）；FingerprintSet/classify_change 原语；D1 契约与冻结规则；
qualification 五态机与 reason codes 体系；memory invalidate_for_dataset 机制。

**Still Missing（本方向交付）**：versioned GISDatasetDescriptor 契约与 fingerprint；
四结构单向映射；ingest/query/mapspec/registry 生产接线；descriptor→qualification stale
reason codes；MapSpec source descriptor_fingerprint（additive）；语义持久化 store
（bounded/migration/fail-closed）；harvest version_token 生产触发；zh/en 高风险语义 corpus。

**Must Not Touch**：#1488 三模块的推导内部（measurement/field_resolver/scale_semantics）；
data_fabric/contracts.py 冻结面；artifact_registry.py 本体；governor/dispatch_adapter.py；
replay/* 本体；completion/pipeline.py。

**Integration Seams（全部 additive/小缝）**：data_ingest/pipeline.py（descriptor build+
metadata 键）；mapspec_store.source_profile（build+attach，try/except additive evidence）；
mapspec_schema.py（2 个 source 类各 +1 可选字段）；data_qualification.py（可选 descriptor
入参 + stale guard）；context_layers.py data 域（+descriptor fingerprints）；gis_memory/
harvest.py（version_token 优先取 descriptor_fingerprint）。

## 8. Master 已知失败基线

尚未跑全量（资源纪律：不跑无意义 xdist 全核）。策略：实现前先跑与本方向直接相邻的
targeted 套件记录基线（test_measurement_semantics_v1 / test_data_qualification /
test_spatial_meta_profiler / test_data_fabric_profile_round4），本分支新增失败与基线严格
区分；全仓状态以 GitHub CI 为准（不等待）。
