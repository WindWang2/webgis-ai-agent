# DATA_READINESS_CONTRACT — 数据准备与资格判定链契约（G03 / ADR-0286）

> 状态：随 zcode/goal-geodata-readiness-fabric-20261107 PR · 基线 master 585812ee4
> 权威实现：`app/services/gis_harness/data_qualification.py`（五态资格）、
> `app/services/dataset_semantics/`（身份证/预检/绑定/闸门）、
> `app/services/data_ingest/pipeline.py`（摄入管线）

## 1. 词表映射（验收词表 ↔ 仓库权威词表）

本仓库**不新增**资格枚举。任务侧的 readiness 词汇按下表映射到既有五态
（`QUALIFICATION_STATES`，G01 稳定契约，测试锁定）：

| 任务验收词 | 仓库权威态 | 依据与判据 |
|---|---|---|
| ready | `eligible` | `qualify_data_role` 全判据通过（科学前置/角色/单位/密度） |
| needs_mapping | `transform_required`（修复操作 `derive_field`/`aggregate`） | 角色绑定 ambiguous / 字段提案 needs_clarification |
| needs_reprojection | `transform_required`（修复操作 `reproject`） | CRS 判据 + `crs_safety` 等价集外 |
| needs_cleaning | `transform_required`（`repair_geometry`/`normalize`/`filter_null`） | quality issues（nodata/空值/几何无效） |
| unsafe | `blocked` | 反幻觉守护网关 BLOCK、GD-09 守卫拒绝、单位维度矛盾 |
| unavailable | `unknown`（画像缺事实）或 `SourceUnreachableError` | 画像缺席 ≠ 数据不合格；源不可达是 typed 异常非资格态 |

修复操作词表 `REMEDIATION_OPS`（8 种）与实现背书
`REMEDIATION_OP_BACKING` 在 `data_qualification.py:45-73`——经
CapabilityRegistry 对账，非平行词表。

## 2. 语义身份证（GISDatasetDescriptor，ADR-0265 扩展）

维度级证据三态（G03 GD-01）——`dimension_provenance: Dict[str, str]`：

| 维度 | observed | inferred | unknown |
|---|---|---|---|
| geometry_type / feature_count / bbox | 实际扫描 | — | 无扫描证据 |
| crs | 载荷声明/来源声明 | 坐标域识别（推导入口显式传入） | 未声明（**不虚构 4326**） |
| fields | fields_status=explicit | — | unknown |
| temporal | has_time_field=True | — | 无证据 |
| license / attribution | 声明通道非空 | — | 缺省（"" ≠ 未知许可证名） |

契约：
- 三态封闭（`PROVENANCE_STATES`），非法值在校验器静默剔除；
- **不入语义指纹**（证据分级变化 ≠ 语义身份变化，与 license 同理）；
- 字段级置信（role_confidence 等）仍在 FieldEntry——维度级是汇总非替代。

## 3. 数据资格闸（工具分发面，ADR-0286 D4）

`app/services/dataset_semantics/dispatch_gate.py`，接线于
`tool_dispatch_service` 1.45 反幻觉网关之后：

| 记录状态 | 裁决 | 码 |
|---|---|---|
| ok + 指纹与记录面一致 | pass + `qualification_context`（crs/要素数/bbox/kind，模型可见） | — |
| ok + 期望指纹不一致 | **blocked** | `DATA_QUALIFICATION_STALE` |
| corrupt / unsupported | **blocked** | `DATA_QUALIFICATION_DESCRIPTOR_CORRUPT` |
| missing | pass + 披露 `descriptor_missing`（strict 模式 blocked） | `DATA_QUALIFICATION_DESCRIPTOR_MISSING` |

- 豁免命名空间：`ref:raster/`、`ref:profile-`、`ref:redis-unavailable-`；
- kill switch：`DATA_QUALIFICATION_GATE=0`；严格模式：`DATA_QUALIFICATION_GATE_STRICT=1`；
- 闸门 fail-open（自身异常 = 放行 + best_effort 日志）；
- 期望指纹来源：`qualification_bridge.recorded_fingerprints`（recovery
  source_fingerprints，记录面权威）。

## 4. 任务范围（GD-05）

- `preflight_multi_dataset(..., task_scope={place?, bbox?})`：
  dataset bbox 与任务 bbox 不相交 → `PREFLIGHT_SCOPE_MISMATCH`；
  bbox 未知 → `PREFLIGHT_SCOPE_UNVERIFIABLE`（披露不硬失败）；
  未声明 scope → 不检查（向后兼容）。
- `task_scope.scope_fingerprint(place, bbox)`：范围指纹（place 与 bbox 是
  不同资格粒度）；`evaluate_scope_staleness(recorded, current)`：城市/视口
  切换 → stale + 可解释 reason（消费面：MapSpec 复用门对账）。
- 零记录诚实：空 FC 源如实零要素（`feature_count=0, truncated=False`），
  绝不合成数据；源不可达是 `SourceUnreachableError` 不是空结果。

## 5. 恶意输入守卫（GD-09，`data_ingest/geojson_guard.py`）

| 输入 | 拒绝码 |
|---|---|
| NaN/±Inf 坐标 | `INVALID_COORDINATES_NONFINITE` |
| 嵌套深度 > 64 | `STRUCTURE_TOO_DEEP` |
| 单属性字符串 > 256KB | `PROPERTY_STRING_OVERSIZED` |

拒绝发生在 store 之前（无 ref 铸造）；O(features) 有界（深度帽剪枝 +
每要素步数帽 20k）。

## 6. 协作取消与幂等（GD-06）

- `ingest(..., cancel_event=...)`：阶段边界检查；store 后取消先回滚孤儿
  ref（`rollback-on-cancel` 步）；取消码 `INGEST_CANCELLED`；
- 重试幂等：同载荷指纹 dedup 复用（取消→重试不产生重复逻辑对象，测试锁定）。

## 7. 既有 API 兼容性证据

- `QUALIFICATION_STATES` 五态词表 characterization 测试锁定（本 PR 未增删）；
- `preflight_multi_dataset` 新参数全部缺省，旧调用行为逐位不变（scope 不
  检查测试锁定）；
- `IngestPipeline.ingest` 新参数（license/attribution/cancel_event）全部
  缺省 None；`IngestResult` 新字段缺省空——`to_dict()` 键值 `None` 化向后
  兼容；
- 上传路由表单新字段可选；`GISDatasetDescriptor` 新字段默认空 dict，
  旧载荷 from_dict 兼容（无迁移）；
- 既有 43 项 qualification/ingest 测试与 113 项 dataset_semantics/explorer
  测试全绿（见 LOOP_LEDGER）。
