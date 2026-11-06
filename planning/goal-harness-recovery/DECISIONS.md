# DECISIONS — GOAL 05

| # | 决策 | 理由 | 备选与否决原因 |
|---|---|---|---|
| D1 | 诊断层引用而非扩展 `HarnessFailureClass`（11 类不动），新增 `GISRecoveryReason` 封闭细粒度词表（~22 码）映射回 11 类 | ADR-0248 词表已被 5 个评测语料钉死；additive 引用零破坏；429/5xx/权限等新语义在诊断层闭合 | 直接扩枚举：会动摇 REMEDIATION_POLICY 全表与既有语料期望值 |
| D2 | 处置 6 值（retryable/repairable/needs_user_data/needs_user_approval/unsafe_to_retry/terminal）为**裁决面**；与 RemediationAction（动作面）、SAFETY_CLASSES（安全面）三正交层各自封闭 | 三个既有词表语义不同维度，合并会破坏各自测试钉死 | 复用 SAFETY_CLASSES：其面向 cartographic findings，缺 needs_user_data/terminal |
| D3 | RecoveryPolicy 为纯决策（零 IO/零执行/零 sleep），只输出 RecoveryDecision{allowed,denied,required_confirmations,backoff_schedule} | 红线"不制造新调度引擎"；执行归既有通道；可确定性回放 | policy 内直接重试：违反 CAS/确认门与取消即停 |
| D4 | 工具风险分级 `ToolRiskClass` 7 值（read_only/idempotent_compute/state_mutating/paid_external/export_side_effect/cross_session/destructive），来源=注册表 tier + 名称前缀静态映射 + capability 元数据（可得时） | tier3/destructive 已有拒绝门；前缀映射对 ~160 工具零侵入可审计 | 逐工具手写清单：维护面大且漂移 |
| D5 | goal_verification 为**对账层**（消费 ADR-0183 报告 + observation_states + workflow roles/data_qualifications + L4/L5 可用性），slot 判 pass/fail/warning/not_evaluated 各带 evidence_refs；不重算 ADR-0183 判定 | 防第二事实源；补的正是"observation/数据资格不进裁决"缺口 | 扩展 evaluator：会改 104 案例期望值 |
| D6 | M5 重试指令走"投影披露 + 既有 recompute 工具"：continuation 纯函数给出 failed 能力行/stale 步骤 directive，进 [GIS Recovery] 投影行与 llm_payload，由 agent 调 `webgis_analysis_workflow_recompute` | 复用 #1869 选择性重算；agent 决策权保留；fail-closed 在 ref 校验层 | 新 REST 端点自动重试：绕过 agent 规划且难审计 |
| D7 | trace 补发射只做 additive：错误路径 TOOL_RESULTS payload 加 `recovery` 有界 dict；runtime_repair/finalizer 发 Stage.REPAIR(16)；observation 接受门后发 Stage.MAP_OBSERVATION(14) | chain_emitters 自认 5/18 发射；补齐与诊断同源 | 改 Stage 枚举/窗口大小：破坏 trace_store 兼容 |
| D8 | 修复 TDS:1060 存量键名 bug（`failure_class`→`class`）随 M3 一并提交 | 就在挂接 seam 上；不修则记忆候选恒 unknown，且我的消费者会被同一陷阱误导 | 留给他人：本 PR 依赖该键正确性 |
| D9 | corpus runner 复用 `simulate_agent_loop` 的 ScriptedCall/不变量机制，新增 `run_recovery_case`（fault 注入 → 真实 TDS dispatch → 捕获 recovery payload → 断言处置 + 禁止动作未执行） | replay.py 已有 dispatch seam 驱动；不建第二回放栈 | 全新 runner：重复造轮 |
| D10 | ADR-0279 记录本层契约（词表封闭性/fail-closed 纪律/三层正交） | 仓内 ADR 已到 0278；设计决策需可追溯 | 只写 docstring：违反仓内惯例 |
| D11 | venv 复用主 worktree `.venv`（symlink），测试 `-j2` 上限、`--no-cov` 聚焦跑 | 环境一致性已验证（fastapi 0.141.1/pytest 9.1.1，216 存量恢复测试绿） | 新建 venv：重复安装重依赖（geopandas 等）耗时且无增量价值 |
