# REVIEW — G12-PI-NATIVE-INTEGRATION

## 第一轮独立评审（BLOCK → 已全部修复）

- Reviewer：独立 general-purpose subagent（`agent_d0367ff2`，未参与实现；给定
  base `816705218a` / head `b7cba0d4` + 完整 diff + 用户场景，未预设通过）。
- 裁决：**BLOCK — P1×4 + P2×4 + P3×1**。
- 修复 commit：`见 git log`（"fix(pi): 独立评审修复……"）。

### P1 逐条清偿

| # | 发现（reviewer 证据） | 修复 | 覆盖 |
|---|---|---|---|
| P1-1 | grant 自授权：任何注册面直调可自铸 grant（repro：`granted_without_tool_search ('nearest_facility',)`）；旧负面测试被反转为执行成功 | **语义澄清 + 收紧**：vendor 源码核实（`_getCallableTools`：codemode/deferred 无论 active 均可调；模型直调在 active 集内）→ deferred 直调/沙箱嵌套天生合法，grant 服务这两个场景而非「先搜索才能调」；grant 只接受 **deferred（非 native 前门）**；恢复 tier-3/destructive/hidden 拒绝 + 计数器零执行 + 未注册名 resolve 拒绝 | `test_scope_enforcement_negative_types_rejected`、`test_scope_enforcement_unregistered_name_rejected`、attack-matrix 语义文档 |
| P1-2 | 配置换代只在校验 sweep 触发；在飞 turn deferred 后下一 turn 不恢复（旧配置无限期沿用） | `chat.py` turn 准入处（surface recycle 旁）增加 `recycle_if_config_stale()` | 测试补「turn 结束后下一 turn 边界必须完成换代」断言 |
| P1-3 | 继承的 OPENAI_API_KEY 压过解析新 key，而 generation 哈希新 key（期望/生效对账失真） | `_spawn_process` 解析 key **覆盖** env（单一权威优先级） | A10 测试补继承 env key 场景（真实请求用新 key） |
| P1-4 | rebuild 读错形状：`layer.visible` 而非 canonical `layer.layout.visibility`；`plan.capabilities`/`recent_evidence_refs` 在真实 SessionPlan 不存在 → 隐藏层误投可见、待办/证据永不投影 | `_layer_is_hidden`（layout.visibility 优先 + 旧字段 fallback）；`open_capabilities(plan)`（progress 语义）；`PlanStep.evidence[].ref`；测试改用**真实** SessionPlan/PlanStep/StepEvidence + canonical 隐藏层回归 | `test_rebuild_block_hidden_layer_not_misreported_visible` 等 5 例 |

### P2/P3 清偿

- **P2-1** clear_queue TOCTOU：发出前重校验在飞 turn 身份，翻转则跳过 clear+abort（不清错队列/误杀新 turn）。
- **P2-2** J6b 掩盖选择器回归：恢复原始两段式裸名流程；`declared_nearest` 降为证据字段。**存量抖动证据**：master@v1.5.0 干净基线（G21 worktree）10 跑 4 失败，同签名（`nearest_ok:False` + step_error）——非本 PR 引入，如实披露；确定性发现覆盖由新 J8 承担（driver 3/3：search→声明面 merge→buffer 真实执行）。
- **P2-3** fresh-spec fail-open：非空 echo + fresh spec 不可编译 → fail-closed（echo 缺席才放行）。
- **P2-4** 层截断披露：>12 每类遗漏计数披露（含隐藏类）。
- **P3-1** ADR compaction 钩子名（`session_after_compact` 不存在于 v1.5.1 → 改为实际存在的 before/failed）；回退文档补 `PI_MIN_VENDOR_TAG` 回退要求。

### 评审已验证项（reviewer 结论，复述）

- vendor checkout = tag v1.5.1 原样；C11–C16 契约描述与 vendor 源码一致
  （tool_search merge / nested parentToolCallId / clear_queue / EOF shutdown）。
- `parent_tool_call_id` 加性且顶层缺席；OpenAPI 无破坏性变更。
- J6b 存量抖动干净基线复现（同上）。

## 复审（第二轮）

- 复审范围：上述 9 项修复的 diff + 全量回归证据。
- 复审结论：见下方「复审记录」。

## 第二轮复审（BLOCK → N1/N2/N3 已修复）

- Reviewer：同一独立 subagent（`agent_d0367ff2` 恢复上下文复审）。
- 裁决：**BLOCK — P1×2（N1/N2）+ P3×2（N3/N4）**；P1-1/P1-2/P1-3/P1-4、P2-3、
  P2-4、P3-1 判 FIXED；P2-1/P2-2 判 PARTIAL（见下）。
- 修复 commit：「fix(pi): 复审修复——scope 违规传播死存储与 receipt 缺席复检回归」。

### N1（P1，W05 核心路径失效）— FIXED
`stale_surface_gate` 死存储 `scope_mismatch`：grant 失败后未赋回 `mismatch` →
函数 `return None` 放行 → `SURFACE_SCOPE_VIOLATION` 生产不可达（reviewer 实测
native 前门面外直调通过 gate 并会执行；分支既有测试
`test_pi_surface_abi_route.py::test_scope_enforcement_rejects_out_of_surface_bare_name`
失败为证）。修复：grant 尝试后 `scope_mismatch is not None → mismatch =
scope_mismatch`（typed 拒绝传播，master 同款直接传播语义恢复）。该旧测试按
ADR-0288 真实语义重写为三断言（native 面外拒 / deferred 面外 grant+执行 /
proxy 不受限）——`test_scope_enforcement_semantics_adr_0288` 6/6 通过。

### N2（P1，兼容回归）— FIXED
fresh-spec 复检条件 `not_selected = receipt is None or ...` 把「receipt 缺席 =
兼容放行」扫入复检（分支既有测试 `test_no_receipt_echo_is_ignored_for_compat`
失败为证；与模块文档矛盾）。修复：复检对象限定为本 turn **实际 grant 过的工具**
（`discovered_store.contains(...)`），receipt 缺席恢复兼容放行。

### N3（P3）— FIXED
字段契约快照未刷新（`API_CONTRACT_FIELDS_UPDATE=1` 刷新）：含本 PR 3 个加性
字段 + 2 个存量漂移字段（agent_runtime_* ← #2012、constraint_layout ← #2015，
与 openapi.json 同模式）。

### PARTIAL 项（如实记录，不标记完成）
- **P2-1**（clear_queue 同会话后继窗口）：跨会话翻转已挡；同会话后继 turn 不改变
  `_active_turn_sid`（CONC-F1 既有语义），残留窗口内 clear_queue+abort 理论上可
  命中后继 —— 增量风险小（abort 本就命中同 worker），需 turn 转换序列化或
  turn-scoped vendor 操作才能清零，超出本 PR 安全边际（留记）。
- **P2-2**（J6b 声明面断言）：`declared_nearest` 为证据字段。计划资格选择器的
  回归掩护由 J8（确定性发现旅程，硬断言）部分覆盖；J6b 选择器回归需先修存量
  抖动根因（selector turn-2 激活不稳定）——已留记，不属本 PR 引入。
- **N4**（P3，J7 llm_calls vacuous）：master 存量（`json.dumps({"key":...})` 带
  空格 vs node 紧凑输出，恒 0）—— 非本 PR 引入，留记。

### 测试口径修正（ reviewer 指正 ）
第一轮「unit 240 passed」未覆盖 `tests/unit/test_pi_surface_abi_route.py`
（该文件在分支上实际红 —— N1/N2 之证）。修正后按 reviewer 同口径实跑：
`pytest tests/unit -k pi` → **1849 passed, 77 skipped, 1 xfailed**
（唯一失败 `test_mapspec_store::test_validate_and_compile` 为本 worktree 缺
`frontend/node_modules/.bin/jiti` 的环境差异；补符号链接后 19/19 通过，
master 基线同通过）。

## 第三轮复审记录（终审）

- Reviewer：同一独立 subagent（第三轮）。
- 裁决：**PROCEED** —— 全部 P0/P1 闭合且有实测证据；剩余仅如实留记的
  PARTIAL×2（P2-1 同会话后继窗口 / P2-2 declared_nearest 证据字段化）+
  P3×2（N4 J7 计数 vacuous 存量 / 测试名陈旧 nit），不构成合入门槛。
- 逐项：**N1 FIXED**（四个场景实测：native 面外直调 →
  SURFACE_SCOPE_VIOLATION 拒绝；deferred 面外 → grant+执行且可观测；
  receipt 缺席+伪造 echo → 放行；已 grant+过期 echo →
  SURFACE_FINGERPRINT_MISMATCH fail-closed 仍在）；
  **N2 FIXED**（复检收窄为实际 grant 工具；`test_no_receipt_echo_is_ignored_for_compat`
  转绿）；**N3 FIXED**（字段快照刷新，含 2 存量漂移字段说明）。
- 新引入问题：无（控制流重排经推演无新洞：selected 走 receipt 校验 / grant 走
  fresh-spec / kill-switch=0 整体跳过）。
- Reviewer 额外核查：更宽子集（`-k "pi or surface or context_assembly"`，
  2156 passed）暴露 2 个失败（`gis/test_platform_capabilities.py::
  [plan_workflow_orchestration]`、`gis_harness/scene3d/::
  test_analysis_orchestration_staleness.py`）—— 经 `git diff --stat` 核实所在
  文件与本 PR diff **零改动**，属基线存量/环境问题，不在本 PR 范围。
- 一个 P3 nit（无需行动）：`test_scope_enforcement_off_by_default` 名称陈旧
  （默认 ON 下依赖 deferred grant 放行），断言在两种语义下均成立。

## 总结论

- 独立评审 3 轮：BLOCK(P1×4+P2×4+P3×1) → BLOCK(N1/N2 P1) → **PROCEED**。
- 全部 P0/P1 闭合；PARTIAL×2 与 P3×2 如实留记于 PR body「残余风险」。
- 满足任务书 §F：独立 review 无阻塞 → 推送并创建 PR。
