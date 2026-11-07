# G06 — 稳定接口契约（子代理 / 实现 / 测试共同遵守）

> 唯一事实源纪律：本线**不新建**第二 ToolRegistry / AlgorithmRegistry / ExecutionCatalog /
> 第二 fingerprint 体系 / 第二认证体系 / 第二 Planner。所有新模块都是既有权威源的**只读投影或受控扩展**。
> 文件所有权：主 Agent 写 `app/lib/gis/capability_availability.py`、`method_eligibility.py`、
> `availability_rerank.py`、`provider_eligibility.py`、`capability_schema_parity.py`、
> `app/tools/capability_ecosystem_tools.py`、`app/lib/gis/data/*`；
> 测试子代理**只写** `tests/unit/gis/test_capability_ecosystem_*.py` 与 `tests/unit/tools/test_capability_ecosystem_*.py`；
> 冷审子代理**只读**，不写任何文件。任何两个 agent 不得同时改同一文件。

## A. CAP-01 `app.lib.gis.capability_availability`（核心）

```python
AVAILABILITY_SCHEMA_VERSION: str = "capability-availability/v1"
AVAILABILITY_STATUS_VOCABULARY: tuple[str, ...] = (
    "native_verified",          # 真实注册 + 8 面 conformance pass + 可执行 + 证据在场
    "available_unverified",     # 真实注册可执行，但 8 面 warning / 证据不全
    "declared_only",            # 仅 capability/algorithm 声明，或 tool status=planned
    "temporarily_unavailable",  # external_unavailable / 缺 provider / 扩展停用 / 依赖缺席
    "forbidden",                # 权限收缩、撤权、跨 owner，fail-closed
)
# 另保留 not_evaluated 作为「无法判定」的显式 reason，不猜、不计入 verified。
NOT_EVALUATED_REASON = "not_evaluated"

@dataclass(frozen=True)
class AvailabilityRecord:
    capability_id: str; algorithm_id: str; tool_id: str; provider: str
    scientific_status: str
    input_artifacts: tuple[str, ...]; output_artifacts: tuple[str, ...]
    crs_semantics: str; geometry_requirements: tuple[str, ...]
    fields: tuple[str, ...]; units: tuple[str, ...]
    resource_class: str; side_effect: str; determinism: str
    ownership: str; permission: str; extension_trust: str
    availability_status: str
    evidence_sources: tuple[str, ...]     # 8 面中的在场证据面名
    reason_codes: tuple[str, ...]         # 单一理由码：直接取自 ToolConformanceReport.faces[].reason
    unverified_reasons: tuple[str, ...]   # 显式未验证原因，verified 时为空
    registry_generation: str
    def to_dict(self) -> dict: ...

@dataclass(frozen=True)
class CapabilityAvailabilitySnapshot:
    schema_version: str
    records: tuple[AvailabilityRecord, ...]
    registry_generation: str       # runtime_manifest.fingerprint
    catalog_generation: str        # ExecutionCatalog.generation_fingerprint
    content_fingerprint: str       # canonical(records) 短摘要
    counts: dict[str, int]
    diagnostics: tuple[str, ...]
    truncated: bool
    degraded_sources: tuple[str, ...]
    def to_dict(self) -> dict: ...

def compile_availability_snapshot(
    *, registry=None, bootstrap: bool = False,
    certification_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
    max_records: int = 1024,
) -> CapabilityAvailabilitySnapshot: ...
def query_availability(
    snapshot, *, status=None, capability_id=None, tool_id=None,
    provider=None, owner=None, permissions=frozenset(),
    limit: int = 64, offset: int = 0,
) -> dict: ...   # {schema_version,total,returned,truncated,records,content_fingerprint,unverified_reasons}
```

判定规则（**CAP-O02 硬约束**）：
- `native_verified` **必须**同时满足：ToolRegistry 有真实 descriptor、`descriptor.executable`、
  `status` 非 PLANNED/EXTERNAL_UNAVAILABLE、`audit_tool_conformance(...).overall == "pass"`、
  registration+dispatch+result 三面非 fail/not_evaluated。**任一不满足即降级**。
- 只有 capability/algorithm 声明、无真实注册 tool → `declared_only`（历史通过不算数）。
- provider 缺席 / 扩展停用 / `EXTERNAL_UNAVAILABLE` → `temporarily_unavailable`。
- `permissions` 不足或 `owner` 不匹配 → `forbidden`（fail-closed，不泄露 capability 细节）。
- 缺证据 → `available_unverified` + 显式 `unverified_reasons`，**绝不猜**。
- 8 面 reason code **直接复用** `tool_capability_conformance.FaceVerdict.reason` 与
  `FALLBACK_CODE_XWALK`，不另造一套校验或理由码。

版本门：`to_dict()` 输出 `schema_version`；`AVAILABILITY_SCHEMA_VERSION` 提升时必须保留
旧字段（additive-only）。消费方按 `schema_version` 分支，旧版消费者忽略新字段。
Feature flag：`GIS_CAPABILITY_AVAILABILITY_V1`（默认 False，关闭时 `compile_availability_snapshot`
仍可离线调用，但 Pi/API 面不透出；失败一律返回 `not_evaluated`，不虚构可用性）。

CLI：`python -m app.lib.gis.capability_availability --json [--status ...] [--limit N] [--bootstrap]`

## B. CAP-03 `app.lib.gis.method_eligibility`

```python
ELIGIBILITY_VOCABULARY = ("eligible", "ineligible", "not_evaluated")
FALLBACK_CLASSES = ("equivalent", "approximation", "proxy", "degraded", "not_allowed")

@dataclass(frozen=True)
class MethodEligibility:
    algorithm_id: str; verdict: str
    reason_codes: tuple[str, ...]
    backend_candidates: tuple[str, ...]
    fallback_class: str
    transform_hint: str
    disclosures: tuple[str, ...]
    precondition_verdicts: tuple[tuple[str, str, bool], ...]  # (id, verdict, deferred)
    def to_dict(self) -> dict: ...

def preflight_method(
    algorithm_id: str, *, profile: Optional[dict] = None,
    space: Optional[dict] = None, params: Optional[dict] = None,
    allow_transform: bool = False,
) -> MethodEligibility: ...
```

量纲/CRS 硬规则（**CAP-O06**）：
- `space.distance_unit` ∈ {m, km} 与 `PARAM_UNIT_VOCABULARY` 的 `seconds` 混用
  → `ineligible` + `REASON_UNIT_DIMENSION_CONFLICT`（「路网时间」秒 ≠ 「距离」米）。
- `crs_class == "geographic"` 且操作要求线性距离/面积 → `ineligible` +
  `REASON_GEOGRAPHIC_LINEAR_DISTANCE`（`REQUIRES_TRANSFORM` 时给 `transform_hint`）。
- 任一 `PreconditionResult.deferred=True` → 不得判 `eligible`，降级 `not_evaluated`。
- `INVALID_METHOD` / `INSUFFICIENT_DATA` → `ineligible`。
- **CAP-O07**：`fallback_class` 直接取 `AlgorithmDescriptor.fallback_semantics` /
  `science_final_gate.NON_EQUIVALENT_SEMANTICS`；`proxy` 绝不返回 `equivalent`。

## C. CAP-02 `app.lib.gis.availability_rerank`

```python
@dataclass(frozen=True)
class RankedCandidate:
    tool: str; score: float; availability_status: str
    reasons: tuple[str, ...]
@dataclass(frozen=True)
class RerankResult:
    candidates: tuple[RankedCandidate, ...]
    suppressed: tuple[tuple[str, str], ...]   # (tool, reason)
    diagnostics: tuple[str, ...]
    bounded: bool
    def to_dict(self) -> dict: ...

def rerank_candidates(
    candidates,           # Iterable[(tool_name, score)] 或 dict
    snapshot, *, query: str = "", input_types=(), method_constraints=(),
    max_candidates: int = 8,
) -> RerankResult: ...

def validate_pi_call(tool_name: str, arguments: dict, *, snapshot, registry=None) -> tuple[bool, str]: ...
```

- 不可用工具（declared_only / temporarily_unavailable / forbidden）**硬过滤**，
  无论向量相似度多高；进 `suppressed` 并给理由（**CAP-O04/O05**）。
- `available_unverified` 降权但不激活为首选；`native_verified` 保序加权。
- 拒绝不在 registry 的 hallucinated 工具名、参数多余字段、pi-schema 宽松 coercion。
- 结果有界：`max_candidates` 硬上限；`bounded=True` 时披露截断。

黄金语料 `app/lib/gis/data/capability_lexicon_golden.json`：
`{"cases":[{"query":..., "expect_tool":..., "expect_status":..., "forbid_tools":[...], "note":...}]}`
覆盖中英：缓冲 / 空间连接 / 分区统计 / 路网时间 / 等时圈 / 密度 / 通视 / 插值 / 地理综合。
负例必须包含「距离秒数不等于米」与「停用扩展的高相似度工具」。

## D. CAP-04 `app.lib.gis.provider_eligibility`

```python
@dataclass(frozen=True)
class ProviderQualification:
    provider_id: str
    facets: dict[str, str]       # model_loaded/provider_healthy/compatible_input/inference_budget/permission
    qualified: bool
    gpu_fallback: str            # "not_needed" | "cpu_degraded" | "unavailable"
    output_measurability: dict[str, bool]  # shape/range/tile/confidence/uncertainty/provenance
    reason_codes: tuple[str, ...]
    def to_dict(self) -> dict: ...

def qualify_provider(provider_id: str, *, input_profile=None, budget=None,
                     permissions=frozenset(), host=None) -> ProviderQualification: ...
def extension_lifecycle_view(host) -> dict: ...  # 只读：activate/deactivate/upgrade/pin 状态
```
只读消费 `ProviderCapabilities` / `CompatibilityReport` / `DevicePlan` /
`ExtensionHost.get_record()` / `build_certification_index_from_host`；**不调用私有 worker**、
**不新引擎**。停用/升级后必须给出 stale 与 fail-closed 证据。

## E. M5 两个真实原生原语 `app.tools.capability_ecosystem_tools`

注册函数 `register_capability_ecosystem_tools(registry)`，挂进 `app/tools/__init__.py:_TOOL_MODULES`。

| 工具名 | 输入 | 输出 |
|---|---|---|
| `conditional_quantile_zonal_stats` | `zones`(面 FC) + `raster_path` + `quantiles` + `where`(条件表达式) + `nodata` | 每 zone 的多分位数 + 条件筛选后有效样本计数/覆盖率 + 科学方法披露 + 预算 + artifact ref |
| `network_service_coverage_consistency` | `network`(线 FC) + `facilities` + `cutoff` + `cost_unit` | 多方案服务覆盖一致性（Jaccard / 差异段）+ 严格量纲校验 + artifact ref |

两项必须：真实 `@tool` 注册、真实 `ToolRegistry.dispatch` 可达、`apply_contract` 参数严格校验、
输出 `data` + `artifact` + `science` + `budget`，**禁止 dummy/no-op**。
`cost_unit` 为 `seconds` 而请求 `distance` 时返回 typed 错误（CAP-O06 回归保护）。
参数契约经 `app/lib/gis/algorithms/capability_ecosystem.py` 域包 `PARAMETER_CONTRACTS` 导出。

## F. CAP-06 对拍 `app.lib.gis.capability_schema_parity`

```python
def parity_report(*, registry=None, snapshot=None, bootstrap=False) -> dict: ...
```
对拍工具名 / `required` 参数 / 量纲 / 摘要 在 ToolRegistry → schema → dispatch → slim payload
四处一致；`slim_tool_result` 结果摘要必须含关键统计数值或 `ref`，否则记 `SUMMARY_VALUE_LOSS`
诊断（**只读诊断，不修改 `llm_result_formatter.py`** —— 避免抢 Issue #2006）。

## G. 兼容与回滚

- 全部新模块 additive；不改 `_dispatch_impl` / `pi_native_surface.py` / `agent_pi_bridge.py` /
  `llm_result_formatter.py` / `families.py` / 数据 fabric / MapSpec 渲染链。
- 新 capability/algorithm 种子加入既有域包（不是新 registry）；新增工具会改变
  `registry_fingerprint` 与 catalog generation → 用既有生成器
  `python -m app.lib.gis.execution_catalog_docs` 重生成产物（`--check` 必须通过）。
- 回滚：feature flag 关闭 + 从 `_TOOL_MODULES` 移除一行即恢复旧目录，无 schema 破坏。
