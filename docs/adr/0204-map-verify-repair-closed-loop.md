# ADR-0204: 地图验证→批评→修复闭环收口（Unified Finding 契约补全 / 制图-视觉投影接入 / 环内 no-progress 硬停 / 视觉评估生产接线）

- 状态: Accepted
- 日期: 2026-09-20
- 线: feat/map-verify-repair-loop
- 关联: ADR-0081（Map Product Finalizer）、ADR-0086（render observation）、ADR-0088（runtime repair）、ADR-0118/0119（V6 统一 findings W6–W11、visual seam W9）、ADR-0134（V7 意图验收闭环）、ADR-0183（goal satisfaction）

## 1. 背景

方向目标 `Render → Observe → Verify → Findings → Repair Plan → Minimal MapSpec Patch → Re-render → Re-verify → Complete` 在 V3–V7 波次已分阶段落地为四个有界环（制图 quality loop / runtime repair / Map Product Finalizer / repair planner 防循环账本），完成语义由 product verdict + 七维契约 + goal satisfaction 驱动。勘察（`docs/dev/map-verify-repair-loop-recon.md`，基准 5a4d4632）确认：**闭环主体已存在，本方向不重做**；真实残缺是四点：

- **G1**：`UnifiedFinding` 缺 V1 要求的 finding id、**类别轴**（semantic / gis_correctness / cartographic / visual / runtime_display / export）、user-ownership 声明、recurrence fingerprint（W11 账本在 planner 侧私算同构哈希，finding 本体不携带）；
- **G2**：统一投影只覆盖 3/5 域 —— `semantic_check`（制图 review checks）声明"预留"从未接线，visual 产物不入 collector；制图层 deterministic fail 从不进 repair planner 的分类/账本面；
- **G3**：finalizer 环内修复循环只靠 `MAX_FINALIZATION_PASSES` 与"修复返回空"兜底，同一 finding 修复未生效时会重复索要同一修复直至轮数上限（环内对抗）；
- **G4**：visual seam（ADR-0119 W9）无生产调用方（m1 注记明示"接线是独立 roadmap 项"）——本方向即该 roadmap 项。

## 2. 决策

### 决策一：契约补全是 additive 投影，不动词表（G1）

`UnifiedFinding` 新增 4 个全默认值字段：`finding_id`（`domain[:12]:code[:24]:fp[:12]`，稳定自描述）、`finding_class`（封闭类别轴，`derive_finding_class` 单一推导点：domain 自带语义优先 → code 精确表 → scope 兜底 → 保守默认 `runtime_display`，不猜）、`user_owned`（声明：受影响实体 ∈ 调用方给出的用户锁/override 集；权威裁决仍在突变层统一 guard 与 `classify_repair`，投影层不放大权限）、`recurrence_fingerprint`（`canonical_fingerprint({domain,code,entity})`，与 `repair_planner.finding_fingerprint` **同构同值**——跨轮追同因与 W11 防循环账本共用一把指纹，不建第二套哈希；planner 侧改为优先取印记、空则回退本地计算，parity 由测试锁定）。

### 决策二：制图/视觉发现入统一投影，执行通道不增（G2）

`from_cartographic_check` 只投影 fail/warning（pass/not_evaluated 与畸形输入不产 finding——诚实缺席），`suggested_fix.operation` 经封闭映射归一到既有 16 类 repair_class 词表（未知 op → 空，分类走 planner 兜底，不新造动作）；`collect_unified_findings` 新增 kw-only 参数 `cartographic_review`（三形状兼容：CartographicLoopResult / CartographyReport 直挂 / `_cartographic_review` map_state 块的 `cartography.checks` + `desired_review.checks`，规则级去重，≤24）与 `visual_findings`（只收 seam 白名单产物 `UnifiedFinding` 实例，dict 形状必须先过 seam 校验）。`plan_repairs_for_chapter` 复用同一次 map_state 读取（账本 + mutation revision + `_cartographic_review` 同源，不双拉），制图 blocking 规则自此进入 W11 账本面。`classify_repair` 对 `semantic_check` 域：带修复建议 → `presentation_only` + executor=`quality_loop`；无建议的 deterministic fail → `ask_user`/`requires_user_approval`（诚实交人工）；user-wins（锁）仍压过一切。

### 决策三：环内 no-progress 硬停，跨轮防循环仍归 W11（G3）

`run_map_finalization` 循环内增加两道同运行判定：① 修复后全量 findings 指纹集合与上一轮完全一致且本轮确实应用过修复 → 停止；② 全部可修复发现都在索要本运行已申请过的同一修复（`(code,target)` → repair 记账）→ 停止。停止时 `result.loop_stop="no_progress"`（新披露词，REPAIR 链段透传），status 语义不变（可修复残缺 = needs_repair，不是 failed）。理由：同运行内重复提交同一被拒/未生效的突变是对抗；跨触发点的重试由既有幂等门（revision/rows fp/render seq）+ W11 账本 + user-wins 守卫管辖，本决策不重复该语义。真实收敛路径（修复生效）零影响（回归测试锁定）。

### 决策四：visual seam 生产接线 = 触发点 + 有界 snapshot + 恒 degradation_only（G4）

finalizer 尾段新增生产调用：`should_run_visual_evaluation("finalization")` 且 `GIS_VISUAL_EVALUATOR` 已配置时，以 `_assemble_visual_snapshot`（bounded MapSpec 元数据投影复用 `cartographic_projection` + 观察摘要 + 确定性 findings 清单 ≤12；**无截图字节、无大 payload**——§42 隐私边界归评估器实现）执行一次评估。发现（`UnifiedFinding`，seam 白名单已强制 `degradation_only=True`/`blocks_completion=False`）存入 `result.visual_findings`（对象面供 planner → `requires_user_approval`；序列化面 additive ≤8），披露面 severity 封顶 warning——**永不改写 status/READY 档位/完成语义**（LLM/视觉批评不是 verifier，确定性层才是完成门）。未配置 = 零行为变化（m1 语义保留）。`visual_repair` 触发复用既有 finalization 复验（用户批准的视觉修复进入下一触发点自然复验），不新建循环。

## 3. 后向兼容

- 新字段全默认值；`to_dict` 旧键不动（`loop_stop`/`visual_findings` 缺席不写键）；
- `collect_unified_findings` 新参数 kw-only 默认 None；`plan_repairs_for_chapter` 行为变化仅一处：此前"无 error finding → 不出计划"，现在制图 deterministic fail（blocks_completion）也出计划——这是 G2 的目的（披露面更诚实），verdict/goal 消费面不变；
- STATUS_*/VERDICT_*/finding code 词表冻结；无 schema 迁移；回滚 = revert（无持久化形状依赖）。

## 4. 验收

- 契约/投影/防循环：`tests/unit/gis_harness/test_unified_findings_v7.py`（15 例）；
- finalization 级 no-progress / 收敛回归 / visual 接线：`tests/unit/gis_harness/test_verify_repair_loop_wiring.py`（7 例）;
- 既有回归：gis_harness 域 + 制图评审 + verdict 锁全绿（见 PR 证据）。
