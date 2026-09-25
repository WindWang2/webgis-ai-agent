# F01 — GIS Dataset Semantic Contract vNext · Design（ADR-0215）

## 0. 目标一句话

把"数据语义"从各模块各自推导的易逝结果，升级为**一个 versioned、有界、确定性、可序列化、
可比较、可持久化**的全链契约：`GISDatasetDescriptor`。ingest / query / map / replay 四条
路径对同一数据集版本产出并引用同一 `descriptor_fingerprint`；任何 schema / 字段类型 /
单位 / CRS / 时间语义变化都产生可解释的 stale / qualification reason code。

## 1. 架构决策（D1–D7）

**D1 · 单一语义身份，不在推导层动刀。**
`GISDatasetDescriptor` 是数据语义的**权威契约记录**（authoritative semantic contract
record）：它在推导边界（已有 DatasetProfile ⊕ SemanticDatasetProfile ⊕
DatasetMeasurementProfile 全部算完之后）把语义事实**冻结**为一份有界载荷并给出指纹。
#1488 的三个推导模块保持原样（仍是"怎么推"的单一事实源）；descriptor 只回答"推出来的
语义是什么版本"（"是什么身份"）。不合并、不重写、不产生第二套推导。

**D2 · 单向映射：descriptor → 投影，禁止反向。**
从 descriptor 只允许单向重建四个既有形状（全部委托既有实现，词表零复制）：
- `to_dataset_profile()` → DatasetProfile（消费面：qualification / symbology / resolver /
  scientific_preconditions 全部经既有 `to_resolver_profile()` 出口 —— descriptor 不新造
  camelCase 词表）；
- `to_measurement_profile()` → DatasetMeasurementProfile（经既有 from_dict fail-closed）；
- `to_semantic_view()` → 角色/证据的有界 dict（角色词表复用 SemanticFieldRole 值域）；
- `to_d1_view()` → D1DatasetDescriptor 兼容 kwargs（supply-side 投影；contracts.py 冻结
  面零改动）。
任何模块不得再从原始数据"猜"一份 descriptor 已有的语义；新消费方一律走投影。

**D3 · 指纹复用 V3 原语。**
- `schema_fingerprint` = canonical_fingerprint(schema 事实子集)（fields/geometry/crs/
  raster shape/temporal 字段事实）；
- `descriptor_fingerprint` = `"dsd-v1:<sha256>"`，对**全部语义内容与证据域**（schema
  子集、字段清单成员、角色、量纲、质量/采样证据）哈希；`derived_at`、`dataset_key`、
  `source_refs`、`provenance` 等**指针/易变字段不入哈希**（语义身份 ≠ 指针身份：
  同一数据语义无论经哪个 ref/artifact 命名、哪条路径到达，指纹相同）；
- 变更比较 `compare_descriptors(old, new) → DescriptorDelta{change_class, verdict,
  reason_codes, field_diffs(≤64)}`：change_class 复用 `FingerprintSet.classify_change`，
  verdict 复用 `staleness_verdict`；reason codes 是稳定机器可读码（§4）。
- 不新增第 7 套哈希实现；与既有 profile-sha256 / ref content_hash 的对账由 store 在
  写入时记录 `inputs` 证据（不强行统一旧机制 —— 它们各有语义）。

**D4 · 有界与确定性（硬上限全部为常量并测试锁定）。**
MAX_FIELDS=64（同 DatasetProfile）、roles/field ≤6、evidence/field ≤6、checks/field ≤4、
source_refs ≤8、quality signals ≤16、provenance ≤8、sampling evidence 单条 ≤256 字符、
value samples 只进入推导、**不进 descriptor 载荷**（只留 `sampling` 计数/策略证据）。
载荷字节上限 `MAX_DESCRIPTOR_BYTES = 96 KiB`，store 写入超限 fail-closed（拒收 +
`DESCRIPTOR_TOO_LARGE`）。同输入（同 profile 族输入）恒同指纹 —— canonical JSON + 排序。

**D5 · 持久化：session 内 content-addressed 版本链。**
`app/services/dataset_semantics/store.py`：
- 布局 `<session_dir>/dataset_semantics/<dataset_key 哈希>/<fingerprint>.json`
  （内容寻址，天然幂等）+ `<session_dir>/dataset_semantics/<dataset_key 哈希>/head.json`
  指针（原子写 tempfile+os.replace，同 mapspec/store.py 纪律）；
- 每 dataset_key 保留最近 `MAX_VERSIONS_PER_DATASET = 8` 个版本，写入时 pruning；
- 读：head 指针 → 载荷 → `migrate_payload`（v1 直收；未知/未来版本 fail-closed 返回
  `DESCRIPTOR_VERSION_UNSUPPORTED`，不猜）；JSON 损坏 → `DESCRIPTOR_STORE_CORRUPT`
  （fail-closed：调用方拿到显式错误码，绝不拿到"猜出来的语义"）；
- 跨进程一致性：指纹即身份，同指纹重复写幂等；head.json 供读者；并发写以原子替换
  收敛（last-writer-wins，内容寻址使任意收敛结果都自洽）；
- session_dir 复用 `mapspec/store.py` 的 `get_session_dir` 同一基座（同一 session 路径
  真相，不另造路径推导）。

**D6 · 生产接线 = 四条路径同一指纹。**
1. **ingest**（`data_ingest/pipeline.py`）：profile 完成后 → `derive_descriptor_from_v3`
   （有界值采样：首 ≤SAMPLING_FEATURE_CAP=200 要素、≤16 数值字段、每字段 ≤200 值，
   确定性 first-N）→ derive semantic/measurement（委托 #1488）→ descriptor → store →
   fingerprint 写入 artifact metadata（`descriptor_fingerprint` 单键，经既有
   register_artifact metadata 通道，**artifact_registry.py 零改动**）。
2. **query/data fabric**（`data_fabric/manager.py` explain_query）：零扫描投影
   `build_descriptor_from_fabric_descriptor` → 结果证据带 `descriptor_fingerprint`
   （只算不入 store —— catalog 项是目录级身份域，与 session ref 身份域不同）。
   fabric 薄证据域的指纹与 ingest 富证据域**如实不同**（sampling.strategy=
   `descriptor_projection` 标记证据域）；跨域比较必须经 `evaluate_reuse`/
   `compare_descriptors`，不得裸字符串相等。
3. **map**（`mapspec_store.source_profile`）：ref 路径优先从 store 解析 ingest 铸造的
   descriptor（同一数据 → 同一指纹，DoD 主链）；miss 时 ref descriptor 零扫描补铸；
   inline 路径从授权扫描产物就地投影 → source dict 加 `descriptor_fingerprint`
   （try/except additive evidence：失败不阻断制图路径，只少一个键 + log）。schema 侧
   `GeoJSONMapSpecSource` / `DataFabricMapSpecSource` 各 +1 可选 typed 字段（前端
   `types.generated.ts` 为其生成物，需同步再生成）。
4. **replay/restore**：`dataset_semantics/reuse.py` `evaluate_reuse` /
   `evaluate_reuse_with_history`（指纹对账 → ReuseDecision{verdict, reason_codes,
   delta}）已交付为库面 + MapSpec source 指纹已随 spec 持久化；**重放/恢复面的生产
   消费接线（restore 时调 evaluate_reuse 并披露 verdict）为后续工作**（热区避让：
   restore 面在 #1498/#1503 触碰范围内）。

**D7 · 消费面收敛点。**
- `data_qualification.qualify_workflow_data_roles` / `qualify_data_role` 新增可选
  `descriptor` 入参：在场时先做 freshness guard（期望指纹 ≠ descriptor 指纹 →
  state 按 staleness verdict 降级 + `DESCRIPTOR_STALE_<CLASS>` reason code）；随后
  走 `descriptor.to_dataset_profile().to_resolver_profile()` 供给既有事实路径 ——
  无 descriptor 时行为与现状逐字节一致（向后兼容，零回归风险）。
- goal satisfaction 消费 qualification evidence（既有通道），reason codes 自动透传，
  无需改 completion/pipeline.py（热区避让）。
- context reuse：`gis_memory/harvest.py` version_token 优先取
  `source.descriptor_fingerprint`（生产触发缺口闭合）→ `invalidate_for_dataset`
  版本对账从"恒空转"变为真实生效；`context_layers` data 域追加 descriptor fingerprints
  （≤16，同现有 budget 纪律）。

## 2. GISDatasetDescriptor 契约形状（v1）

```
GISDatasetDescriptor
├─ descriptor_version: Literal[1]          # from_dict 未知版本 fail-closed
├─ dataset_key: str                        # 会话内稳定身份（ref/artifact id，≤200）
├─ kind: "vector"|"raster"|"table"|"unknown"
├─ source_refs: List[SourceRef] (≤8)       # {type, ref, fingerprint?}  引用不搬运
├─ schema: SchemaFacts                     # schema_fingerprint 的哈希输入
│   ├─ geometry_types ≤8, feature_count?, bbox?, crs
│   ├─ raster {width,height,band_count,nodata,pixel_size,dtype}?
│   └─ fields: List[FieldEntry] (≤64)
│       └─ {name≤128, dtype, nullable?, null_ratio?, roles≤6,
│           measurement_kind, unit_dimension, unit,
│           kind_confidence, unit_confidence,
│           domain_hint≤2, center_hint?, evidence≤6, checks≤4}
├─ temporal: {has_time_field?, time_field?, coverage_start?, coverage_end?,
│             granularity?, observation_count?}      # 诚实缺省 None
├─ quality: {null_ratio_max?, value_variance?, duplicate_coordinate_count?,
│            unique_coordinate_count?, longitude_convention?,
│            signals ≤16}                            # 信号为稳定码字符串
├─ sampling: {strategy, feature_cap, fields_capped, samples_per_field,
│             notes≤4}                               # 采样证据有界
├─ provenance: List[{producer, method}] (≤8)
├─ derived_at: str                          # 不入指纹
├─ schema_fingerprint: str                  # "dsd-schema-v1:<hex>"
└─ descriptor_fingerprint: str              # "dsd-v1:<hex>"（全部语义内容）
```

## 3. DescriptorDelta / ReuseDecision reason codes（稳定词表）

- 分类级：`DESCRIPTOR_UNCHANGED` / `DESCRIPTOR_CRS_CHANGED` / `DESCRIPTOR_SCHEMA_CHANGED` /
  `DESCRIPTOR_CONTENT_EVIDENCE_CHANGED` / `DESCRIPTOR_METADATA_ONLY_CHANGED` /
  `DESCRIPTOR_UNCOMPARABLE`。
- 字段级（≤64 条，每条带 field 名）：`DESCRIPTOR_FIELD_ADDED` / `DESCRIPTOR_FIELD_REMOVED` /
  `DESCRIPTOR_FIELD_RETYPE` / `DESCRIPTOR_FIELD_UNIT_CHANGED` / `DESCRIPTOR_FIELD_KIND_CHANGED` /
  `DESCRIPTOR_FIELD_ROLE_CHANGED` / `DESCRIPTOR_FIELD_NULLABILITY_CHANGED`。
- 结构级：`DESCRIPTOR_GEOMETRY_CHANGED` / `DESCRIPTOR_RASTER_SHAPE_CHANGED` /
  `DESCRIPTOR_TEMPORAL_CHANGED` / `DESCRIPTOR_VERSION_BUMPED`。
- 基础设施级：`DESCRIPTOR_VERSION_UNSUPPORTED` / `DESCRIPTOR_STORE_CORRUPT` /
  `DESCRIPTOR_TOO_LARGE` / `DESCRIPTOR_MISSING` / `DESCRIPTOR_FINGERPRINT_MISMATCH`。

verdict 映射：复用 staleness_verdict（CRS/SCHEMA/CONTENT → recompute；METADATA_ONLY →
stale；NONE → valid；证据缺失 → unknown → 保守 recompute）。

## 4. 性能纪律

- descriptor build 全部来自既有 profile 事实 + **有界 first-N 采样**（≤200 要素）——
  正向路径不新增全表扫描；build+hash 是 O(fields)（≤64）非 O(rows)；
- store 读路径 O(1)（head 指针）+ O(payload) 反序列化（≤96 KiB 上界）；
- 性能探针测试：10 万要素合成 FC 上断言 build 只触达采样窗口（证据计数锁定） +
  descriptor 载荷 ≤ 上限。

## 5. 兼容与退役

- 全部接线 additive：descriptor 缺席 = 现状路径原样（ingest/qualification/mapspec 均
  try/except 或可选入参）；旧 session 无 dataset_semantics 目录 → 诚实
  DESCRIPTOR_MISSING，不虚构；
- 旧 MapSpec（无 descriptor_fingerprint）完全合法；恢复面按 DESCRIPTOR_MISSING 披露；
- 退役路径：未来 v2 descriptor → migrate_payload 先兼容读 v1，v1 写入停用时机由后续
  ADR 决定（本 ADR 只立 v1 + fail-closed 版本闸）。

## 6. 测试计划（验收矩阵）

- V1 契约：bounds/确定性/roundtrip/fail-closed/易变字段不入指纹。
- V2 投影等价：descriptor.to_dataset_profile().to_resolver_profile() == 原 profile 的
  resolver 投影（同一指纹下逐键相等）；to_measurement_profile 与原 profile 等值。
- V3 比较矩阵：CRS/schema/unit/kind/role/temporal/geometry 各维变化 → 精确 reason codes
  + verdict；无关 metadata 变化 → METADATA_ONLY/stale。
- V4 store：写读/幂等/版本链 pruning/损坏 fail-closed/未知版本 fail-closed/跨进程
  （新 store 实例）。
- V5 同域指纹一致性：同一合成数据集 ingest 铸造、store 现读、mapspec ref 路径解析
  得到**同一** descriptor_fingerprint（ingest↔map↔store↔replay 主链）；fabric/inline
  薄证据域只断言指纹格式与证据域标记（跨域一致性按 D6.2 语义经 compare 处理）。
- V6 qualification：fresh → 与 profile 路径同结果；fingerprint 不匹配 → DESCRIPTOR_STALE_*
  降级；无 descriptor → 现状不变。
- V7 context reuse：harvest 带 descriptor_fingerprint → version_token 写入 + 异版本失效。
- V8 corpus：zh/en 高风险语义用例矩阵（数量vs密度 / 比率vs总量 / 百分比vs分数 /
  带符号变化 / 类别 / 时间字段 / 坐标字段 / 未知单位 / 冲突单位）→ 期望 kind/unit/checks
  逐项断言。
- V9 性能探针：10 万要素采样窗口证据 + 载荷上限。
