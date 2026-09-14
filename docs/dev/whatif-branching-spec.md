# What-If Scenario Branching — 工程规格书（whatif-branching-spec）

- 依据: ADR-0193（`docs/adr/0193-whatif-scenario-branching.md`）
- 范围: `app/services/simulation/whatif/` + MapSpec `scenario_mode` 协议 + 前端映射
- 测试套件: `tests/unit/test_whatif_branching.py`（TDD，本文件 §6 即其验收矩阵）
- 分支: `agent/09-counterfactual-whatif-scenario-branching`

## 1. 模块布局与职责

```
app/services/simulation/
  __init__.py
  whatif/
    __init__.py            # 公共面 re-export + 防重复施工声明
    branch_manager.py      # ScenarioBranchManager：fork / 干预 / 回滚 / 注册表 / 释放
    spatial_diff_engine.py # 几何差分 + 指标差分（确定性纯函数）
    prescriptive_advisor.py# 确定性核（Pareto/ROI/优先级）+ 可选 LLM 叙述层
    comparison_report.py   # 结构化对比矩阵（JSON + markdown）
```

分工红线：
- 不重写 `app/tools/what_if_simulate.py`（单次评估工具，保持不变）；
- 不碰 `gis_situation/diff.py`（事实差分 vs 空间差分，词汇分离）；
- 不碰 `map_product_service.fork_version`（发布谱系 vs 会话推演分支）；
- LLM 只经 `chat/llm_client.call_llm`，失败显式降级，零静默降级。

## 2. branch_manager.py

### 2.1 数据契约（pydantic v2）

```python
BranchStatus = Literal["active", "archived", "discarded"]

class ScenarioBranchMeta(BaseModel):
    branch_id: str                      # [A-Za-z0-9_-]{1,32}
    title: str
    hypothesis: str = ""                # 反事实问句
    parent_session_id: str
    branch_session_id: str              # {parent}__wif_{branch_id}（纯函数派生）
    fork_revision: int                  # fork 时父会话 mutation revision
    forked_at: str                      # UTC ISO
    baseline_fingerprint: str           # 父 spec canonical SHA256
    status: BranchStatus = "active"
    intervention_count: int = 0
    last_revision: int = 0              # 分支会话自身 revision
    last_intervention_summary: str = ""
```

常量：`BRANCH_ID_PATTERN`、`MAX_ACTIVE_BRANCHES = 5`、
`REGISTRY_KEY = "_whatif_branches"`、`FORK_EVIDENCE_KEY = "_whatif_fork"`。

### 2.2 API（全部 async）

| 函数 | 语义 | 错误面 |
|---|---|---|
| `branch_session_id(parent_sid, branch_id) -> str` | 纯函数派生 | branch_id 非法 → ValueError |
| `ScenarioBranchManager.create_branch(parent_sid, branch_id, title=None, hypothesis="", engine=None) -> ScenarioBranchMeta` | 读父权威 spec → canonical 指纹 → materialize 到派生会话（save_mapspec revision=1）→ 写 fork 存证 + 注册表（父锁内） | 父无 spec → `BranchError(code="baseline_missing")`；branch_id 重复/非法、活跃超限 → BranchError |
| `apply_intervention(parent_sid, branch_id, intents, *, actor="whatif", reason=None) -> list[MapSpecResult]` | 逐 intent `apply_gis_mutation(branch_sid, origin="agent", actor=...)`；更新注册表计数/摘要 | 分支不存在 → BranchError(code="branch_not_found")；单条 intent 失败**不回滚已成功项**（逐条披露， MapSpecResult.is_error） |
| `rollback_branch(parent_sid, branch_id, checkpoint_id=None) -> MapSpecResult` | 对分支会话发 `RollbackIntent`；checkpoint_id=None 时扫描分支 `checkpoints/` 目录取 mtime 最近者（auto checkpoint 语义） | 分支不存在 → BranchError；无任何 checkpoint → BranchError("checkpoint_missing") |
| `get_branch_world_state(parent_sid, branch_id) -> dict` | `build_world_state(branch_sid)` + 分支元信息头 | 分支不存在 → BranchError |
| `list_branches(parent_sid) -> list[ScenarioBranchMeta]` | 注册表快照（只读） | 无注册表 → [] |
| `delete_branch(parent_sid, branch_id) -> bool` | 注册表标记 discarded → `session_data_manager.clear_session(branch_sid)`（联动磁盘 purge）→ 注册表移除 | 幂等；不存在返回 False |
| `compare_branches(parent_sid, branch_ids, *, optimization_goals=None) -> ScenarioComparison` | 逐分支 diff → 汇聚矩阵 → advisor。**对比口径**：diff 的 Baseline = 比较时刻父会话**当前**权威 spec（用户对比的是现状，非 fork 历史快照）；`baseline_revision` 记录锚点、`baseline_fingerprint` 为该时刻父 spec 指纹（注册表不携带 spec 载荷，遵守 Zero Big Data in Context） | 空 branch_ids → BranchError；父无 spec → BranchError("baseline_missing") |

### 2.3 Fork 语义细节

- 父 spec 读取：`mapspec_store_instance.get_mapspec(parent_sid)`（含磁盘兜底）。
- 指纹：`json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",",":"))`
  → SHA256（与 store `_fingerprint_sync` 同口径）。
- materialize：`store.save_mapspec(branch_sid, deep_copy(spec), mutation_revision=1)`。
  直接写 store 是有意的：fork 是快照物化。存证 `_whatif_fork = {fork_revision,
  baseline_fingerprint, forked_at, parent_session_id, branch_id}` 写分支 map_state。
- 注册表写入持父会话锁；fork 幂等键：同 (parent, branch_id) 重复 create →
  BranchError(code="branch_exists")。

## 3. spatial_diff_engine.py

### 3.1 几何差分（纯函数）

```python
@dataclass
class GeometryDiff:
    layer_id: str
    added_features: list[dict]        # Feature dict（含 diff 标注）
    removed_features: list[dict]
    modified_features: list[dict]     # (before, after) 配对
    unchanged_count: int
    added_area_m2: float
    removed_area_m2: float
    added_length_m: float
    removed_length_m: float
```

- 配对键：`properties.id` → 缺失时 `type|wkt 规范化几何` 哈希。
- 几何度量用 shapely；4326 下面积/长度用 Web Mercator 近似系数
  `cos(lat0)` 校正并在 `meta.projection_note` 披露。
- 覆盖差分：`coverage_diff(baseline_fc, branch_fc, service_radius_m)` →
  `{"gained_area_m2", "lost_area_m2", "gained_features", "lost_features"}`
  （union 缓冲差集，dissolve 后计算）。

### 3.2 指标差分（确定性代理模型 proxy:v1）

`compute_metric_deltas(baseline_layers, branch_layers) -> list[MetricDeltaV2]`：

| metric_key | 方向 | 模型 | 输入缺失行为 |
|---|---|---|---|
| `facility_count` | — | 点图层要素计数差 | None + gap note |
| `green_area_m2` | maximize | 属性 `category`/`landuse` 标记绿地面的面积和差 | None |
| `road_length_m` | — | LineString 长度和差 | None |
| `road_capacity_index` | minimize 拥堵方向 | 新增道路长度 × `LANE_CAPACITY_PROXY`（120 veh/h/m proxy）归一 | None |
| `service_coverage_population` | maximize | 人口图层（`properties.population`）∩ 设施缓冲（`service_radius_m` 默认 800m）覆盖人口差 | None |

- GIS-03 纪律：任何一侧图层缺失/无人口字段 → `baseline/simulated/delta_*` 全
  None + `missing_baseline=True` + `evidence_gap_note`（中文，指名缺什么）。
- `delta_pct = (after - before)/before * 100`（before=0 → None，防除零）。
- `meta = {"model": "proxy:v1", "projection_note": ...}`。

### 3.3 Diff 对比图层

`build_diff_overlay(diff_results) -> FeatureCollection`：变更要素拷贝 +
`diff_kind` + `impact_sign`（由指标方向函数 `impact_sign_for(metric_key, delta)`
判定：maximize 指标增 → positive；minimize 指标增 → negative；几何无归属 → neutral）
+ 建议渲染色 `render_hint`（positive→"#22c55e"，negative→"#ef4444"，neutral→"#9ca3af"）。

## 4. prescriptive_advisor.py

```python
class PrescriptiveAdvice(BaseModel):
    mode: Literal["llm", "deterministic"]
    degraded_reason: Optional[str]      # llm_unavailable / llm_output_invalid / llm_error
    recommended_branch_id: Optional[str]
    confidence: float
    rationale_causal_chain: list[str]   # 因果链（A 增加通行能力 → B 降低阻抗 → …）
    roi_sensitivity: list[dict]         # {branch_id, metric_key, delta_pct, cost_proxy, roi_index}
    implementation_priority: list[dict] # {branch_id, action, priority, rationale}
    narrative: str                      # 中文叙述（LLM 或模板）
```

- 确定性核：目标方向表（maximize/minimize，与 comparison_engine 词汇一致）→
  缺基线指标剔除 → 归一化得分 → Pareto 支配 → 推荐 = 最高综合分（并列时取
  branch_id 字典序，保证确定性）；ROI 敏感度 = delta_pct / cost_proxy
  （cost_proxy = 新增要素几何量代理）。
- LLM 层：`llm_available()` 守卫（`settings.LLM_API_KEY` 占位符检测）→
  prompt 内嵌 JSON schema → fence 剥离 → pydantic 校验 → 任何失败
  `mode="deterministic"` + `degraded_reason`，绝不 raise。
- 确定性模板叙述：推荐结论 + 各方案一句话画像 + 缺口披露（哪些指标未参与）。

## 5. comparison_report.py

```python
def build_comparison_report(comparison: ScenarioComparison) -> dict
def render_comparison_markdown(report: dict) -> str
```

- JSON 面：`{report_id, generated_at, baseline_fingerprint, branches[],
  metric_matrix: {metric_key: {branch_id|baseline: value|None}},
  geometry_summary, recommendation, gaps[]}`。
- markdown 面（结构化对比专报）：
  1. `# What-If 多方案对比专报` + 假设问句清单；
  2. 效益对比矩阵表：`| 指标 | Baseline | 方案A | 方案B | A Δ% | B Δ% |`
     （None → `—`，正改善标 `▲`、恶化标 `▼`）；
  3. 几何差分摘要（每分支 added/removed/modified 计数 + 覆盖增失面积）；
  4. 处方性建言（推荐方案、因果链、ROI 敏感度表、实施优先级表）；
  5. 缺口与假设披露（evidence_gap_note 汇总）。
- 自洽要求：矩阵中的每个数值必须可由 metric_matrix 复算（渲染层不做二次计算，
  只做格式化）。

## 6. 验收矩阵（= 测试计划）

| # | 测试主题 | 断言要点 |
|---|---|---|
| T1 | 分支分叉安全 | fork A、B 后：两分支 spec 与 baseline 指纹一致；父会话 revision/spec 不变；注册表 2 条 active |
| T2 | 分支修改隔离 | A 上 Upsert 新层后：`build_world_state(branch_a)` 含新层，`build_world_state(branch_b)` 与 baseline 均无；A/B revision 独立递增 |
| T3 | 分支回滚隔离 | A 先干预 1（生成 checkpoint）→ 干预 2 → 回滚到干预 1 后：A 世界状态=干预 1 态；B 与 baseline 世界状态纹丝不动 |
| T4 | 注册表纪律 | 非法 branch_id / 重复 fork / 超 MAX_ACTIVE_BRANCHES → BranchError 且父状态不变 |
| T5 | 删除释放 | delete_branch 后：`get_map_state(branch_sid)` 为空、磁盘目录消失、注册表无该分支；幂等重复删除返回 False |
| T6 | 几何差分 | 构造已知几何：added/removed/modified 计数、面积/长度增量与手算值一致（1e-6 相对容差） |
| T7 | 覆盖差分 | 设施点 + 人口格网：gained/lost 面积与覆盖人口增量精确断言 |
| T8 | 指标差分 | delta_pct 精确值断言（如通畅率代理 +14.2% 类场景手算）；缺输入指标全 None + gap note（GIS-03） |
| T9 | 对比矩阵 | metric_matrix 与各分支 MetricDelta 一致；None 传播；渲染「—」 |
| T10 | 专报生成 | markdown 含矩阵表/处方建言/缺口披露三段；数值自洽（从 JSON 复算比对） |
| T11 | Advisor 确定性 | Pareto 支配/推荐/优先级在固定输入下稳定（两次调用同一输出）；劣方案不被推荐 |
| T12 | LLM 降级 | 无 API key → mode=deterministic + degraded_reason=llm_unavailable，绝不抛异常 |
| T13 | scenario_mode 契约 | schema 校验（合法值/非法值）、1.2→1.3 identity 迁移、旧版本披露策略不变 |
| T14 | SetScenarioModeIntent | 合法写入 spec 顶层；非法值 is_error 且 last-known-good 不变；None 清除退出推演 |
| T15 | 前端映射纯函数 | split_view→side-by-side、swipe_compare→swipe、None→None（vitest） |

## 7. 非目标（v1 明确不做）

- 栅格差分（raster_change 已存在于 SpatialAnalyzer，接入留给 v2）；
- 真实交通分配（四阶段/agent-based）；阻抗差异只做代理披露；
- 分支合并（merge）语义 —— v1 分支是评估面，采纳 = 人工按方案重放干预；
- MapProduct 发布谱系联动。
