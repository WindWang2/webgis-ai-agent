# F15 Design — Production Visual Observation / Critique / Repair

关联：ADR-0214（本方向）、ADR-0209（verify→critique→repair 闭环）、
ADR-0185（VLM critic runtime）、ADR-0186（visual self-healing）、
ADR-0119 W9（visual seam）。

## Ownership / Authority（单一真相纪律）

- **视觉不是 verifier**：视觉 finding 恒 `degradation_only=True` /
  `blocks_completion=False`（seam `_sanitize_finding` 结构性强制）。
  完成裁决唯一来源是 deterministic verifier + product verdict；
  视觉唯一裁决效应保持 READY → READY_WITH_WARNINGS 降档。
- **finding 词表不动**：domain 词表（5 域）与 finding_class 词表（6 类）
  冻结；视觉 taxonomy 是**消费侧归一轴**（code 命名空间 `visual_<t>`），
  不新增 UnifiedFinding 字段、不建第二 finding 类型。
- **修复执行不建第三通道**：user-approved visual repair 走
  `MapSpecLifecycleEngine.apply_visual_heal_patch`（ADR-0186 事务入口，
  锁/CAS/checkpoint/revision 单调/idempotency 白嫖）；planner 分类面
  （visual error → deferred, executor=user）不变。
- **user-wins**：引擎 `guard_intent_locks` 是唯一锁裁决；user-locked
  entity 上的 heal 直接 error 回执（`layer_locked`），提案/应用层不放大权限。
- **预算/防循环不建新账本体系**：跨运行 recurrence 是 W11 账本
  （per-epoch 修复面）与 healer 收敛账本（per-fingerprint 修复面）之外的
  **披露面**（观察侧），键复用 `recurrence_fingerprint`（同一铸造点）。

## Canonical Contracts

### VisualObservationInput（provider-neutral，ref-only）

```python
@dataclass(frozen=True)
class VisualScreenshotRef:      # 只有 ref/sha/尺寸，永不携带字节
    ref: str                    # blob key（内容寻址 vshot-<sha256>）
    sha256: str
    size: int
    width: int = 0
    height: int = 0
    mapspec_revision: int = 0

@dataclass(frozen=True)
class VisualObservationInput:
    trigger: str                      # ⊆ VISUAL_EVALUATION_TRIGGERS
    session_id: str
    mapspec_revision: int
    mapspec_fingerprint: str
    mapspec_projection: Mapping[str, Any]     # cartographic_projection 有界产物
    observation_summary: Mapping[str, Any]    # layers/render_complete/bbox/component_boxes（各有界）
    deterministic_findings: Tuple[Mapping[str, str], ...]  # ≤12 {code,severity,target}
    screenshot: Optional[VisualScreenshotRef] = None
```

- `from_snapshot(dict)` / `to_snapshot()` 双向；畸形输入诚实降级
  （缺键取默认，非 dict 顶层 → None）。
- `to_snapshot()` 永不含 bytes/敏感载荷 —— 测试锁「ref-only 纪律」。

### VisualObservationResult（诚实结论面）

```python
@dataclass(frozen=True)
class VisualObservationResult:
    status: str                  # evaluated | not_evaluated
    reason: str = ""             # not_evaluated 机器可读原因（fail-closed 矩阵）
    findings: Tuple[UnifiedFinding, ...] = ()   # domain=visual，经 seam 白名单二次消毒
    provider: str = ""           # rules_only | vlm | hybrid
    taxonomy_counts: Mapping[str, int] = MappingProxyType({})
    screenshot_sha256: str = ""  # 摘要回声；trace 只允许这一级
    duration_ms: int = 0
```

### Taxonomy（封闭 8 类，单一归一点）

```
overlap | crop | legibility | contrast | label_collision |
legend_mismatch | empty_space | hierarchy
```

- `normalize_to_taxonomy(dimension, defect_type="", evidence="") -> str`
  （不可映射 → ""，诚实丢弃计数进 `taxonomy_counts["unmapped"]`）；
- `finding_code(cat) -> f"visual_{cat}"`（沿用 #1479 的 `visual_` 命名
  空间防撞名纪律）；
- 融合亲和表 `TAXONOMY_DETERMINISTIC_AFFINITY`：taxonomy 类 ↔ 确定性
  code 前缀/精确表（如 label_collision ↔ `carto.label.*`/`label_collision`、
  contrast ↔ `carto.color.*`/`layer_transparent`、legend_mismatch ↔
  `semantic_legend_*`/`GRAMMAR.AUDIT.PAIRING`、crop ↔ `viewport_no_bbox`…）。

### Fusion（跨域融合，deterministic wins）

`fuse_visual_with_deterministic(visual, deterministic) -> FusionOutcome`：

- key = `(affected_entity or "map", taxonomy 类)`；
- 命中确定性 finding（自身或其 repair_class 已指向同一实体同类问题）→
  visual finding 保留在披露但 `repair_class=""`、`evidence` 附加
  `corroborates:<finding_id>` 收据 —— planner 不再为同一实体同类问题
  触发第二次修复（跨域去重的唯一效应；披露不删减，诚实保留）；
- 未命中 → 原样通过（error 级照常走 deferred 需用户批准的既有语义）；
- 纯函数、O(n)、有界（visual ≤12 输入上限沿用 seam）。

## Production Provider（seam 的仓库内首个生产 callable）

`app/services/gis_harness/visual_observation/provider.py::evaluate(snapshot)`

- 部署形态：`GIS_VISUAL_EVALUATOR=app.services.gis_harness.visual_observation.provider:evaluate`
  （未配置 = 零行为变化，m1 语义不变）；
- 模式 `GIS_VISUAL_PROVIDER_MODE`（默认 `rules_only`）：
  - **rules_only**：确定性 rules-half —— 复用
    `local_visual_criteria.evaluate_local_visual` 的像素级可度量事实
    （墨量带 → empty_space/legibility、边缘密度带 → hierarchy、重心偏移
    → hierarchy、显著色桶 → contrast），逐条带测量值证据。**边界纪律
    （W9）**：组件重叠/越界等布局事实是
    `render_observation.derive_component_layout_findings` 的硬证据领地，
    rules-half 绝不重复（否则视觉路径会绕过确定性检查的既有趣味）。
    零网络、可离线；无 screenshot → `not_evaluated("no_screenshot")`
    （诚实缺席，无 findings）；
  - **vlm**：需 screenshot ref 可解析 → `build_critic_engine().evaluate(...)`
    （单次调用、显式超时、fail-closed `not_evaluated` 矩阵原样复用）；
    ref 缺失/解析失败/key 缺席 → 空 findings（诚实缺席，不猜）；
  - **hybrid**：rules ∪ vlm，同 `(entity, taxonomy)` 融合为一条；
- 同步 seam 下的异步桥：`VisualCriticEngine.evaluate` 是 async，seam
  callable 是 sync —— provider 用一次性 worker thread + `asyncio.run`
  执行（不污染调用方 loop）；墙钟预算 `GIS_VISUAL_PROVIDER_TIMEOUT_S`
  （默认 20，上限 60），超时 → 空 findings + 日志（绝不阻断终验）；
- 输出：每条 critique → taxonomy 归一 → `UnifiedFinding(domain="visual",
  code=visual_<t>, source="visual_observation_provider")`，交给 seam
  `_sanitize_finding` 白名单二次消毒（结构性防翻转的最后一道）。

## Screenshot 隐私/保留（ref-only 纪律）

- `store.py`：PNG 魔数 + ≤4 MiB 校验 → sha256 → `get_filesystem_blob_store()
  .put_blob("vshot-<sha>", ...)`（内容寻址，天然去重）；
- 会话索引 `map_state["_visual_screenshots"]`（≤8 FIFO）：ref/sha/revision/
  size/created —— **trace/journal/map_product 只允许 ref+sha 摘要**，
  字节只在 provider 评估瞬间经 `resolve()` 进入内存；
- `prune`：索引 FIFO 淘汰即删 blob（retention ≤8/session；fail-open，
  清理失败不影响评估）。

## User-Approved Visual Repair（两步提案/应用）

新路由 `POST /api/v1/chat/sessions/{sid}/visual-repairs/plan` 与
`.../visual-repairs/apply`（scope `session:write`；CSV + openapi 快照同
步刷新）：

- **plan**：读 `map_product.visual_findings`（+可选 finding_ids 过滤）→
  `repair_bridge.visual_findings_to_defects()`（taxonomy → HEALABLE 四类
  确定性映射；映射不出 → skipped `unmapped_category`，诚实披露）→
  `VisualHealStrategyPlanner().plan()` 预览 ops（闭包 ⊆
  `HEAL_OP_CODES` 四类呈现面微变异）→ 提案（proposal_id + ops_signature +
  base_revision）存 `map_state["_visual_repair_proposals"]`（≤4 FIFO）。
  **零突变**；
- **apply**：body 必须 `approved === true`（缺省/False → 400
  `approval_required`）；重读提案 + `expected_revision` CAS（客户端漂移
  → 409 `revision_conflict`；plan→apply 间 revision 前进 → 409
  `proposal_stale`）；`engine.apply_visual_heal_patch(origin="user",
  expected_revision=..., mutation_id="vrepair:<proposal_id>")`。
  **user-wins 语义**（沿 lifecycle guard 既有裁决）：user origin 是用户
  自有锁的唯一 override —— 但批准必须发生在披露之后，plan 预览对触达
  锁定图层的 op 如实标注 `touches_locked`；agent/system origin 的自动
  修复路径仍被 guard 一律拒绝（锁对自动路径绝对有效）。收敛耗尽
  (`SelfHealConvergenceExhausted`) → 200 `{applied: false,
  reason: convergence_exhausted, hard_stop: true}`（诚实硬停披露，
  **不是** 5xx）；幂等重放同 proposal_id → 引擎 dedup 回放既有世代；
- **复验**：apply 成功后 BackgroundTasks 调 `maybe_finalize_map_product
  (sid, reason="visual_repair")`（seam 触发白名单既有词首次获得生产消费
  方；heal 已改 revision，去重门自然打开）；复验失败不影响 apply 回执。

## Recurrence 硬停（跨运行）

- `recurrence.py`：账本 `map_state["_visual_observation_state"]`
  `{"v":1, "findings": {fp: {runs, first_verdict, last_revision}},
  "hard_stopped": [fp…]}`，findings ≤16 FIFO、hard_stopped ≤8；
- 键 = UnifiedFinding.recurrence_fingerprint（既有铸造点，零新哈希）；
- `run < MAX_VISUAL_RECURRENCE_RUNS(=3)`：runs+1，照常披露；
- `runs >= 3`：移入 hard_stopped —— 该 finding 在披露面降为 info +
  `visual_loop.hard_stopped` 收据，**不再进入 plan-face**
  （不再反复向用户索要同一修复）；退出条件 = 指纹消失（修复生效）或
  用户手动清（新提案批准即重置该类计数——修复尝试本身就是进展信号）；
- finalizer additive：`MapCompletionResult.visual_loop: Dict`（缺键不
  序列化，镜像 `loop_stop` 纪律）；异常 → 空披露绝不阻断终验。

## Failure Semantics（全 fail-closed）

provider 缺席/超时/解析失败/screenshot 不可解析 → 空 findings +
`not_evaluated` reason（结果=「无视觉发现」，非「视觉合格」——披露面
无 pass 语义）；recurrence/store 任何异常 → 空披露；endpoint 全部
typed 错误码；视觉永远不能把非 READY 升为 READY、不能掩盖 deterministic
error（测试锁定）。

## 测试计划（无浏览器/无网络/无 LLM）

1. `test_visual_observation_contracts.py`：契约往返、ref-only、有界、
   taxonomy 封闭归一、融合 deterministic-wins；
2. `test_visual_observation_provider.py`：seam 默认关零变化、三模式、
   超时、fail-closed 矩阵、seam 二次消毒、反翻转（visual error 不得
   升级状态/掩盖 deterministic error）；
3. `test_visual_recurrence.py`：runs 阶梯、硬停、FIFO 有界、修复重置；
4. `test_visual_repair_endpoint.py`：plan 闭包/跳过诚实、apply 批准门/
   CAS/锁/convergence 硬停/幂等、复验触发；
5. `test_visual_regression_corpus.py`：golden_images 10 缺陷 + 正常负例
   → 规则/混合面 finding 断言 + 字节不落 trace。

## Out of Scope

- 前端截图采集（RenderObservation 增列）与 UI 审批面板 —— 通道与契约
  本方向交付，采集接入另立方向；
- visual_judge v2 白名单/healer 词表变更；
- `ApplyVisualHealPatchIntent` 进 `intent_codec` 14 意图 union（避免
  lifecycle 五注册面热改；专用路由直达事务入口）；
- S3 blob 后端、多 pod 收敛账本共享（进程内账本局限沿用 ADR-0186 披露）。
