# ADR-0158: 视觉裁判与自愈闭环 —— L5 Oracle 接线 + 动作注册表 + 修复-重评-回退

- 状态：Accepted（record-only 默认；阻断模式切换条件见"切换条件"）
- 日期：2026-09-13
- 关联线：adaptive-cartography/09-visual-judge-selfheal
- 关联：ADR-0060（validity 阶梯天花板）、ADR-0061（诚实评估 / 无证据 ≠ 修正）、
  #657（三态 verdict token）、#722（legacy 模板追踪）、#789（结构性 mutation 分类）、
  specs/cartographic-quality-rules-and-memory-spec.md（visual-judge deferred 的解除条件）

## 背景与问题

闭环骨架（HarnessEvaluator + evidence.py + cartography_runtime + quality_loop）存在三处
断裂，导致"不好看"无法自动变好：

1. **评审只覆盖 fingerprint 路径**：`tool_pipeline.py` 与 `agent_pi_bridge.py` 仅当结果
   携带 `mapspec_fingerprint` 才触发 `evaluate_cartographic_session`；模板
   symbology/heatmap 等前端 command 渲染路径永不进评审。
2. **L5 恒空转**：`_success_levels()` 的 `goal_satisfaction` 硬编码 `not_evaluated`；
   `app/services/gis_harness/visual_evaluator.py`（W9 seam）无生产调用方。
3. **自愈动作空间单一**：runtime AUTO_SAFE 只有投影恢复（4 条规则）；无换色带/分类/
   级数/值域/标注/版面动作，且修复后无效果判定（只迭代、不判断"是否变好"）。

## 决策

### D1 — 评审触发 =「产生了地图变更」（P1）

- **Plan A（结构化证据回填）**：模板 symbology 分支已通过
  `_track_legacy_template_in_mapspec` 提交真实 MapSpec mutation，但 fingerprint 未回填
  结果。新增 `_surface_generation_evidence` 把 lifecycle 的
  `mapspec_fingerprint/is_compiled/runtime_observation_seq/mutation_revision` 回填进
  命令结果 —— 该路径由此恢复完整证据阶梯（结构性分类，#789 语义）。
- **Plan B（命令形态判定）**：`cartography_runtime.MAP_CHANGE_COMMANDS` 命令族白名单
  （add_layer / add_native_heatmap / layer_style_update / layer_visibility_update /
  apply_layer_filter / reorder_layer / remove_layer / add_heatmap_raster）+
  `result_indicates_map_change()`。两条 agent 路径（tool_pipeline / pi_bridge）统一改用
  「fingerprint OR 命令族命中」作为评审触发。相机/注记/导出/底图 chrome 不触发。
- **诚实边界**：无 fingerprint 的命令路径进入评审后，运行态收敛仍诚实
  `not_evaluated`（`mapspec_fingerprint_missing`）—— 评审覆盖 ≠ 伪造收敛证据。

### D2 — 视觉裁判（P2）：新生产模块，gis_harness 桩不碰

- 任务书 §8 的可改区写 `app/lib/harness/visual_evaluator.py`，而既有桩实际位于
  `app/services/gis_harness/`（01/02 线禁改域）。**处置**：桩保持不动；在可改区新建
  生产实现 `app/lib/harness/visual_evaluator.py`（VisualCritique / VisualJudgeReport /
  白名单消毒 / 失败即 `not_evaluated`），注入 vocabulary（`"module:callable"`）与 W9
  seam 保持一致（env `CARTO_VISUAL_JUDGE`）。
- **fail-closed**：judge 未配置 / 注入加载失败 / 无截图 / 截图超限 / VLM 无 key /
  抛错 / 输出不合法 ⇒ `VisualJudgeReport.status="not_evaluated"`（机器可读 reason），
  证据行 `VISUAL_ORACLE`（not_evaluated, info）落账；绝不产出 pass，也不因缺席而 fail。
- **限流**：`(session, fingerprint, 截图内容摘要)` 记忆化 + 单次调用无重试 ⇒ 每份证据
  状态至多一次真实外呼。
- **输入**：截图来自既有头照设施（headless validator 的 `runtime_dir/map.png`，经
  `ToolCallEvidence.runtime_evidence_path` 链路）或测试 fixture（env 注入路径）。
  内置 VLM callable（OpenAI 兼容 chat completions + image_url）由
  `CARTO_VISUAL_JUDGE_VLM=1` 显式开启；无 key（占位符检测）⇒ `no_api_key`。
- **维度（≥5）**：readability / color_discriminability / composition_balance /
  information_density / polish_completeness；白名单外维度整条丢弃；任何改图意图字段
  （mutation/intent/spec/patch/layers…）整条判废（结构对齐 W9 `_sanitize_finding`）。

### D3 — L5 落地（P3）

- `derive_goal_satisfaction(cartography)`：
  - 无已评估视觉结论 ⇒ `not_evaluated`（携带 reason：visual_judge_disabled /
    no_api_key / no_screenshot / provider_error / …）；
  - 有视觉结论但 L4 锚点未过 ⇒ `not_evaluated`（`l4_anchor_not_passed`）——
    **visual 不得替 L4 背书**；
  - L4 通过 ∧ 视觉 error 级批评 ⇒ `fail`（诚实降级证据，fail-closed 兼容）；
  - L4 通过 ∧ 无 error ⇒ `pass`。
- **证据层级硬约束保持**：`evidence_class: visual` 的证据**不得单独**判 L4/L5 PASS
  （closed-loop 文档既有约束）；L4 状态在视觉证据附着**之前**计算（结构性隔离），
  视觉检查行只追加、永不改写既有三态 verdict。
- `MapSpecValidityTier` 天花板（ADR-0060，SEMANTIC_VALID=3）：**维持不变**。视觉裁判
  提供的是 goal-satisfaction 维证据，不进入 MapSpec 有效性阶梯；若未来把视觉结论计入
  validity 阶梯，需要独立 ADR（涉及 #659 的生产上限约定）。

### D4 — 自愈动作注册表（P4/P5）

- 新模块 `app/lib/cartography/selfheal_actions.py`（纯函数、无服务依赖）：
  - **动作类型（10 种 ≥ 5）**：restore_visibility / reapply_opacity / refresh_legend /
    restore_style_projection（既有投影恢复，行为不变）+ rotate_palette（换色带）/
    clamp_layout（改版面）/ adjust_classification（换分类方法·级数，复用 03 线
    `SymbologyDecision.rejected[]` 落选者）/ clip_value_domain（重裁值域）/
    adjust_labels（改标注策略）/ switch_map_type（换图型，explicit_only 仅建议）。
  - **风险分级**：auto_safe < auto_with_semantic_risk（默认只计划+披露，需
    `CARTO_SELFHEAL_EXPLICIT=1`）< explicit_only（永不自动执行）。
  - **排序契约**：risk 升序 → expected_effect 降序 → action_id 字典序；取首个被授权
    且可构造的动作。
  - **两个执行面**：`runtime`（同代前端 patch —— 投影恢复/回退，世代不变）与
    `desired_state`（lifecycle 呈现提交 —— 换色带/版面，经 `layer_upsert` 获得确定性
    复审 + 锁 guard + 世代推进；换色带必须同时轮换 paint 输出色，防
    LEGEND_STYLE_EQUIVALENCE 拒绝）。跨代语义变更**不得**只改 live（违反
    RUNTIME_MAPSPEC_GENERATION 收敛）—— 这是 runtime patch 面只能投影恢复的架构
    根因，也是提交面存在的理由。
- **修复-重评-回退（P5）**：
  - 每次动作签发记录 `quality_before`（确定性 fail/warn 计数优先，visual 只作降级
    信号）；动作成功（ACK 终态 + 新观察）后一次性判定 `quality_after`。
  - 投影恢复 patch：**变差（有害）⇒ 回退**（before/desired 互换的同代补偿 patch）；
    **持平 ⇒ 不回退**（回退会把 live 拉离权威投影），走既有
    `repeated_runtime_repair → repair_exhausted` 语义。
  - 呈现提交：未改善即回退（重提交变更前呈现，防色带轮换循环）；提交推进世代后，
    尝试记录进入 `history` + `inherited_tried`（防换代后同一动作无限重选）。
  - 动作耗尽 ⇒ `repair_exhausted`（`selfheal_actions_exhausted`），全部尝试留档可审计。
  - `MAX_RUNTIME_REPAIR_ITERATIONS = 2` **维持不变**（回退/提交均计入签发上限）。
- **锁纪律**：呈现提交在会话锁释放后执行（`apply_mutation` 自带会话锁，持锁重入
  自死锁）。不持锁调用路径（测试 + 多数生产入口）内联执行、确定性完成；直接持锁
  调用方（observation/ACK 路由）调度后台任务锁外执行，新世代评审由下一次观察自然
  触发。

### D5 — 兼容与 verdict（P6）

- `quality_loop` 期望态职责、`cartography_runtime` 运行态职责边界不变；首个修复尝试
  保持与既有 `plan_runtime_repairs` 行为逐字节一致（union patch）；既有
  repeated/superseded/iteration-limit 终止语义保持。
- `render_verdict_for_llm` 增量披露：repair_attempts 增加 `action_name/improved`；
  新增 `visual`（裁判摘要）与 `selfheal_suggestions`（未授权动作建议，≤3 条）。
  **三态 token（pass/fail/not_evaluated）语义不变**；pass 形状不含新增块。
- 提交产生的新世代登记进 harness mutation 台账（`cartographic_selfheal_commit`，
  结构性分类）—— 否则后续评估对着旧世代 reported 指纹永远 superseded，闭环断裂。

## 默认关闭面与切换条件（P8）

- 视觉裁判默认关闭（未配置 ⇒ `visual_judge_disabled`，零 VLM 调用）；开启需显式 env。
- 自愈为**record-only**：视觉结论只落证据行与 visual_evidence，不改三态 verdict、
  不独立触发阻断；旋转/钳制类呈现提交由**确定性规则**失败驱动（visual 仅作维度
  映射建议），语义级动作默认只建议。
- **阻断模式切换条件**（归 10 线 ratchet）：① 视觉裁判在 ≥3 类图型 × ≥2 周生产观察
  中误报率（error 级批评被人工推翻的比例）< 15%；② 确定性规则与视觉结论冲突率
  < 5%；③ ratchet 基线（10 线）收录 visual 维度并稳定 1 个里程碑。满足后方可把
  `VISUAL_*` 检查计入 gate（`CARTO_VISUAL_JUDGE_MODE=block`，当前未实现、留待 10 线
  定义精确语义）。

## 风险与回滚

- 全部新行为 env 门控或增量字段；回滚 = 关闭 `CARTO_VISUAL_JUDGE*` /
  `CARTO_SELFHEAL_EXPLICIT`，评审触发回退仅需还原 `result_indicates_map_change`
  单点（fingerprint 路径行为始终未变）。
- 呈现提交走既有 lifecycle（锁 guard、确定性复审、revision 守卫、checkpoint），
  无新持久化格式、无迁移。
