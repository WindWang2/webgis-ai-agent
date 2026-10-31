# INDEPENDENT_REVIEW — 独立评审记录

## R1（2026-10-29/30，general-purpose subagent，未参与实现，66 个工具调用全 diff 实证审）

**Verdict：FIX-FIRST**（P1×2 + P2×3 + P3×5；核心抽象契约/状态机/指纹/证据判定为干净，问题集中在 tools 层生命周期与并发缝）。

### P1
- **P1-1** recompute 无法解除 blocked 且 `recomputed_steps` 谎报未执行步骤（实证复现：无路网 plan→recompute 传路网 → 返回声称重算 3 步但零 receipt/零 ref，isochrone 仍持旧码）。
- **P1-2** plan/recompute 破坏 state.py 声明的单写者协议（plan 完全不取锁；recompute 锁外快照 mutate → 并发 run 丢失更新）。

### P2
- **P2-1** 预算门三条旁路：(a) `max_features` 调用方可传 10^12 解锁；(b) step: 上游输入与步骤输出无行数门；(c) recompute 只对 affected 子集做指纹对账——未提及角色的原地 overwrite 静默服旧。
- **P2-2** `params_overrides` 可改 `unit` 参数而不联动 `unit_contract`（receipt/envelope 声称米、计算公里）。
- **P2-3** 科学前置在本层是装饰性（`step.preconditions` 零消费）且 orchestrator/families 注释虚假承诺「用既有评估器裁决」/「CRS/范围对齐由本门裁决」。

### P3
- P3-1 默认 workflow_id 用 `abs(hash(...))`（PYTHONHASHSEED 随机化，与 deterministic=True 冲突）；P3-2 `TOOL_ALGORITHM_IDS` 是第二份映射且 zonal_stats 占位语义错配；P3-3 重复 plan 同 id 清空运行态；P3-4 role 输入逐步骤重复水合；P3-5 投影多工作流无时序。

### 明确排除的面（R1 核查后未发现问题）
typed blocked/skipped 静默变 pass（verdict_of 全拦截）；跨 session/租户；catalogue 完整性（4 命令全登记）；波内并发共享突变；与 workflow_runtime/plan_mode/analysis_reuse 的重复建设（分层成立）。

### 清偿记录（commit 311e3247e，每项配红测）
- P1-1 ✅：recomputed_steps 只报 attempts 增长步骤；unblock 路径真实可达（apply_plan_verdicts 语义修正：fresh 非 blocked/skipped 即解除）；test_recompute_unblocks_with_new_data_and_reports_honestly
- P1-2 ✅：plan/recompute 全程 `session_lock_registry.lock(awf:{id})`；recompute 锁内 reload；test_plan_idempotent_preserves_run_state
- P2-1 ✅：钳制 [1, DEFAULT_MAX_FEATURES]；step: 上游行数门；recompute 接线 collect_stale_steps 全量对账；test_run_max_features_clamped_server_side
- P2-2 ✅：unit 覆盖联动 unit_contract + 词表校验；test_unit_override_syncs_unit_contract / rejected
- P2-3 ✅：文档改为「声明性引用 + 算法层权威裁决」（诚实边界）
- P3-1 ✅ sha256 内容寻址；P3-2 ✅ 删错配占位；P3-3 ✅ plan 幂等保留；P3-4 ✅ (role,ref) 水合缓存
- P3-5 ⚠️ 已知限制：投影按 alias 取当前工作流（单工作流权威语义）——PR 披露不改

## R2（2026-10-30，全新上下文复审：清偿逐条复核 + 二次全量审，76 工具调用含 2 次真实篡改演练 + 1 次 POC）

**Verdict：FIX-FIRST（无 P0/P1）**。Part A：R1 清偿 11 项中 10 项 CLEAR（含实测牙齿验证），P2-3 半清偿（orchestrator docstring 残留）。Part B 新 findings：

- **B1（P2）** plan 幂等复用谓词不含 data_contracts → 同 id 换绑静默丢弃 + 响应/持久态撕裂（POC 实测：补 denominator 被 silent drop）。
- B2（P3）recompute 强制 stale→pending 击穿执行器指纹复用（no-op 重发也真重跑）。
- B3（P3）`status=="repeated"` 当成功（潜伏——当前不可达但判别式错误适配）。
- B4（P3）run 锁内 reload 失败回退锁外快照；B5（P3）F28 负例无法区分 run 诚实 blocked 与 run no-op；B6（P3）投影注释与实现不符 + only_steps 裸字符串拆字符；B7（P3）P2-1b/1c 清偿分支零直接测试。
- 核查排除面：跨 session 锁键（保守互斥无正确性风险）、Redis 降级 decline、families 参数漂移三道防线、catalogue、verdict 语义、波内并发、锁续约。

### R2 清偿记录（commit bb935143f，每项配红测）
- B1 ✅ 复用谓词 = 指纹 + 绑定 refs 一致；换绑 → plan_replanned；test_plan_rebinding_same_id_is_replan_not_silent_reuse（POC 场景反转钉扎）
- B2 ✅ 去除强制 stale；no-op → recomputed_steps==[] + ref 不重铸；test_recompute_noop_reuses_by_fingerprint
- B3 ✅ TOOL_DISPATCH_REPEATED 诚实失败 + 红测（registry.has 仅在无注入 dispatch 时查——dispatch 注入面自身处理未知工具）
- B4 ✅ AWF_STATE_UNREADABLE decline；B5 ✅ run_log_events 期望 + 重 pin；B6 ✅ 注释纠偏 + 字符串拒绝；B7 ✅ 两个直接红测补齐；P2-3 残留 ✅ orchestrator docstring 改声明性表述
- 验证：82 单测 + 45 验收绿；ruff 绿

## R2b（终审：R2 评审员带原上下文复核清偿）

（进行中）
