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

### L3（2026-11-15 凌晨）—— 评审修复 + 浏览器全链 + 事故与恢复

- R1 全部处置（见 REVIEW.md）；P1 签名修复后 tests/unit/test_pi_rpc_client + test_review_core_low_boundaries 118 用例通过。
- 浏览器全链打通：Turbopack 拒绝共享符号链接 node_modules（hermetic root）→ `next dev --webpack`；CORS_ORIGINS 默认仅 :3000 → 分端口装配显式放行随机前端端口；j18 real 档通过（浏览器→后端→真实 Pi→dispatch→UI + 预算拒绝）。
- 既有失败取证更新：`test_api_auth_required::test_mutating_routes_use_auth_enforcing_depends` 在干净 base（detached worktree 1276692737 实跑）同样失败（data_import/pi_interactions/temporal_media 路由缺失，非本任务路由）；连同 test_pi_bridge_lock×2、test_pi_rpc_client_810::stop_reap 共 5 个 base 既有失败，均与本任务无关。
- **事故与恢复**：base 取证时误用空 `git stash` + `git stash pop`，弹出了**共享 stash 列表中外部分支**（codex/all-issues-deep-review, 11-06）的储藏，冲突标记污染工作树（128 UU）+ 投入 58 个外部分发文件。恢复：`git reset --hard HEAD`（本分支自己的 tip，无未提交损失）→ 清除外部副本（其中误删 3 个已跟踪 .claude 文件，`git checkout -- .claude` 即刻恢复）→ 工作树与 3 提交完好。教训：多任务共享 stash 列表，禁止在 worktree 里使用裸 stash；base 取证一律用临时 detached worktree。
- 环境注记：检测到并行任务（/tmp/g38-base-check 出现）——共享 venv/锁/stash 纪律更严。

## 下一步

- 旅程通过 → 交独立 reviewer（子代理槽位 2）全量 diff 评审 → 修复 → 复审。
- 合流最新 master → 受影响回归 → commit/push/PR。

## 已知未测/边界

- browser→后端 全链（真实浏览器）未在本任务跑（无 REQUIRE_BROWSER 车道改动）；HTTP API+真实 Pi 链已覆盖；UI 组件为标准 React 且 typecheck 通过 —— 浏览器层标注未跑。
- compaction usage 的原生上报路径未在旅程触发（stub 不产生压缩）；coverage 面如实披露 not_included。
- 真实供应商账单不可得（无真实凭据）—— 价格事实只能来自 stub 回包/价格表，已按 provider/estimated/unknown 分层。
