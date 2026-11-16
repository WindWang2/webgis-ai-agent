# G42 TEST_MATRIX — 验收矩阵 → 测试映射

| 编号 | 场景 | 验证层 | 位置/状态 |
|---|---|---|---|
| R01 | 地图导出缺失补全 | 单元集成（真实 G20 提交链→统一派发→export_mixed_map→真实文件+lineage 入章节） | ✅ `test_map_export_loop.py::test_map_export_loop_real_export` |
| R02 | 数据成果交付 | 单元集成（真实 G18 链） | `test_runner_loop.py::test_delivery_loop_real_services` ✅ |
| R02+Pi | Pi 同链交付 | Pi 旅程 | `tests/integration/test_pi_g42_goal_repair_journey.py`（真实终验产计划→preview→start→交付→重终验 satisfied） |
| R03 | 未变 revision 新观察 | 单元 + 面板事件接线 | `wait_fresh_observation`（同 revision 新 seq 才成功）；前端重观察事件 → map-panel 既有 reconcile（j42 档 + map-panel 监听为加性两行） |
| R04 | 多动作一次计划 | 单元 | bindings/runner 多动作顺序执行（supersede 用例含 2 动作） |
| R05 | 动作成功但缺证 → 保持 partial | Pi 旅程断言 finalizer 语义 | 旅程 endorses only on real evidence（partial→satisfied 仅当回执族在证） |
| R06 | satisfied/无计划 | 单元 | `test_service_semantics.py::test_satisfied_verdict_typed_error` / `test_missing_plan_typed_error` |
| R07 | 篡改 action id | 单元 | `test_invalid_action_selection_rejected`（服务端重建绑定，仅认 id） |
| R08 | 文本伪造授权 | 设计面（无文本授权通道）| start 只认 session 归属 + 服务端派生参数 |
| R09 | 损失确认缺席 | 单元（真实 preview 词表） | `test_delivery_loop_real_services` 第一段：零提交 |
| R10 | blocking 不放宽 | 绑定 hard-coded `partial_policy=blocking` | `test_bindings.py`（entry 绑定断言） |
| R11 | 双击/并发单 claim | 单元（DB 幂等键） | `test_store.py::test_idempotent_double_submit_same_active_claim` + service 复用 |
| R12 | supersede | 单元 | `test_runner_loop.py::test_superseded_goal_marks_attempt_stale` |
| R13 | revision 漂移 | export CAS（expected_revision）+ 前置 goal 指纹 | 绑定 params 断言 + R12 同机制 |
| R14 | 同证据 no_progress | 单元 | `test_service_semantics.py::test_no_progress_refusal_on_same_evidence` / `test_new_evidence_allows_new_attempt` |
| R15 | Pi 回调启动 | Pi 旅程 turn 3 | 零等待返回 + 独立任务执行（无递归 prompt/writer） |
| R16 | UI busy | 单元 | `test_ui_entry_busy_when_active_pi_turn` |
| R17 | 用户锁冲突 | 复用 runtime_repair 内部闸 | `execute_runtime_repair` 透传 outcome（locked_refused 披露） |
| R18 | 权限撤销 | 路由 verify_session_owner + delivery verify_session_access | 路由层测试（404 语义） |
| R19 | 他人回执 | 复用 receipts owner 过滤（A17） | 不新增通道 |
| R20 | 观察超时 | 单元（waiting_observation） | `wait_fresh_observation` 有界等待路径（超时分支在 R03/R20 用例合并覆盖） |
| R21 | 预算 admission | service start + runner 步前双闸 | `test_service_semantics`（denied → typed） |
| R22 | runtime_repair 双扣 | adapter skip（内部自理） | runner `_charge_if_executed` 跳过 repair_cartography |
| R23 | 成功文本≠证据 | evaluator 纪律（不改动） | 既有 goal_satisfaction 测试覆盖 |
| R24 | 取消竞争 | 单元 | `test_cancel_skips_unstarted_actions` |
| R25 | 断网/刷新恢复 | attempt 持久行 + 读路径续接 | store 视图回读 + panel activeAttempt 恢复 |
| R26 | 崩溃对账 | 惰性对账 | `service._mark_runner_lost` / `_reconcile_waiting_delivery` |
| R27 | 迟到 job 归属 | waiting_delivery 收口 + finalizer 幂等门 | `_reconcile_waiting_delivery` |
| R28 | 取消晚于产物 | 回执如实保留 | `test_cancel_skips_unstarted_actions`（cancelled_partial） |
| R29 | 不支持动作 typed | 单元 | `test_bindings.py` unsupported 系列 |
| R30 | 跨租户 | 路由归属复核 | `_owned_session`（attempt 归属会话再验） |
| R31/R32 | UI→Pi→dispatch 真实链 | Pi 旅程 | J-G42（供应商边界 stub，其余真实） |
| R33/R34 | 浏览器面板闭环 | Playwright | ✅ j42 2 passed（预览→确认→启动→回执→终验对照→下载链；无计划收敛）；SSE 无重复终态（stub 档 + reducer attach 一次性） |
| R34 | 流式/非流式一致 | 同 service 层 | 单元集直接覆盖 service（两入口仅包装差异） |
| R35 | G43 未合入独立 | 依赖面零引用 | grep 证实（无 pi_turn_context receipt 依赖） |
| R36 | 换代/compaction | 从宿主 refs 重建 | preview 每次从存储块重建（无内存授权缓存） |
| R37 | wave=1 自等待 | Pi 旅程（TOOL_WAVE_*_CONCURRENCY=1） | start 零等待设计 + 旅程运行期环境变量 |
| R38 | start/取消竞争 | 取消幂等 + runner registry | `cancel_repair_attempt` 无 runner 时直接确认终态 |

## 层级记录纪律

- 单元/集成：isolated SQLite + 内存 store + 真实服务（G18 交付链真实写盘）。
- Pi 旅程：真实 Pi 子进程 + 生产 app + stub LLM（仅供应商边界）+ 真实终验/交付/重终验。
- 浏览器：真实 UI 面板交互（playwright）——与后端 SSE fixtures 联测。
