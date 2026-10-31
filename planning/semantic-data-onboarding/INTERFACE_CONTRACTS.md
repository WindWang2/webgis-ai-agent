# INTERFACE_CONTRACTS — M2 冻结契约

本文件是全任务接口契约的唯一真相；实现偏离必须先改这里并记录理由。

## 1. 角色词表（canonical，`app/services/data_preparation/vocabulary.py`）

```
FIELD_ROLES = {
  "id", "name", "address", "x", "y", "lon", "lat",
  "time", "category", "value", "admin_code", "denominator", "weight",
}
```

- 到既有词表的映射（adapter，只做投影不改写既有系统）：
  - explorer `auto_field_mapping` 键：name/address/lat/lon ⊂ 本词表
  - data_fabric `infer_field_roles`：time↔time, geo→(lon/lat/admin_code), id↔id, measure→(value/denominator/weight)
  - descriptor `FieldEntry.roles`：提案确认后以 `evidence="user_confirmed|inferred"` 写入（v2 版本化）
- x/y = 投影/局部坐标对；lon/lat = 地理坐标对。提案引擎必须区分（值域+列名联合判定）。

## 2. 字段提案（`field_proposal.py`）

```python
@dataclass(frozen=True)
class RoleCandidate:
    role: str            # FIELD_ROLES 成员
    confidence: float    # [0,1]，两级：>=0.85 high；0.5–0.85 medium；<0.5 不提案
    reasons: tuple[str, ...]      # 稳定码（见 REASON_CODES），供 UI/i18n

@dataclass(frozen=True)
class FieldProposal:
    field: str
    dtype: str                 # 来自 profile
    candidates: tuple[RoleCandidate, ...]   # 按 confidence 降序
    rejected: tuple[tuple[str, str], ...]   # (role, reason_code) 字段级被拒候选
    needs_clarification: bool  # top1 < 0.85 或与次候选差距 < 0.2 时 True
```

> 契约变更记录（M3 实现时）：`rejected` 从 RoleCandidate 上移至 FieldProposal
> —— 被拒候选是字段级事实，逐候选重复携带同一列表是建模缺陷。

- 输入：字段名 + profile 列统计（null 率/min/max/distinct/样本值，全部来自现有 profiler，不另扫数据）。
- REASON_CODES（稳定词表，中英双语渲染在 UI 层）：`NAME_PATTERN / VALUE_RANGE_LAT / VALUE_RANGE_LON / RANGE_PLANE_PROJECTED / TIME_PARSED / CATEGORY_CARDINALITY / NUMERIC_MEASURE / ID_UNIQUENESS / SYNONYM_ZH / SYNONYM_EN / AMBIGUOUS_TIE / RANGE_CONFLICT`。
- 双语同义词语料（成对：role → 中/英 pattern 列表），含负例（如 "编号" ≠ 行政码）。

## 3. 空间检查收据（`spatial_receipt.py`）

```python
@dataclass(frozen=True)
class InspectionFinding:
    code: str            # 复用 app/lib/data/quality.py 既有码 + 新码见下
    severity: str        # ok | warn | blocked
    message_zh: str
    evidence: dict       # 有界（样本行号/计数/值域），绝不含整表
    suggestion_code: str # NONE / SWITCH_XY / FILTER_ROWS / DECLARE_CRS / PICK_TIMEZONE / CONFIRM_ROLE / ...

@dataclass(frozen=True)
class SpatialInspectionReceipt:
    input_ref: str
    geometry_type / srs / crs_source / axis_order_guessed / coordinate_range
    encoding / time_fields / tz_status / primary_key_check
    row_count / sample_rows_scanned
    findings: tuple[InspectionFinding, ...]
```

新码（quality.py 没有的）：`AXIS_ORDER_SUSPECT`（xy 对调）、`PK_DUPLICATED`、`PK_MISSING`、`DENOMINATOR_ABSENT`（角色级）。既有码直接透传不复制逻辑。

## 4. 准备计划（`plan.py` / `executor.py`）

```python
Step = 复合类型（closed union）:
  FieldMappingStep(role→field, source: proposal|user)
  AxisSwapStep(x_field, y_field)                 # 显式修正，receipt 必须先 AXIS_ORDER_SUSPECT
  FilterRowsStep(condition_code, expected_row_delta_max_frac)
  DedupStep(key_fields)
  RenameFieldStep(from, to)
  ReprojectStep(target_epsg)                     # 仅当 crs_source != assumed 才允许

@dataclass
class PreparationPlan:
    plan_id: str                 # "prep-{uuid16}"
    session_id / owner / input_ref
    input_fingerprint: str       # descriptor_fingerprint（dsd-v1:…）
    steps: tuple[Step, ...]
    plan_hash: str               # sha256(canonical json(steps)+input_fingerprint)，幂等键
    state: str                   # proposed | previewed | executed | verified | rolled_back | discarded
```

`PreparationReceipt`（执行产物，随 prepared ref 落 artifact metadata + provenance）：
`output_ref / output_fingerprint / quality_before / quality_after（issue 计数按码）/ row_before / row_after / dropped_reasons / steps_applied / user_confirmations / created_at`。

- 原件永不覆盖：输出写新 session ref（`ref:prep-…`），经 `provenance.record_transformation` + `mint_output_descriptor` 铸新 descriptor。
- 幂等：同 (session, plan_hash) 已 executed → 直接返回既有 receipt，不重算。
- dry-run：对采样子集（≤2000 行或全量取小者）执行纯函数步，产出预估 delta，不写任何 store。

## 5. HTTP API（`app/api/routes/data_preparation.py`，前缀 /api/v1/data-preparation）

| 方法 | 路径 | 语义 | 鉴权 |
|---|---|---|---|
| POST | /inspect | {source_ref 或 upload_id} → SpatialInspectionReceipt + FieldProposal[] + 有界样本预览 | 同 upload 纪律（Bearer + session owner） |
| POST | /plan | 构建计划（服务端校验步与前置 finding 匹配，如 AxisSwapStep 需 AXIS_ORDER_SUSPECT）→ plan(state=proposed) | 同上 |
| POST | /plan/{plan_id}/preview | dry-run → delta（不落盘） | 同上 |
| POST | /plan/{plan_id}/execute | 幂等执行 → prepared ref + receipt | 同上 |
| GET  | /plans?session_id | 列出本会话计划 | 同上 |
| DELETE | /plan/{plan_id} | state→discarded（仅未 executed 或回滚后） | 同上 |
| POST | /bindings/fields | {ref, roles: {field: role}} → 写回 descriptor v2（evidence=user_confirmed）| 同上 |

- 越权：跨 session/owner 的 ref → 404（与 uploads 纪律一致）；AST auth 扫描 + endpoint scope matrix 自动盯新路由（登记 scope）。
- 有界：inspect 返回样本 ≤ 50 行 × ≤ 64 字段；绝不回传整表。

## 6. Agent 工具（`app/tools/data_preparation_tools.py`）

单工具 `prepare_dataset`（tier=2, domains=["data"], side_effect="ref_write", produced_refs=["data"], result_size_policy="ref_offload"）：

输入：`source_ref`（必填，有界 ref）、`role_hints?`（{field: role}，来自用户对话）、`auto_execute?`（默认 false：仅 inspect+提案；true 时高置信且无 blocked 才执行）。

返回判别式：
```json
{"status": "prepared", "output_ref": "...", "receipt_summary": {...}}
{"status": "requires_input", "clarifications": [{"field": "...", "candidates": [...]}], "blocked_findings": [...]}
{"status": "degraded", "output_ref": "...", "degradations": [{"code": "...", "detail_zh": "..."}]}
{"status": "blocked", "blocked_findings": [{"code": "...", "evidence": {...}}]}
```

- 挂入 `app/tools/__init__.py::_TOOL_MODULES`；能力目录经既有 registry→catalog 链自动聚合（新 capability `data_preparation` 需过 descriptor validate 词表，若词表封闭则在 descriptor.py 最小适配 patch）。
- 绝不把行数据放进 llm_payload（ingest_tools 同款纪律）。

## 7. 前端（`frontend/components/sidebar/data-preparation/`）

- `prepare-flow.tsx`：上传/选已有数据 → inspect 面板 → 字段映射复核（候选 chip、确认/改选、低置信度显式要求选择）→ 空间收据视图（findings 按严重度）→ 计划预览 delta → 执行 → prepared ref → 「加载到地图」（复用 data-sources-tab.tsx:301-369 范式）。
- API client 增 `lib/api/data-preparation.ts`；`DatasetDescriptor` TS 类型补 roles/qualification（additive）。
- i18n：新增键全部 zh-CN/en 成对（沿用 `__GEOAGENT_LOCALE__` 机制）。
- 复用：dataset-manager 的上传、InlineNotice/StatusBadge/toast、preview 表格。

## 8. 中心文件最小适配清单（预期 diff 面）

| 文件 | 预期改动 | 性质 |
|---|---|---|
| `app/tools/__init__.py` | _TOOL_MODULES +1 行 | 最小 |
| `app/tools/descriptor.py` | （仅当 capability 词表封闭）补 1 词 | 最小 |
| `app/api/routes/__init__.py` 或路由注册点 | 挂新 router | 最小 |
| `docs/integration/ownership.json` | 新模块归属登记（如该文件管辖） | 最小 |
| OpenAPI 快照 `tests/quality/snapshots/openapi.json` | API_SNAPSHOT_UPDATE=1 再生 | 生成物 |
| `api_contract_fields.json` | 同上再生 | 生成物 |

不触碰：`app/tools/registry.py`、`tool_dispatch_service.py`、`mapspec_schema.py`、`catalogue.ts`（除非 M7 发现硬阻塞，先记录再动）。
