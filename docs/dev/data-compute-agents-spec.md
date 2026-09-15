# 规格：Data Scout & GeoCompute 专精子智能体（agent-swarm/04）

- ADR：`docs/adr/0188-specialist-agents-data-compute.md`
- 分支：`agent/04-specialist-agents-pack-data-compute`
- 测试：`tests/unit/test_specialist_data_compute.py`（TDD，先红后绿）

## 1. 模块布局

```
app/services/agent_swarm/
├── __init__.py     # 公共导出（ Specialists / contracts / registry ）
├── contracts.py    # SpatialProfileRef、DataScoutReport、ComputeSubmission、8KB 常量
├── base.py         # BaseSpecialistAgent + SpecialistToolDeniedError/SpecialistTimeoutError
├── data_scout.py   # DataScoutAgent（数据猎手）
├── geocompute.py   # GeoComputeAgent（空间计算专家）
└── registry.py     # SPECIALIST_REGISTRY + ensure_subagent_roles_registered()
```

## 2. 契约（contracts.py）

### 2.1 常量

| 名 | 值 | 含义 |
|---|---|---|
| `SERIALIZATION_BUDGET_BYTES` | `8 * 1024` | SpatialProfileRef 序列化硬上限 |
| `AUTO_UTM_AREA_KM2_THRESHOLD` | `5000` | bbox 面积超过 → 注入 UTM 防御节点 |
| `AUTO_UTM_ROWS_THRESHOLD` | `200_000` | 估算行数超过 → 注入 UTM 防御节点 |

### 2.2 `SpatialProfileRef`（frozen，pydantic v1 风格 BaseModel）

GeoCompute 的**唯一**结果通货（Zero Big Data in Context）：

```python
class SpatialProfileRef(BaseModel):
    version: str = "1.0"                # 契约版本（冻结）
    ref_id: Optional[str]               # session ref 提货券（worker 落存后回填）
    plan_id: str                        # ExecutionPlan.plan_id
    plan_digest: str                    # 语义指纹摘要（sha1 前 16 位）
    status: Literal["submitted","running","completed","failed","degraded"]
    rows: Optional[int]                 # 结果行数（未知=None，诚实）
    crs: Optional[str]                  # 输出 CRS（如 EPSG:32650）
    crs_defense: Optional[str]          # "auto_utm" | None
    volume_estimate: VolumeEstimate     # rows/bytes/area_km2（可 None 字段）
    job_id: Optional[int]               # durable job id
    celery_task_id: Optional[str]
    duration_ms: Optional[float]
    summary: str = ""                   # ≤600 字符有界摘要
    notes: Tuple[str, ...] = ()         # ≤8 条、每条 ≤200 字符
    truncated: bool = False             # metadata 被截断的诚实标注
    metadata: Dict[str, Any] = {}       # 有界元数据（截断牺牲品）
```

- `to_json_bytes()` → UTF-8 JSON 字节；`GeoComputeAgent.build_result_ref()`
  保证 `len(bytes) < SERIALIZATION_BUDGET_BYTES`：先截 `metadata`
  （保留键名短者），仍超 → 抛 `SpatialProfileTooLargeError`。
- `summary`/`notes` 写入时即截断（字段级有界），不参与 8KB 兜底。

### 2.3 `DataScoutReport`

```python
class DataScoutReport(BaseModel):
    ok: bool
    descriptor: Optional[D1DatasetDescriptor]   # 主产出（复用 ADS D1）
    source_used: Optional[str]
    decisions: List[FallbackDecision]           # D3 降级链留痕
    fact: Optional[AcquisitionFact]             # D4 观测
    error: Optional[str]                        # 诚实失败原因
    aligned_fields: Dict[str, str] = {}         # 异构字段 → 标准字段映射
    crs_alignment: Optional[str] = None         # 对齐说明（如 gcj02_offset 声明）
```

### 2.4 `ComputeSubmission`

```python
class ComputeSubmission(BaseModel):
    plan_id: str
    node_count: int
    job_id: Optional[int]
    celery_task_id: Optional[str]
    ref: SpatialProfileRef
```

## 3. 基类（base.py）

```python
class SpecialistToolDeniedError(PermissionError): ...   # 越权拦截（typed）
class SpecialistTimeoutError(TimeoutError): ...          # 墙钟熔断（typed）

class BaseSpecialistAgent(ABC):
    name: ClassVar[str]
    role_name: ClassVar[str]                # SUBAGENT_ROLES 键
    TOOL_ALLOWLIST: ClassVar[FrozenSet[str]]
    SPECIALIST_PROMPT: ClassVar[str]        # 专属系统提示词边界

    def __init__(self, *, registry=None, clock=time.monotonic,
                 deadline_s=None, logger=None)
    # 构造期 fail-closed：TOOL_ALLOWLIST ∩ registry 存在名 ≠ 白名单 → ValueError
    def authorize_tool(tool_name) -> None               # 越权 raise SpecialistToolDeniedError
    def guarded_registry() -> AllowlistDispatchRegistry # dispatch 边界包装
    def heartbeat(stage: str) -> float                  # 记录 + log；返回 age
    def heartbeat_age_s() -> float
    def check_deadline() -> None                        # 超时 raise SpecialistTimeoutError
    def delegate(task, dispatcher) -> SubagentResult    # 生产 LLM 委派（role=…）
```

## 4. DataScoutAgent（data_scout.py）

```python
class DataScoutAgent(BaseSpecialistAgent):
    name = "data_scout"; role_name = "data_scout"

    def __init__(self, *, source_registry=None,       # 默认 source_registry_service
                 chain_executor=None,                 # 默认 fallback.execute_fallback_chain
                 adapter_factory=None,                # 协议 → 适配器（可注入桩）
                 **base_kwargs)

    def scout(dataset_key, *, bbox=None, limit=…, require_crs=None) -> DataScoutReport
    def probe(dataset_key) -> DataScoutReport             # 仅元数据，不取要素
```

行为规格：

1. `source_registry.get(dataset_key)` → `SourceDefinition`（未注册 →
   诚实失败 report，不抛出）；
2. `fallback.resolve_chain(dataset_key)` 解析声明式回退链；
3. `chain_executor(primary, runner, chain=…)`：runner 经
   `adapter_factory(definition).preview(dataset, limit)` 轻量探测
   （LocalFileAdapter / StatsApiAdapter 内置支持；未知协议 → typed
   失败 → 计入下一跳）；
4. 熔断 OPEN 源由 `execute_fallback_chain` 自动跳过（circuit_open）；
5. 成功 → `D1DatasetDescriptor.from_fabric_descriptor` 或直接构造：
   只填已探测事实（preview 行数 / 声明 CRS / bbox），quality_signals
   透传 `verified`；`decisions` 非空 → `fact.degraded=True`；
6. 实体对齐提示词（`SPECIALIST_PROMPT`）声明字段映射与 GCJ-02 纠偏
   纪律；`aligned_fields` 由探测到的 `schema_fields` 与标准字段词典
   （name/poi/admin/pop…）映射产出，无证据不猜测。

## 5. GeoComputeAgent（geocompute.py）

```python
class GeoComputeAgent(BaseSpecialistAgent):
    name = "geocompute"; role_name = "geocompute"

    def __init__(self, *, submitter=None,             # 默认 jobs.submit_durable_job
                 **base_kwargs)

    def estimate_volume(descriptor|bbox, rows_hint=None) -> VolumeEstimate
    def infer_utm_crs(bbox) -> Optional[str]          # 静态纯函数也可独立调用
    def plan_computation(request: ComputeRequest) -> ComputeSubmission
    def build_result_ref(...) -> SpatialProfileRef    # 8KB 硬闸
```

`ComputeRequest`：`dataset_ref / operation / operation_params /
bbox / rows_hint / session_id / deadline_s`。

行为规格：

1. **估算**：`estimate_volume()` 用 cost_hint.rows → bbox 面积密度
   → 行数启发式三级降级（全部未知 → rows=None 诚实）；
2. **UTM 防御**：面积 > `AUTO_UTM_AREA_KM2_THRESHOLD` 或估算行数 >
   `AUTO_UTM_ROWS_THRESHOLD` → 计划头部插入
   `reproject` 节点（`infer_utm_crs(bbox)`：
   `EPSG:32{6|7}{zone}` 南北半球判定，zone=`floor((lon+180)/6)+1`；
   无 bbox → None，跳过并在 ref.notes 披露）；
3. **任务图**：`ExecutionNode(category=…, policy=DURABLE_JOB, …)` 序列 +
   `ResourceBudget(max_nodes≤…)`；`validate_plan()` 必须通过
   （内部 `build_plan_from_json` 同规校验）；CRS 期望写入节点
   `CrsExpectation`；
4. **Celery First 提交**：尾节点经 `submit_durable_job`（task=
   `geocompute.tasks.run_geocompute_node`，queue=`geocompute`）派发，
   `input_refs` 交接上游 ref；编排器进程内零重算；
5. **提货券**：`build_result_ref()` 产出 `SpatialProfileRef`，8KB
   闸门按 §2.2。

## 6. 注册表（registry.py）

```python
SPECIALIST_REGISTRY: Dict[str, Callable[[], BaseSpecialistAgent]]
def get_specialist(name) -> BaseSpecialistAgent   # 未知 → ValueError
def ensure_subagent_roles_registered() -> None    # 幂等注入 SUBAGENT_ROLES + 重跑注册表自检
```

角色定义（要点；全文见 registry.py）：

| 角色 | 域 | 预算 | 突变 | expected_outputs | failure_behavior |
|---|---|---|---|---|---|
| `data_scout` | dataset/chinese/osm | rounds 8 / tools 24 / heavy 2 / wall 240s | 禁 | d1_dataset_descriptor、fallback_decisions、source_health_notes | degrade_with_disclosure |
| `geocompute` | statistics/raster/network/temporal/dataset | rounds 10 / tools 24 / heavy 6 / wall 300s | 禁 | spatial_profile_ref、plan_digest、volume_estimate | fail_closed |

## 7. RBAC 白名单

- **data_scout.TOOL_ALLOWLIST**（ADS/Fetch 面，19）：
  `connect_data_source, inspect_data_source, list_datasets,
  search_datasets, search_spatial_catalog, describe_dataset,
  profile_dataset, query_dataset, query_federated_chain,
  query_federated_data, plan_data_query, materialize_dataset,
  refresh_data_source, query_local_osm, query_osm_boundary,
  query_osm_buildings, query_osm_poi, query_osm_roads,
  search_and_extract_poi`
- **geocompute.TOOL_ALLOWLIST**（计算分析面，24）：
  `validate_execution_plan, execute_execution_plan, get_execution_run,
  cancel_execution_run, buffer_analysis, multi_ring_buffer,
  overlay_analysis, spatial_join, clip_layer, dissolve_layer,
  convex_hull, h3_binning, h3_lisa, zonal_stats, hotspot_analysis,
  emerging_hotspot_analysis, kriging_interpolation,
  network_shortest_path, network_service_area, network_od_matrix,
  network_centrality, network_accessibility, nearest_facility,
  reproject_coordinates`
- **双向禁区**（类目级断言）：data_scout × `execute_execution_plan`/
  任何 `geocompute` 算子 → 拦截；geocompute × `connect_data_source`/
  制图工具（`apply_layer_style`、`create_thematic_map`、`webgis_*`）→ 拦截。

## 8. 测试矩阵（TDD 验收）

| # | 用例 | 断言要点 |
|---|---|---|
| T1 | data_scout 主源成功 | descriptor 填充、decisions 空、fact.success |
| T2 | 主源熔断 OPEN → 回退链 | circuit_open 决策 + 换源成功 + degraded 披露 |
| T3 | 全链失败 | ok=False 诚实失败，不虚构 descriptor |
| T4 | 白名单存在性校验 | 未知工具名 → 构造 ValueError |
| T5 | data_scout 越权调计算算子 | `SpecialistToolDeniedError`（execute_execution_plan / buffer_analysis） |
| T6 | geocompute 越权调制图/接数 | 拦截 connect_data_source / apply_layer_style |
| T7 | dispatch 包装面拦截 | guarded_registry.dispatch → TOOL_NOT_ALLOWLISTED |
| T8 | estimate_volume 三级降级 | cost_hint → bbox 密度 → None（诚实） |
| T9 | UTM 防御注入 | 大 bbox → 首节点 reproject、EPSG:326xx 正确；小 bbox 不注入 |
| T10 | 任务图合法 | validate_plan 通过；policy=durable_job |
| T11 | Celery 提交（stub submitter） | 提交参数含 input_refs/queue；编排器无内联执行 |
| T12 | ref_id 提货券合法性 | 形如 session ref、与 job 关联 |
| T13 | 8KB 硬闸 | 大 metadata → 截断 truncated=True；不可截 → SpatialProfileTooLargeError |
| T14 | 心跳/超时熔断 | heartbeat_age_s 推进；deadline 超时 → SpecialistTimeoutError |
| T15 | 注册表完备 | 两角色注册、spawn 描述渲染、validate_subagent_role_registry 通过 |
| T16 | delegate 委派路径 | dispatcher.run 收到 role= 与专属 prompt 任务头 |

## 9. 验收命令

```bash
./.venv/Scripts/python -m pytest tests/unit/test_specialist_data_compute.py -v
ruff check app/services/agent_swarm/ tests/unit/test_specialist_data_compute.py
```
