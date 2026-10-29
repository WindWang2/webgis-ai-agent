# INTERFACE_CONTRACTS — 冻结接口（M1 实现前定稿）

## 1. DesignTaskContext（投影契约：结构化事实，禁止 query 字符串）

```python
class DenominatorEvidence(BaseModel):
    field: str = ""            # 分母字段名（空 = 无）
    available: bool = False
    unit: str = ""             # 人口/面积等单位语义
    source: str = "profile"    # profile|intent|dataset_semantics

class DesignTaskContext(BaseModel):
    task_categories: Tuple[str, ...] = ()
    geometry_kind: str = "polygon"          # point/line/polygon/raster
    variable_kind: str = "continuous"       # none/categorical/ordinal/continuous/rate
    statistic: str = ""                     # count/sum/rate/index/density
    artifact_types: Tuple[str, ...] = ()
    point_count: int = 0                    # 在场点要素数（R2/R3 证据）
    point_count_basis: str = "unknown"      # profile|dataset|sampled —— 证据来源
    denominator: DenominatorEvidence = DenominatorEvidence()
    temporal_periods: int = 0               # ≥2 触发 R4
    user_requested_models: Tuple[str, ...] = ()   # 显式表达要求（heatmap 等）
    scale_hint: str = ""
    output_target: str = "interactive"
    measurement: str = ""                   # MeasurementKind 投影
```

## 2. MapDesignCandidate / DesignCandidateSet

```python
class DenominatorCondition(str, Enum):
    REQUIRED_SATISFIED; REQUIRED_MISSING; NOT_APPLICABLE

class CandidateSuitability(str, Enum):
    SUITABLE; CONDITIONAL; BLOCKED

class MapDesignCandidate(BaseModel):
    candidate_id: str            # f"{map_model_id}@{n}" 确定性
    map_model_id: str
    expression_family: str       # point/aggregation/density_surface/area_stat/change/linkage
    composition_template_id: str
    score: float
    suitability: CandidateSuitability
    condition_codes: List[str]   # typed：DENOMINATOR_MISSING/POINT_OVERPLOT_RISK/...
    condition_reasons_zh: List[str]
    data_requirements: List[str] # 来自 MapModel.accepted_artifact_types
    denominator_condition: DenominatorCondition
    classification: Optional[Dict]  # {method,k,authority,source}（R6，可缺省）
    palette_semantics: str       # MapModel.color_scheme_kind
    components: List[str]        # 组合槽位组件类型
    scientific_risks_zh: List[str]
    degradation_path: List[str]  # fallback_model_id 链
    reasons_zh: List[str]        # 为什么合适
    against_zh: List[str]        # 对照：为什么优于落选者（≤3）

class DesignCandidateSet(BaseModel):
    version: int = 1
    candidates: List[MapDesignCandidate]     # 2–4（不足 2 时如实 1，不注水）
    selected_candidate_id: str = ""
    selection_basis: str = ""                # planner 实际产物对齐说明
    user_request_handling: Dict              # {requested,honored,typed_blocked}
```

## 3. DesignDecisionTrace（bounded dict，≤4KB 序列化）

键：version/intent_summary/data_facts(≤8 条)/candidates(候选有界投影)/selected/classification/composition_template_id/mapspec_fingerprint/user_request_handling/scientific_receipts(≤6)/generated_at 不含（确定性——不含时间戳）。

## 4. 语义检查新码（M4 契约面）

- `CHART_MAP_FIELD_COHERENCE`：maplevel；severity error；not_evaluated 当无 chart 组件或无主题层。
- `VIEWPORT_COVERS_RESULTS`：maplevel；severity error；not_evaluated 当无 view bbox 或无结果层 bounds 证据。
- `DENSITY_SEMANTICS_HONESTY`：layer；severity warning（计数误标）/error（KDE 无披露声称人口密度）；not_evaluated 当无密度语义线索。

## 5. 工具面

- `webgis_map_product` 结果 payload 增键：`design_candidates`（DesignCandidateSet.to_bounded_dict()）、`design_decision_trace`（bounded）。既有键零变化。
- 新工具 `webgis_map_design_select(map_model_id, session context)`：幂等；返回 {pinned, previously_selected, rejected_reasons_carried}。
- pin 存储：复用 representation user-wins pin 面（implementation 决定具体 seam，验收只看行为：下一轮 map_product 尊重 pin + quality_loop 不覆盖）。

## 6. 冻结纪律

- 所有新载荷 to_bounded_dict() 有界化（对齐 CompositionCandidate.to_bounded_dict 惯例）。
- 确定性：同输入两次 model_dump 相等（golden 可锁）。
- 中英文：reasons 双语字段名后缀 _zh 起步，前端 i18n messages 补 en；检查码消息走既有 i18n 面。
