# G37 TEST_MATRIX

## 验证层级与结果（诚实分层）

| 层 | 内容 | 状态 |
|---|---|---|
| 单元（38 用例） | `tests/test_g37_session_model_runtime.py`：目录构建/代际/凭证位/注入拒绝（M07/M08）、CAS 并发双写单胜（M11）、message_end 提炼/合计（M20/M21/M24）、费用三态+零价 unknown（M28/M29）、账本幂等（M23）、能力门（M16/M17）、预算 admission（M30 逻辑面）、应用编排 stub（set_model/回读/撤销/inherit 降级，M09/M10/M12）、stream_prompt 协议路径（model_status SSE/预算拒绝 prompt 零发送/usage 落盘）、PUT 目录外拒绝 | ✅ 38 passed（g37-venv） |
| 既有回归 | test_pi_e2e / test_pi_bridge_lock / test_pi_model_config_generation / test_chat_api / test_g37 全组合 | ✅（除 2 个 base 既有失败：`test_prompt_drain_timeout_raises_and_sends_abort`、`test_prompt_send_failure_sends_abort` —— MagicMock 不可 await 的 abort 断言，干净 base 复现取证，非本任务引入） |
| 既有回归（补充） | `test_pi_rpc_client_810.py::test_stop_reaps_after_sigkill_810` base 即失败（同 venv/环境），非本任务引入 | ⚠️ base 既有 |
| 迁移 | `alembic upgrade head` → 三表 + head=g3701；`downgrade -1` + `upgrade head` 回环 | ✅ 实跑（sqlite /tmp/g37-mig-test） |
| 前端 | `tsc --noEmit`（主配置）零错误；eslint（改动文件）零警告；vitest store 解析 3 用例 | ✅（test 配置 tsc 的既有错误与 base 相同） |
| 真实 Pi 全链（WP12） | `tests/integration/test_pi_g37_session_model_journeys.py`（PI_JOURNEY_E2E=1，node stub LLM + uvicorn 生产 app + 真实 vendor v1.5.2 子进程）：J0 M01/M20（显式 g37-b + webgis_map_intent 真实 dispatch + 2 条 usage + responseModel）、J1 M02/M03/M05、J2 M30（stub 请求计数零增）、J3 M06（kill→观测死亡→respawn 重应用 + 累计不归零）、J4 M23（Last-Event-ID 重放不重发不重计 + 非空转守卫） | ✅ 1 passed（真实 Pi 子进程 + 生产扩展 + 生产 dispatch + stub 协议记录；2026-11-15 00:35） |
| 浏览器层 | 真实浏览器（Playwright）未跑 | ❌ 未运行（如实披露；HTTP/SSE API 层已由旅程覆盖，组件经 typecheck+vitest） |
| 供应商真实账单 | 无真实凭据 | ❌ 不可得（费用事实按 provider/estimated/unknown 分层，stub 回包验证 provider 路径） |

## 关键修复（由旅程驱动）

- **Pi reader 跨代竞态（既有 bug，M06 旅程暴露）**：外部 kill → respawn 换代后，旧 `_read_responses` finally 无守卫地把新代 `_process_died` 翻真并 fail 新代 pending → 新进程被旧尸体毒死。修复：reader 世代钉扎（`start()` 传入本代 proc；循环/读行只忠于本代；finally 以 `self._process is proc` 守卫）。
- SSE 顺序：model_status 移到 task_start 之后（保持既有首事件契约）。
- inherit + set_model 失败 → 不阻断（目标即 spawn 默认），get_state 继续验证；explicit 失败 → 拒绝。
- 零价（runtime 无价目默认 0）→ unknown，绝不当免费；Pi 目录价（totalFromProvider=false）→ estimated。
