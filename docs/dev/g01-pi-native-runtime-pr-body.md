# PR: feat(g01-pi-native-runtime): Pi 原生运行时契约一体化 —— TurnPhase 状态机/SSE 幂等/三态披露/代漂移回收/durable 撤销/PI-09 全旅程（ADR-0286）

## 来源（issue / 文档债 / 任务书）

- 目标来源：G01-PI-NATIVE-RUNTIME goal 任务书（/goal-loop，2026-11-07）；前置 #1973（Pi v1.5.0 vendor/Host，已合并）、#1989/#1993（pi-gis usability v1/v2，已合并）、#1998（载荷预算，已合并）、#2009（session 锁重入，已合并）。
- Baseline：`origin/master @ 585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07 17:54 +0800，执行时实测；与任务书快照 SHA 一致，无漂移）。
- Dedup：open PR 仅 8 个 dependabot 依赖 bump（无功能冲突）；审计明细见 `planning/G01-PI-NATIVE-RUNTIME/BASELINE.md`（含冲突矩阵与避让决策：#1999–#2007 issues 全部避让）。

## 根因与证据（Problem Evidence）

M0 实时审计（子代理 A 逐文件深读，证据 文件:行号 见 BASELINE.md §2.2）：

1. **G3 状态机碎片化**：`app/agent_pi_bridge.py:2044-2061/2558-2589`（现约 +100 行偏移）—— 6 布尔旗标 + 2 字符串 × 两条路径各复制一份初始化（历史两处 UnboundLocalError）；`tool_wait/settling` 相位不可表达不可观测；任务词表（created/…/failed）在代码中零命中。
2. **G1 SSE 事件幂等缺失**：`pi_event_mapper.py:247-271` cache-miss 降级 + `agent_pi_bridge.py:369-381` dispatch cache 读后即清 → Pi 重复回传同一 `tool_execution_end` 时第二份仍发 `step_result`；无 toolCallId 去重、无测试。
3. **G2 纯文字任务无负面锁**：mapper 只在有 map_product 时加键（行为正确），但全 Pi 测试无一断言「纯文本 turn 的 task_complete 不含 map_product」。
4. **G4 exactly-once 无直测**：seam 幂等设计完备（单结算 seam + coordinator 收据），但「第二次结算为 no-op」无用例；else→complete_task 开放分支无未知词表防御。
5. **G6 三态披露不统一**：/health 只报 runtime 名（health.py:93-122）；per-request 静默降级客户端不可见。
6. **G5/G7**：dump 仅 spawn 一次（pi_rpc_client.py:394-397）→ registry 代内动态注册对已运行子进程不可见；TTFT/冷启动只进日志无聚合（pi_surface_metrics.py:28-35 仅 4 计数器）。
7. **G8 durable 撤销缺链路**：JobOrigin 关联有（agent_pi_bridge.py:781-802），turn 取消 → 排队 durable job 撤销无实现无测试。
8. **P0（旅程发现）**：`pi_native_surface._pi_tool_definition` 把未解包 schema 条目传给 `_pi_parameters` → 全部长尾 dormant 工具投影成空 properties + `additionalProperties:false` → vendor 按注册 schema 拒绝一切参数 → 裸名长尾面整体不可调用（native 7 不受影响）。

## 改动清单（Architecture & Files）

设计：ADR-0286（docs/adr/0286-pi-native-turn-phase-and-idempotent-observability.md）；行为契约成文 docs/PI_RUNTIME_CONTRACT.md；上游矩阵 docs/PI_UPSTREAM_COMPAT_MATRIX.md。

- **W1 TurnPhase 状态机**（PI-01）：新增 `app/services/chat/pi_turn_state.py`（枚举 + 转移守卫 + settlement 门闩恰好一次 + 有界轨迹）；两条宿主路径挂载；seam 相位门闩拦第二次结算；未知 kernel status fail-closed → fail_task；`TurnEvidence.attach_phase` → summary["turn_phase"]。
- **W2 SSE 幂等**（PI-04）：流循环 turn 内 toolCallId 终结事件去重集（有界 1024，超界诚实降级）；重复丢弃三面观测（metrics/warning/日志）；重复 agent_settled 单终态。
- **W3 三态披露**（PI-06）：`pi_compat.classify_runtime_mode` 单一定义点（native/fallback/unavailable + 4 原因码）；/health additive 字段；task_start.runtime_mode；pi_doctor 汇总行 + JSON runtime_mode。
- **W4 代漂移回收 + 度量**（PI-03/08）：rpc 客户端锚定 spawn dump 的 registry_generation；`recycle_if_surface_stale`（turn 边界 stop+start，在飞 turn 延迟，`PI_SURFACE_HOT_RECYCLE=0` kill switch，_respawn_lock 双检）；chat 准入接线；metrics 时延窗口（spawn_ready/ttft p50/p95，窗口 256）+ surface_recycles；perf 车道。
- **W5 durable 撤销 + 注入抵抗**（PI-05/07）：新增 `pi_durable_cancel.py`（桥接 DurableJobStore.request_cancel，never-raise，db 可注入，跨 turn 防御）；seam 非 clean 终态触发；伪造 ACTIVE_TOOLS marker 中和 + 伪 token HMAC 拒绝 + 100ms 级短预算边界测试。
- **W6a 投影修复 + PI-09 旅程**：`_pi_tool_definition` 传 function schema 本体（与 native_tools_for_pi 同源）+ 回归锁；旅程扩展 J6（wrap-native 嵌套拒绝）/J6b（两段式计划资格 → nearest_facility 裸名 + 内联 FC 真实最近邻计算）/J7（同消息 + Last-Event-ID 重放，零 LLM 调用）。
- **测试污染修复**：包级 `run_settle_effects` 打桩改手工保真恢复（PEP562 惰性委托物化污染，monkeypatch 恢复机制反噬）。

新增：pi_turn_state.py / pi_durable_cancel.py / tests/unit/test_pi_turn_state_machine.py / test_pi_sse_idempotency.py / test_pi_runtime_mode_disclosure.py / test_pi_surface_refresh.py / test_pi_durable_cancel.py / docs×3 / planning×3。
修改：agent_pi_bridge.py（钩子，不改控制流权威）/ evidence.py（additive）/ pi_surface_metrics.py / pi_compat.py / health.py / health_schema.py（additive）/ pi_rpc_client.py / chat.py（9 行）/ pi_native_surface.py（1 行修复）/ pi_doctor.py / 4 个测试文件扩展。

## 验证命令与输出摘要（Test Evidence）

- 命令（本地串行、无 cov）：
  - `python -m pytest tests/unit/test_pi_turn_state_machine.py tests/unit/test_pi_sse_idempotency.py tests/unit/test_pi_runtime_mode_disclosure.py tests/unit/test_pi_surface_refresh.py tests/unit/test_pi_durable_cancel.py -q --no-cov` → 51 passed（新增 51 用例）
  - 全量 Pi 回归 5 批（unit+integration+chaos+sse_resume+health，详见 LOOP_LEDGER 第 9 轮）→ 590 passed / 0 failed
  - `bash scripts/build-vendor-pi.sh && python scripts/pi_doctor.py` → 6 域全 PASS + runtime mode 行（exit 0）
  - `PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py -q --no-cov` → 1 passed（33.6s）——**连续两遍一致**（第二遍 32.9s）
  - `python -m pytest tests/integration/test_pi_vendor_contract.py tests/integration/test_pi_schema_coercion_golden.py -q --no-cov` → 9 passed（**两遍一致**）
  - `python -m ruff check <全部改动 .py>` → All checks passed；`python -m mypy`（repo ratchet）→ Success: no issues found in 35 source files
- 基线既有失败归因：pi_rpc_client.py 6 个 mypy 报错为基线债（ratchet 白名单外：Popen-None 收窄×4 / bytes f-string×1 / stdout None×1，均不在本 PR 新增行）；无其他基线红。

## Review 意见与清偿（Independent Review）

- Reviewer：子代理 B（独立冷读，与实现者不同判断链），逐文件 review `git diff origin/master...HEAD`（含 7 项特别审查：去重集生命周期/resume 一致性、durable 撤销时长、pool 亲和、相位机早退收口、pi_doctor JSON 破坏面、legacy 零影响、投影预算）。
- R1（986895304 时点）：**P0×0 / P1×3 / P2×10，需修复** → 逐条清偿（commit 50c517161）：
  - `P1-1 回收失败仍返回可用→502` → chat 准入回收后复检 `_bridge_alive`，死 worker 走既有 lazy respawn/降级 + 回归测试
  - `P1-2 回收 check-then-act 竞窗可杀在飞 turn` → recycle 折入 turn 锁临界区（1s 有界获取；锁序经全仓调用图核实 `_respawn_lock→_lock` 唯一向，无死锁）+ 回归测试
  - `P1-3 durable 撤销无预算拖长锁持有` → `wait_for(PI_DURABLE_REVOKE_TIMEOUT_S=5s)`（与 abort RPC 同口径；常量可测）+ 超时测试
  - P2×10 全清偿（门闩空串防御/probe_failed 原因码/pi_doctor breaking 标注/benchmark 真跑 start()/SQL 编译断言/deque 并发防护/docstring 措辞/契约幂等范围限定/J6 注释/怪表达式/TEST_MATRIX 对齐）
- R2（50c517161 时点）：**P0×0 / P1×0 / P2(新)×3，放行** —— P1×3 认定实质清偿（级联完整/锁序无反向/预算同口径）；新 nP2×3 随手清偿 2.5 项（probe_failed 入有界词表+测试、契约句删除、teardown try/finally），nP2-3 的 helper 四处去重记为后续测试基建项。
- 未清偿项：① `AnalysisTask (session_id, turn_id)` 复合索引（DB 迁移超出本方向所有权——jobs/gis_harness 域；5s 撤销预算已界损害，jobs lifecycle 终态不可迁移规则兜底晚到结果；建议后续方向处理）；② `_patch_pkg_settle_effects` 测试 helper 收敛到共享 conftest（测试基建重构，四处行为一致）；③ R2 附注：P1-2 的 1s 有界获取+临界区双检路径与 fast-path 预检行为等价，可选加固锁中途释放变体。

## 风险与回滚（Compatibility & Rollback）

- 兼容性：全部 additive（HealthResponse 新可选字段、task_start 新键、pi_doctor JSON list→dict——仓库内无自动消费方（已 grep 核实）；seam 新参数带默认值，旧调用语义不变）。schema/DB 无迁移。
- 回滚开关：`PI_SURFACE_HOT_RECYCLE=0`（关闭代漂移回收）；相位机/去重/撤销为默认行为，revert 对应单 commit 即可（W1/W2/W5 独立成 commit）。
- 残余风险：① durable 撤销在 seam 内 await DB（`wait_for` 5s 有界预算 + never-raise + 类型化 error 摘要；超时放行，晚到 job 由 lifecycle 终态不可迁移规则兜底；`(session_id, turn_id)` 复合索引为后续项——当前全表扫描被预算界定）；② 代漂移回收的 stop+start 使该 worker 会话 vendor 上下文重建（与既有 respawn_if_dead 同机制）；③ 全栈旅程的跨线程 SSE 取消窗口在本 sandbox 不稳定 → 取消语义由确定性车道锁定（test_runtime_chaos_pi 16 项 + 单元级），已在契约文档 §11 诚实披露。

## Out of Scope

- fork 语义 / 跨 turn sequence / 事件回放 API（会引入平行会话持久化，违反唯一事实源；边界已文档化 PI_RUNTIME_CONTRACT §3/§11）。
- PI_SURFACE_SCOPE_ENFORCE 生产启用推进（opt-in 现状保留）。
- issues #1999–#2007（制图/数据域独立 P1，M0 已裁决避让）。
- Pi 上游升级（保持 pin v1.5.0；升级走 PI_UPSTREAM_COMPAT_MATRIX §3 清单）。
