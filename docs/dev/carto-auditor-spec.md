# 规格：制图专家 Cartographer 与独立审计裁判 CriticAuditor（agent-swarm/05）

- 状态：与 ADR-0189 同步评审
- 分支：`agent/05-specialist-agents-pack-carto-auditor`
- 关联 ADR：0187（总控编排）、0188（专家包基座）、0183（Goal
  Satisfaction）、0152（symbology v2）、0118（AUTO_SAFE 修复）

## 1. 目标与反目标

**目标**：两名互为对抗制衡的专业子智能体——

- `CartographerAgent`：挂载 164 配方（RecipeRegistry）与视觉变量 /
  版面整饰 / 多尺度表达式三类制图知识库，对带多字段属性的 GeoJSON
  裁决最优分类方法（Jenks 等）与色彩梯度，自动编排规范化 MapSpec；
- `CriticAuditorAgent`：只读挂接 GoalSatisfactionEvaluator 与确定性
  语义检查，以一票否决权对交付产物出具《交付质量审计单》；
- `CartoAuditDuoSession`：两者的有界对抗博弈闭环（修正 ≤2 轮）。

**反目标**（ADR-0189 D7）：不造第二 evaluator/verdict；不改
quality_loop/semantic_checks/goal_satisfaction 语义；不做 LLM 审计。

## 2. 模块布局

```
app/services/agent_swarm/
├── specialists/                  # 05 起新专家入包（04 两专家原地保留）
│   ├── __init__.py
│   ├── cartographer.py           # CartographerAgent
│   ├── auditor.py                # CriticAuditorAgent
│   └── ledger.py                 # ArtifactLedger（进程内 ref→payload 有界账本）
├── duo_session.py                # CartoAuditDuoSession + InProcessSpecialistRuntime
├── contracts.py                  # 追加 MapSpecDeliveryRef / DeliveryAuditReport
└── registry.py                   # 追加 cartography_specialist / audit_judge 角色档与注册
tests/unit/test_specialist_carto_auditor.py   # TDD 套件（§8 矩阵）
```

## 3. CartographerAgent

### 3.1 类签名

```python
class CartographerAgent(BaseSpecialistAgent):
    name = "cartographer"
    role_name = "cartography_specialist"
    TOOL_ALLOWLIST = frozenset({
        "create_thematic_map", "apply_layer_style", "webgis_layout_set",
        "export_thematic_map", "webgis_compile_maplibre", "combine_map_theme",
    })

    def __init__(self, *, recipe_registry=None, ledger=None, **base_kwargs): ...
    def compose(self, request: dict) -> MapSpecDeliveryRef
    def revise(self, delivery: MapSpecDeliveryRef, audit_report) -> MapSpecDeliveryRef
```

`request`（有界输入，全部必填校验，fail-loud）：

| 键 | 类型 | 语义 |
|----|------|------|
| `geojson` | dict | FeatureCollection（inlineData 同形） |
| `field` | str | 专题数值/类别字段名 |
| `title` | str | 图名（title 组件文本） |
| `purpose` | str | `screen_16_9` 等词表，未知按 screen_16_9 降级+披露 |
| `recipe_hint` | str | 可选，RecipeRegistry 配方 id/关键词 |
| `requested_method` / `requested_k` / `requested_palette` | 可选 | 显式制图决策（尊重并披露 source=explicit） |
| `source_id` | str | 可选，缺省 `"delivery"` |

### 3.2 compose 流水线（全程确定性，逐相位 heartbeat + check_deadline）

1. **字段勘察**：提取 `field` 的有限值 → `distribution_stats_from_values`
   ；类别型（非有限值占比高）走 categorical 分支；
2. **配方先验**：`recipe_hint` 存在 → `RecipeRegistry.get/keyword_hits`
   ；取 `primary_cartography`/`default_components`/推荐分类器作先验，
   仅排序不覆盖分布证据；
3. **分类与色彩裁决**：`choose_classification(stats, recommended=…)`
   → `symbology_decision_from_values`（method×k×palette×clip_policy，
   CIEDE2000 相邻类可分辨 + CVD/print 约束）；
4. **图例与 paint**：`build_graduated_spec`（数值）/ `build_categorical_spec`
   （类别）→ `spec_to_paint`（单一投影点，图例↔paint 不漂移）；
5. **版面整饰**：`required_components_for(purpose, content)` 必配基线
   （title/scale_bar/north_arrow/attribution[/legend]）→
   `LayoutParticipant` 列表 → `solve_layout_v4`（防撞自愈，轨迹披露）；
6. **规范化**：组装 MapSpec（version/view/sources/layers/layout）→
   `canonicalize_mapspec`（schema 校验，fail-loud）；
7. **出券**：`cartographic_fingerprint` → `MapSpecDeliveryRef`，载荷入
   `ArtifactLedger`。

### 3.3 revise（对抗回路修复面）

只接受审计单 `vetoes[].suggested_fix` / `improvement_notes` 驱动的
修复（AUTO_SAFE 优先），复用 `review_and_repair_cartography` 的修复
执行器语义；无 veto 驱动的改动一律拒绝（防"修图顺带夹带"）。产出新
fingerprint 的 delivery，`revision` 递增。

## 4. CriticAuditorAgent

### 4.1 类签名

```python
class CriticAuditorAgent(BaseSpecialistAgent):
    name = "critic_auditor"
    role_name = "audit_judge"
    TOOL_ALLOWLIST = frozenset({"audit_spatial_quality", "gis_skill_replay_check"})

    def __init__(self, **base_kwargs): ...
    def audit(self, delivery: MapSpecDeliveryRef, *, chapter=None,
              ledger=None, source_profiles=None, map_product=None,
              user_goal="") -> DeliveryAuditReport
```

### 4.2 audit 只读调用链

1. 取载荷（ledger.get(ref_id)，缺席 → fail-closed veto：审计对象不可达）；
2. `review_cartography(mapspec, source_profiles)` → review 证据（只读，
   不跑 repair）；
3. `resolve_goal_contract(chapter)` + `evaluate_goal_satisfaction(...)`
   → goal 面（chapter 缺席 → 降级为仅制图审 + 披露，goal_score=None
   不虚构）；
4. 四红线 veto 判定（ADR-0189 D2 表）；
5. `goal_score = counts.fulfilled / len(required_ids)`（仅当契约面
   存在；带 `goal_score_derivation` 披露）；
6. 出具 `DeliveryAuditReport`（bounded）。

**只读不变量**：audit 前后 delivery 载荷与 chapter 深度相等（测试
锁定）；结论 advisory，不改写任何上游权威状态。

## 5. 输出契约（agent_swarm.contracts 追加）

```python
class MapSpecDeliveryRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "1.0"
    ref_id: Optional[str] = None          # "ref:mapspec-*"
    capability: Literal["thematic_map"] = "thematic_map"
    mapspec_fingerprint: str = ""
    digest: str = ""                      # 载荷 sha256（12 位前缀）
    revision: int = 0
    layer_count: int = 0
    legend_visible: Optional[bool] = None
    classification: dict = {}             # {field, method, k, palette}
    components: List[str] = []            # 组件类型词表（≤12）
    warnings: List[str] = []              # ≤8 × ≤200
    summary: str = ""                     # ≤600
    truncated: bool = False

class DeliveryAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "1.0"
    audited_ref_id: str = ""
    audited_fingerprint: str = ""
    round_index: int = 0
    verdict: Literal["pass", "fail", "not_evaluated"]
    goal_score: Optional[float] = None
    goal_score_derivation: str = ""       # 必填披露（score 非 None 时）
    success_threshold: Optional[float] = None
    uncovered_requirements: List[str] = []   # ≤12
    cartography_risks: List[str] = []        # 规则码 ≤12
    vetoes: List[dict] = []                  # 因果证据链 ≤8
    improvement_notes: List[str] = []        # ≤8 × ≤240
    review_status: str = ""                  # quality_loop 词表
```

## 6. CartoAuditDuoSession

```python
class CartoAuditDuoSession:
    MAX_REPAIR_ROUNDS = 2
    def __init__(self, *, cartographer, auditor, ledger=None,
                 governor=None, aggregator=None, clock=time.monotonic,
                 max_repair_rounds=2): ...
    async def run(self, request: dict, *, chapter=None,
                  run_id=None) -> DuoSessionResult
```

- 每轮 compose/revise→audit 都经 `SwarmConcurrencyGovernor.acquire`
  （集群并发 ≤3 同一纪律）并产出 `SubagentReceipt`（经
  `normalize_receipt` 归一）；
- 收敛：`report.verdict == "pass"`；超限：`verdict="failed_escalated"`
  + 全程证据；
- `DuoSessionResult`：`status / final_delivery / final_report /
  receipts / rounds_used / rounds：List[{round, delivery_ref, report}]`
  （全部 bounded / 可序列化）；
- `InProcessSpecialistRuntime(SpecialistRuntime)`：capability
  `swarm.cartography.compose` → compose、`swarm.cartography.revise` →
  revise、`swarm.audit.judge` → audit；供 ADR-0187 dispatcher 路径
  在进程内直跑确定性专家（LLM 委派路径仍走
  `SubagentDispatcherRuntime`）。

## 7. registry.py 追加

| 项 | cartographer | critic_auditor |
|----|--------------|----------------|
| role key / value | `cartography_specialist` | `audit_judge` |
| budget_class | standard | light |
| max_wall_time_s | 240 | 180 |
| allow_mutation | True | False |
| failure_behavior | degrade_with_disclosure | fail_closed |
| expected_outputs | thematic_map, mapspec_ref, classification_decision | audit_report, goal_score, uncovered_requirements |

`SPECIALIST_REGISTRY` 追加 `"cartographer"` / `"critic_auditor"`；
`_role_name()` 按 `role.value` 查表命中 → ADR-0187 相位自动路由。

## 8. 测试矩阵（tests/unit/test_specialist_carto_auditor.py）

| # | 组 | 用例 |
|---|----|------|
| T1 | 制图 | 多字段 GeoJSON 数值专题：`natural_breaks`(Jenks) 胜出（适度偏态 + 推荐集），breaks 单调、k∈[3,7] |
| T2 | 制图 | 重尾计数数据 → `head_tail` 胜出，equal_interval/quantiles 留痕 rejected |
| T3 | 制图 | 色彩对比梯度：palette_colors 数 = k、相邻色 ΔE00 ≥ 阈值（可分辨） |
| T4 | 制图 | 类别字段 → categorical 图例 + qualitative 色板 |
| T5 | 制图 | 必配组件：title/scale_bar/north_arrow/attribution/legend 全在场，legend.visible=True；V4 自愈无 suppressed |
| T6 | 制图 | canonicalize 通过（schema 合法）；fingerprint 稳定可复现（同输入两次 compose 同指纹） |
| T7 | 制图 | delivery 券边界：ref 序列化 < 8KB；载荷在 ledger 且 ref_id 可取回 |
| T8 | RBAC | cartographer 越权调 `query_dataset` → SpecialistToolDeniedError；guarded_registry 返回 TOOL_NOT_ALLOWLISTED |
| T9 | 制图 | heartbeat/deadline：FakeClock 推进超限 → SpecialistTimeoutError |
| T10 | 审计 | 完整交付 + 满足 chapter → verdict=pass，goal_score=1.0 |
| T11 | 审计 | 故意缺图例 → V1 veto + verdict=fail + improvement_notes 含修复建议 |
| T12 | 审计 | 数据空洞（空 FeatureCollection）→ V4 veto |
| T13 | 审计 | 指标口径：需求未满足 → uncovered_requirements 非空 + V3 + goal_score<threshold |
| T14 | 审计 | fail-closed：审计对象不可达 / not_evaluated → 永不 pass |
| T15 | 审计 | 只读不变量：audit 前后载荷/chapter 深度相等；audited_fingerprint 与载荷指纹一致 |
| T16 | RBAC | auditor 越权调 `create_thematic_map` → 拒绝；四专家工具面两两不相交 |
| T17 | 收敛 | R0 缺图例 fail → revise 按 suggested_fix 修复 → R1 pass，rounds_used ≤ 2 |
| T18 | 收敛 | 不可修复场景（EMPTY_DATA）→ failed_escalated，全程 receipts 上交，绝不 pass |
| T19 | 收敛 | governor 并发纪律：在飞 ≤3；receipts 全部 normalize 通过 |
| T20 | 总控 | registry：get_specialist 两专家可达；ensure_subagent_roles_registered 幂等；未知名 fail-closed |
| T21 | 总控 | InProcessSpecialistRuntime 按 capability 路由产出合法 receipt；HeuristicSpatialDecomposer 相位含 swarm.cartography.compose/swarm.audit.judge 且 validate_swarm_graph 通过 |
| T22 | 委派 | delegate 路径：RecordingDispatcher 收到专属提示词头与角色名 |

## 9. 验收门禁

- `python -m pytest tests/unit/test_specialist_carto_auditor.py -v` 全绿；
- `ruff check app/services/agent_swarm/specialists/ app/services/agent_swarm/duo_session.py tests/unit/test_specialist_carto_auditor.py` 干净；
- 既有 `test_specialist_data_compute.py` / `test_swarm_orchestrator.py`
  回归不破（合并基线已验证）；
- 审查纪要：`review/AGENT-05-REVIEW.md`。
