# G37 LOOP_LEDGER

## 轮次记录

### L1（2026-11-14 晚）—— Phase B + WP01-11 实现

- 研究完成：BASELINE/DEDUP_MATRIX/PLAN 落盘；Explore 子代理产出 Pi pin 精确契约报告（消耗 1 个 subagent 槽位，已释放）。
- vendor/pi：网络 clone 超时 → G35 同款离线方案（`git clone --shared .g35-pi-gitdir` @ b4afdddf + 硬链 node_modules/dist）。gitlink 未动。
- 实现：目录/prefs(CAS)/应用编排/usage 账本/预算 admission/迁移 g3701/路由/只读工具/SSE model_status/前端组件与 i18n。
- 迁移实跑：`alembic upgrade head` + downgrade -1 + upgrade（sqlite）✅ 三表在、head=g3701。
- 单测 38 通过（tests/test_g37_session_model_runtime.py）。
- 既有回归：test_pi_e2e/test_pi_bridge_lock/model_config_generation 49 通过；仅 2 个 **base 上即失败**（`test_prompt_drain_timeout_raises_and_sends_abort`、`test_prompt_send_failure_sends_abort` —— MagicMock 不支持 await 的 abort 断言；干净 base `git stash` 复现取证，非本任务回归，不在本任务修复范围）。
- 前端：`tsc --noEmit`（主配置）零错误；test 配置的既有错误与 base 相同（同 node_modules 版本漂移，取证）。node_modules 以符号链接共享主工作区（G35 同款）。
- **环境事件**：主工作区共享 `.venv` 被外部进程重建为空壳（py3.12，23:57）→ 改用任务私有 venv `webgis-worktrees/.g37-venv`（uv + requirements.txt/dev 全量安装）。后续验证一律用它。
- 修复记录：SSE 顺序（model_status 移到 task_start 后）；inherit+set_model 失败降级（不阻断，M09 披露）；explicit 读回不同模型拒绝（M10）；零价→unknown（M29）；Pi 目录估计→estimated。

### L2（2026-11-15 凌晨）—— WP12 真实 Pi 旅程

- 新增 `tests/integration/test_pi_g37_session_model_journeys.py`（driver 子进程：node stub LLM 记录每次请求 model + 回包 usage/responseModel；uvicorn 生产 app；真实 vendor bridge）。
- 旅程：J0 M01/M20（显式 g37-b + webgis_map_intent 真实工具 + 2 条 usage 记录 + responseModel）、J1 M02/M03/M05（三会话交错 + 回切）、J2 M30（预算拒绝 prompt 零发送）、J3 M06（kill 子进程 respawn 偏好重应用 + 累计不归零）、J4 M23（Last-Event-ID 重放不重发不重计）。
- 运行：见 TEST_MATRIX.md（进行中）。

## 下一步

- 旅程通过 → 交独立 reviewer（子代理槽位 2）全量 diff 评审 → 修复 → 复审。
- 合流最新 master → 受影响回归 → commit/push/PR。

## 已知未测/边界

- browser→后端 全链（真实浏览器）未在本任务跑（无 REQUIRE_BROWSER 车道改动）；HTTP API+真实 Pi 链已覆盖；UI 组件为标准 React 且 typecheck 通过 —— 浏览器层标注未跑。
- compaction usage 的原生上报路径未在旅程触发（stub 不产生压缩）；coverage 面如实披露 not_included。
- 真实供应商账单不可得（无真实凭据）—— 价格事实只能来自 stub 回包/价格表，已按 provider/estimated/unknown 分层。
