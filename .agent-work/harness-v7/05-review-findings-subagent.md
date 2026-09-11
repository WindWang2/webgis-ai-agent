# Harness V7 独立 Review（Subagent B，Round 0）

日期：2026-09-10。范围：0b077dbc..6b45f606（10 commits，+6364 行）。方法：全部新模块逐行读 + 生产写者/读者交叉 grep + 关键断言本机 Python 实证。不信任自审结论，逐条复核。

## VERDICT: REQUEST-CHANGES

理由：两个 CRITICAL —— D1 的 committed 终态与 D6 的上下文提交在**生产写者形状下永不触发**（键契约错位）；D2/D6 的 headline「replan 生产驱动点」**逻辑上不可达**（预算闭环死锁）。两者均为新功能死代码、零 V6 回归（回归套件全绿），但 ADR-0130 的核心声明当前不成立，且单测以手工构造的章节形状掩盖了这两处。

## 验证结果（本机实测）

- `run-tests.sh` 7 个 V7 套件：**80 passed, 1 skipped**（6.65s）
- `run-tests.sh` test_map_completion + test_workflow_instance + test_durable_context_continuation_v6：**94 passed**（10.39s）
- `ruff check app/services/gis_harness/ app/evaluation/scenario_corpus.py app/services/chat/tool_surface_v3.py`：**All checks passed**
- 实证探针（python -c）：`map_product_block(READY+verified)` 输出**无 `task_complete` 键**；`decide_continuation` 在 deepen/requalify/repair 全耗尽、replan 未用时返回 **reobserve**（非 abort）。

## Findings

### F1 [CRITICAL] commit 标记永不写入 → COMMITTED 阶段不可达（键契约错位）
- 证据：`app/services/gis_harness/completion/pipeline.py:471`（`block = result.to_dict()`）——`MapCompletionResult.to_dict()`（contracts.py:435-451）**不含 `task_complete`**；`map_product_block` 也不补写该键。生产唯一 `gis_chapter["map_product"]` 写者是 pipeline.py:761。而 V7 读该键的三处：`runtime_state_machine.py:263-267`（`_task_complete`）、`:309`（COMMITTED 判定 `_task_complete and _committed_marker`）、`:717-718`（`commit_runtime_context` 早退 `not product.get("task_complete")`）。
- 实证：对生产形状块调 `derive_runtime_phase` → 永不 committed；`commit_runtime_context` 恒 None → 提交标记恒缺席 → 阶段天花板 = finalizing。
- 为何要紧：ADR-0130 D1「committed 终态」、D6「READY → 上下文提交」、`format_runtime_line` 的 `committed=true` 全部死路；gate 指纹里 `task_complete` 恒 False。
- 测试掩盖：`test_runtime_state_machine_v7.py:76,192`、`test_intent_acceptance_v7.py` 用手写 `{"task_complete": True}` 章节；`test_intent_acceptance_v7.py:249` 是恒真断言（`... or True`）、`:262` phase 断言宽到 `(committed, finalizing, critiquing, executing)`。
- 修复：在 `map_product_block` 持久化 `task_complete = _is_task_complete(stored)`（即 pipeline.py:943 的 verdict+final_map 折叠，单一来源），或在 runtime_state_machine 内复制该折叠（ verdict ∈ READY* ∧ final_map ∈ {verified, verified_with_degradation}）；并把测试改为消费真实 `map_product_block` 输出。

### F2 [CRITICAL] replan 生产驱动点不可达（预算闭环死锁）+ 自审修复 1 是死代码
- 证据：`pipeline.py:877`（`_finalizer_continuation`）仅当 `decision.verdict == "abort_with_disclosure"` 才调 `request_replan`。`decide_continuation`（continuation.py:117-124）的 abort 条件是 `sum(remaining) <= 0`，而 `remaining` 现含 `replan`（LOOP_BUDGETS replan=1，durable_context.py:66）。`replan` 只能由 `request_replan` 记账（plan_runtime.py:460）→ **只有先 replan 过才可能 abort，只有 abort 才会 replan**。修复预算耗尽的真实裁决是 `reobserve`（continuation.py:150-157），不是 abort。
- 实证：`decide_continuation(loops={deepen:2,requalify:2,repair:2,replan:0}, failure=renderer_failure)` → `reobserve`。
- 附带：自审修复 1 的守卫 `pipeline.py:881-882`（`failure_class not in ("cancelled","budget_exhausted")`）——failure dict 在 :865-870 **本地构造**，class 只可能是 `renderer_failure`/`tool_error`，该分支永不为真（死代码）。且 deep/requalify 与渲染修复无关却参与 abort 判定，使 abort 更不可达。
- 波及 V6：LOOP_BUDGETS 加 `replan:1` 后，V6 既有消费者 `runtime_repair._attach_continuation`（runtime_repair.py:383）在全耗尽场景的裁决从 `abort_with_disclosure` 漂移为 `reobserve` —— 「additive 零漂移」声明不完全成立。
- 测试掩盖：`test_intent_acceptance_v7.py:271-273` 用 `in ("abort_with_disclosure","reobserve")` + `if ...== abort` 的对冲断言，删掉 `request_replan` 调用该测试照样绿；docstring 宣称的「replan 置 pending」路径实际从未执行。
- 修复：驱动条件改为「repair 侧耗尽」（如 `decision.verdict == "reobserve"` 或以 `remaining["repair"]==0 ∧ 不可修复 error` 判定），deepen/requalify 不参与该路由；删除或接通死守卫；测试改为断言确定性 verdict 并实证 pending 置位。

### F3 [MAJOR] [GIS PlanRuntime] 投影与最小重算清单零生产消费者
- 证据：`format_plan_runtime_line`（plan_runtime.py:289）与 `seed_recompute_from_failures` 的产出**在 app/ 内无任何调用点**（grep 证实）；`session_plan.py` 只接了 `format_runtime_line`（:258）。D2 声称「投影给 Pi（[GIS PlanRuntime] / recompute 行）」—— Pi 永远看不到 replan=pending、min-rerun、reuse。
- 为何要紧：即使 F2 修复，replan 回路对 Pi 仍无指令面（replan_pending 仅经阶段词间接可见）；失败种子的「重算谁/复用谁」是 D2 的核心交付。
- 修复：在 `format_session_plan_projection` 仿 runtime_line 接入 `format_plan_runtime_line`。

### F4 [MAJOR] intent_acceptance 与 F_LAYER_HIDDEN「user-wins」语义矛盾
- 证据：`validators/layers.py:123-130` —— 用户隐藏结果层 = **warning**（"user-wins, disclosed only"），不阻断 READY；而 `intent_acceptance.py:101-103`（spec `visible is False` → `layer_hidden_in_spec` unmet）与 `:113-116`（observed visible False → unmet）**不区分是否用户主动隐藏、不过滤角色**，直接 `accepted=False`/`intent_verified=False` → observation_health 的 semantically_correct 被降级（pipeline.py:774 传入）。
- 为何要紧：用户合法隐藏图层（V6 明确容忍并只披露）在 V7 下被永久判为「意图未满足」，两处裁决面打架；且 requirements 取全部 `map_layers`（含非 result 角色）→ 误报面扩大。
- 修复：需求面过滤 `RESULT_LAYER_ROLES` 且仅对章节侧声明可见（复用 `_layer_declared_visible` 语义）的层做 desired/observed 核对；用户隐藏沿用 user-wins（不进 unmet）。

### F5 [MAJOR] delegation 台账在 rows 漂移时整批丢弃（审计丢失 + 幂等失效）
- 证据：`delegation.py:157-160` —— `_persist_records` 的 goal/rows 漂移守卫直接放弃写入。委派的子代理**本身会跑工具改行**，长委派几乎必然触发漂移 → 已真实执行的委派记录静默消失 → `delegate_cartography_qa` 的 `qa:{revision}` 查重（:297-300）失效 → 重复委派重复付费。
- 修复：锁内改为**合并写**（重读 fresh records + append 本条），漂移只放弃「陈旧结果字段」而非整条台账。
- 附带：:256 重试预算硬编码 `2`，复制 `LOOP_BUDGETS["repair"]`（第二本账，违反模块自述纪律）；未知角色的失败记录（:196-201）不入台账（无审计）。

### F6 [MINOR] kill switch 不逐位：commit 与 context checkpoint 不受门控
- `GIS_RUNTIME_STATE_MACHINE=0` 只挡 `maybe_update_runtime_state`（:597）；`commit_runtime_context`（:692，pipeline.py:796 无条件调用）与 `checkpoint_context_layers`（turn 收尾 agent_pi_bridge.py:2294 + READY pipeline.py:795）照常写键。当前被 F1 掩盖（commit 恒早退），修复 F1 后即暴露。ADR「逐位回退」声明对这两处不成立。context_layers 无任何开关。修复：commit/checkpoint 纳入同一开关（或独立开关）。

### F7 [MINOR] build_anchor 副作用 + revision 语义漂移
- `resume_anchor.py:107` 在锚点构建内调 `checkpoint_context_layers`（写 map_state）；`derive_context_layers`（context_layers.py:204-207）**每次调用 revision+1，与内容无关** —— turn 收尾 + READY + save_anchor 每路径都自增。revision 沦为写计数器，锚点摘要的 revision 比对语义弱化。修复：内容指纹不变则不写/不自增；或将 checkpoint 移出 build_anchor 的读路径。

### F8 [MINOR] 状态机锁窗口输入漂移守卫弱于同门兄弟
- `maybe_update_runtime_state` 在锁外派生（:648），锁内只守 goal/rows/stored-block 指纹（:668-679），**不守 map_product / workflow_instance 在等锁窗口内的变化**（finalizer 正是 map_product 写者）。对比：plan_runtime 锁内重派生（plan_runtime.py:361-363）、workflow_instance 有契约漂移守卫。后果有界（写入块 gate 指纹过期 → 下一触发自愈），但存在瞬态错相。修复：锁内用 fresh 章节重派生（O(rows) 便宜）。

### F9 [MINOR] 陈旧 replan_pending 在后续 READY/commit 后残留
- 消费只发生在计划指纹变化（plan_runtime.py:277-281）；READY/commit 路径不清位。场景：replan_pending 置位后新观察使成品转 READY → 阶段优先序（READY #2 > replanning #4）掩盖 pending，块内 `replan_pending=True` 与 committed 并存，[GIS Runtime]/后续派生受污染。修复：READY 判定或 commit 时清 pending（诚实：回路已被更好的事实取代）。

### F10 [MINOR] capability 可靠性反馈对 capability 类描述符永不生效 + 部分索引永久缓存
- `related_tools` 只在 algorithm 段填充（capability_descriptors.py:211）；capability 段（:149-167）为空 → `select_capabilities` 的可靠性罚分（:394-399）对 capability 描述符仅当裸 id 恰为工具名才命中。另：四个段各自 try/except 静默跳过（:168/:213/:231/:250）+ 进程级缓存（:431-436）→ 首次构建若某 registry import 失败（如本机无 h3 时 algorithm 链），残缺索引**终身缓存**。修复：capability 段补 related_tools（capability→tools 映射已存在）；缓存加「构建完整」标记或失败不缓存。

### F11 [MINOR] tool_surface_v3 V7 信号：+0.8 非明显「小幅」，且正路径零断言
- `tool_surface_v3.py:706-711` 首位描述符的 top-2 related_tools 各 +0.8 —— 相对检索分差是否「小幅」未论证（仅凭 V6 eval 门今日通过）。`test_capability_descriptors_v7.py:230` 的 on-path 断言 `assert v7_on in (True, False)` 恒真 —— 开启路径的行为（reasons 出现、排序扰动上界）完全未钉。修复：补一条「命中候选必得 v7 reason 且相对序偏移 ≤N」的实断言；评估 0.8→0.2-0.3 起步。
- 脆弱性评估：信号只加成**已入分**候选、不新增候选、失败零贡献 —— 结构正确；风险在量级与 algorithm tool_candidates 的误亲和使用。

### F12 [MINOR] map_critique.check_invalid_bounds 消费上一代成品的陈旧 bbox
- `map_critique.py:174-179` 读 `chapter["map_product"]["result_bbox"]`（上一轮持久化块）；本轮新 bbox 在 `_validate_all` 之后才算出（pipeline.py:240）。旧块坏 bbox 会在本轮重验时误报 `invalid_result_bounds`（error → failed）。修复：observation bbox 优先、stored bbox 仅作无观察回退并标注来源；或把 critique 挪到 bbox 计算后二次调用。

### F13 [MINOR] 场景语料覆盖门口径
- `scenario_corpus.py:337-343`：coverage_gaps 只排除 `category=="data_access"`，其余任何 category 的 registry 新族都进门 —— 新增非 analysis 类目（如纯渲染类）会误红；`build_scenario_corpus(max_cases=5000)` 静默截断后 `total` 报告的是截断值。构建 2316 案例仅评测期调用、stride 200 抽样 —— 热路径安全（实测套件 6.65s）。确定性（排序槽名 + 笛卡尔积 + 稳定 case_id）核实无误。

### F14 [TEST] 对冲/恒真断言清单（修复 F1/F2 时必须同步收紧）
- `test_intent_acceptance_v7.py:249`（`or True` 恒真）、`:262`（phase 四选一过宽）、`:271-273`（verdict 对冲 + 条件断言）；
- `test_capability_descriptors_v7.py:230`（`v7_on in (True, False)` 恒真）；
- `test_runtime_state_machine_v7.py` 全部用手工 `task_complete` 产品 —— 建议补一条「`map_product_block` 输出 → `commit_runtime_context` 全链」集成断言（会红在 F1 上，正是缺的那条）。

## 自审 3 项修复的独立复核

1. 「cancelled/budget_exhausted 不路由 replan」— **无效修复**：见 F2，守卫条件在唯一调用路径上不可满足（死代码）。真实的用户取消从不流经 `_finalizer_continuation`（其 failure 为本地合成），该修复既没保护到真实取消路径，也没改变任何可达行为。
2. 「移除 derive_runtime_phase 未用 stored 形参」— **真实修复**：签名现为 `(chapter, *, recovery_loops=None)`（runtime_state_machine.py:285-289），callers 同步，无残留。
3. 「补 continuation 直连单测」— **部分成立**：测试存在（test_intent_acceptance_v7.py:250）但断言对冲（F14），未钉住其 docstring 声称的 abort→replan_pending 分支（该分支按 F2 不可达）。

## Checklist 覆盖结论（余项）

- (1) 阶段优先序：文档序与实现一致、测试穷举；「READY+failed rows」经 finalizer 不可生产（blocked → BLOCKED_*），「replan_pending+READY」「stale debt+needs_repair」序正确 —— 除 F9 的 pending 残留外无错相。
- (2) 并发：锁模式与 maybe_update_workflow_instance 同款 + 漂移守卫；缺 map_product 窗口守卫（F8）；request_replan 预算读在锁外存在小 TOCTOU（budget=1 下最坏 used=2，有界，不另立条目）。
- (3) commit 幂等 × dedup 门：幂等守卫（revision 对齐）正确；「陈旧标记 → needs_repair 期 COMMITTED」因 F1 当前不可达；修复 F1 时按其建议折叠 verdict+final_map 即天然安全。
- (5) continuation 输入：除 F2 外，`remediate_and_retry`/`reobserve` 仅披露不驱动，无多余回路风险。
- (9) context_layers：预算「先域压缩后总淘汰」顺序正确、violations 留痕；`_context_layers`/`_final_display_ack` 与 `_recovery_state` 同属 map_state 非约定键 —— `classify_context_key` 在 app/ 内**零调用者**（含 V6），所谓白名单纪律实际无执行点（文档措辞与事实不符，惯例上与既有下划线键一致，不算回归）。
- (12) 兼容：全部新键 additive（runtime_state/plan_runtime/delegations/_context_layers/_final_display_ack/context_digest/intent_acceptance/continuation/display_confirmed），旧读者忽略属实；唯一共享面变更是 LOOP_BUDSETS 加 replan 对 V6 continuation 裁决的漂移（已并入 F2）。

---

## 修复记录（主 agent 对 Round 1 裁决的响应）

VERDICT REQUEST-CHANGES → 全部 14 项处置如下（commit 见 5b287753f* 系列）：

- **F1 [CRITICAL] 已修**：`map_product_block` 持久化 `task_complete`
  （复用 `_is_task_complete` 单一折叠）；并发现评审未点透的**深层根因**：
  块上 `product_verdict` 是 derive 的完整 dict 而非字符串 ——
  `_is_task_complete`/`_verdict_ready` 的字符串判定在真实块上恒 False
  （V4 起既有形状错位）。两处折叠均改为 dict/str 双形状兼容；
  runtime_state_machine `_task_complete` 对旧块回退 verdict+final_map
  折叠。回归锁：`test_committed_reachable_via_real_map_product_block`
  （真实 map_product_block 输出 → COMMITTED 可达）。
- **F2 [CRITICAL] 已修**：`decide_continuation` 与阶段派生的 abort 门槛
  均排除 replan（`_ABORT_GATING_LOOPS` / `_budgets_exhausted` 过滤）——
  abort 语义恢复 V6 逐位（runtime_repair._attach_continuation 无漂移），
  replan 成为 abort 的逃生舱：abort → request_replan → pending 置位 →
  计划事实变化消费。删除自审的死守卫；continuation payload 改为
  `replan` 子对象（abort 裁决保持权威不被覆盖）。回归锁：
  `test_abort_gate_excludes_replan_budget` + continuation 直连测试改为
  确定性断言（abort + replan_pending=True）。
- **F3 [MAJOR] 已修**：`format_plan_runtime_line` 接入
  `format_session_plan_projection`（additive 行，全返回路径覆盖）。
- **F4 [MAJOR] 已修**：需求面改用 `_planned_result_layer_ids`（结果层
  单一来源）；spec 用户隐藏层 → user-wins（不阻断、跳过 observed 核对、
  记入 disclosures）。测试同步。
- **F5 [MAJOR] 已修**：台账改锁内**合并写**（按 delegation_id），仅
  supersede 放弃；重试预算消费 `LOOP_BUDGETS["repair"]`（去硬编码）；
  unknown-role 失败记录入台账。
- **F6 [MINOR] 已修**：`runtime_state_enabled()` 公共门；finalizer READY
  提交块与 bridge turn checkpoint 纳入同一开关。
- **F7 [MINOR] 已修**：checkpoint 内容指纹不变 → 幂等跳写（返回存储块）。
- **F8 [MINOR] 已修**：状态机锁内重算全输入 gate（rows+product+stale+
  plan_version），不一致放弃（下一触发重派生）。
- **F9 [MINOR] 已修**：`commit_runtime_context` 清陈旧 `replan_pending`
  （幂等门放行 pending 在场时的重入）。
- **F10 [MINOR] 已修**：capability 段补 related_tools（capability→tools
  反查）；组件段改用 `_COMPONENT_DEFAULT_IDS` 静态表（原
  build_default_components 需计划上下文必失败 → complete 恒 False 的
  残缺索引终身缓存问题一并消除）；仅完整构建入缓存。
- **F11 [MINOR] 已修**：加成抽为纯函数 `descriptor_boosts`，权重 0.8→
  0.25 逐位衰减（下限 0.05）；正路径单测（同 pick 同权、跨 pick 衰减、
  无命中零映射）。
- **F12 [MINOR] 已修**：`check_invalid_bounds` 只消费本轮 observation
  bbox（陈旧 stored bbox 不再触发 error）。
- **F13 [MINOR] 已修**：`coverage_report` 增 `truncated` 标记（截断值
  不冒充全集）。
- **F14 [TEST] 已修**：恒真/对冲断言全部替换为确定性断言（`or True`、
  `v7_on in (True,False)`、verdict 对冲清单逐项复核）。

实测：V7 7 套件 + durable_continuation + map_completion +
workflow_instance = **178 passed / 1 skipped**；ruff 全绿；全量回归见
regression-v7-final.txt。
