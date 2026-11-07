# G01-PI-NATIVE-RUNTIME — Oracle / TEST_MATRIX

> 每个实现波次（W1–W6）先写失败测试，实现后同一测试连跑两次。基线：475 passed（M1）。
> 通用门：改动范围内 Ruff + Mypy + 相关 Pytest 全绿；`git diff --check` 干净。

## W1 PI-01：TurnPhase 状态机 + exactly-once（G3+G4）— tests/unit/test_pi_turn_state_machine.py

| # | Oracle | 验证 |
|---|---|---|
| W1-1 | 合法生命周期 CREATED→STARTING→RUNNING⇄TOOL_WAIT→SETTLING→SETTLED 全程可走 | 纯 tracker 断言 |
| W1-2 | 非法转移被拒绝+计数（不抛异常、不改当前相位） | 如 SETTLED→RUNNING |
| W1-3 | 终态锁定：终态后一切转移均拒绝 | |
| W1-4 | settlement 门闩恰好一次：第二次 begin_settlement 返回 False | |
| W1-5 | kernel status→终相位映射：completed/refused→SETTLED；cancelled/failed/aborted/interrupted/未知→FAILED | |
| W1-6 | seam 双调用 no-op：第二次 `_settle_turn_outcome` 不再产生 kernel end/settle 效果，返回已记录 status | bridge 级直测 |
| W1-7 | 未知 kernel status → tracker fail_task（不再 complete） | monkeypatch coordinator 返回 weird status |
| W1-8 | 流式 turn 的 tool_execution_start/end 驱动 TOOL_WAIT⇄RUNNING 且进 evidence summary | scripted RPC |
| W1-9 | 取消 turn 观测到 CANCELLING 相位、终相位 FAILED、outcome CANCELLED | task.cancel() |
| W1-10 | TurnEvidence summary 含 turn_phase 段（host-neutral attach） | |

## W2 PI-04：SSE 事件幂等 + 纯文字负面（G1+G2）— tests/unit/test_pi_sse_idempotency.py

| # | Oracle | 验证 |
|---|---|---|
| W2-1 | 同一 toolCallId 的重复 tool_execution_end 只发一条 step_result；重复被计数 | scripted 双发 |
| W2-2 | 重复 agent_settled 不产生第二个 task_complete/二次结算 | |
| W2-3 | 去重计数进 pi_surface_metrics + TurnEvidence warning | |
| W2-4 | 纯文字 turn（无 map_product）task_complete 不含 map_product 键、无 map_finalization 事件 | 负面断言 |
| W2-5 | 有地图产物 turn 的 map_finalization 照发（防去过火） | 正向对照 |
| W2-6 | 不同 toolCallId 不互相去重 | |

## W3 PI-06：三态披露（G6）— tests/unit/test_pi_runtime_mode_disclosure.py

| # | Oracle | 验证 |
|---|---|---|
| W3-1 | runtime_mode() 词表 = {native, fallback, unavailable}，单一定义点 | |
| W3-2 | /health agent_runtime 段含 mode+reason（安全有界） | TestClient |
| W3-3 | task_start SSE 含 runtime_mode（native 路径） | scripted |
| W3-4 | fallback 场景（pi 不可用）mode=fallback 带原因，不冒充 native | |
| W3-5 | pi_doctor 摘要与运行时同词表 | offline 模式 |

## W4 PI-03/08：热更新可见性 + 性能度量（G5+G7）— tests/unit/test_pi_surface_refresh.py + tests/benchmarks/test_pi_perf.py 增补

| # | Oracle | 验证 |
|---|---|---|
| W4-1 | registry 世代变化 → turn 边界回收 worker，下一次 spawn 重建 dump（新工具对下一 turn 可见） | recycle 单元测试（R1 复审后对齐实现语义） |
| W4-2 | 未变化世代不回收；kill switch/无世代事实 fail-open | 同上 |
| W4-3 | TTFT/冷热启动/事件去重计数进 pi_surface_metrics.snapshot() 聚合 | |
| W4-4 | 冷启动（spawn→ready）与热启动（复用）延迟车道有量化断言（mock RPC 上界） | benchmark 车道 |

## W5 PI-05/07：durable job 取消 + 注入抵抗（G8）— tests/unit/test_pi_durable_cancel.py + 注入用例

| # | Oracle | 验证 |
|---|---|---|
| W5-1 | turn 取消 → 该 turn 排队中 durable job 被撤销/标记取消，不再提交地图 | |
| W5-2 | 其他 session 的 durable job 不受影响（隔离） | |
| W5-3 | 用户上传地理元数据中的恶意指令文本不改变 turn 身份/工具授权（HMAC token 仍绑定原 turn） | 注入 fixture |
| W5-4 | 100ms/10s（短化）预算边界取消行为正确 | 缩短 PI_* 超时 |

## W6 PI-09/10：端到端旅程 + 契约文档

| # | Oracle | 验证 |
|---|---|---|
| W6-1 | stub-LLM 真实 RPC + 真实 registry：学校分布→热力图配色→切城→框选邻近→取消→恢复 全旅程有事件/地图回执 | PI_JOURNEY_E2E 车道 |
| W6-2 | 纯文字旅程无 map completed | |
| W6-3 | PI_RUNTIME_CONTRACT.md + PI_UPSTREAM_COMPAT_MATRIX.md 交付且与代码一致 | 文档审 |
| W6-4 | 全 Pi 测试回归两遍全绿 | |

## M8 独立复审 Oracle

- 子代理 B 冷读 `git diff origin/master...HEAD` 逐文件；P0/P1 全修复并复审；P2 记录不隐藏。

## M9 PR Oracle

- 真实 `gh pr create --base master`；body 含问题/实现/兼容/证据/测试命令/风险。
