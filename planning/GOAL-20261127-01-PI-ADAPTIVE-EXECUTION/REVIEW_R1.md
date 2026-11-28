# REVIEW_R1 — GOAL-20261127-01 独立审查（第一轮）

> 审查者：独立子代理（未参与本分支任何代码编写），只读逐行 + vendor 源码
> 交叉核查。范围 `git diff origin/master..HEAD`（@ 6d4577849）。
> 处置纪律：P0/P1 必修；P2 必修或如实记录理由；P3 择情修 + 台账记录。

## 发现与处置

| # | 级别 | 摘要 | 处置 |
|---|---|---|---|
| F1 | P1 | `GET /runtime` vendor stats 无绑定校验：RPC 无 session 参数，共享 worker 上可返回**他人会话**的用量/路径/内部 id | ✅ 已修：RPC 前校验 `_bound_gis_session_id == session_id` 且 `_active_turn_sid ∈ {None, session_id}`，否则 `session_not_bound`/`worker_busy` 拒答；白名单投影（userMessages/assistantMessages/toolCalls/toolResults/totalMessages/tokens/cost/contextUsage）；负例测试 2 项（unbound/busy/白名单剔除） |
| F2 | P2 | provider 失败**双通道双计**健康惩罚（stderr obs + auto_retry 各记一次 → 2 次真实失败即开冷却） | ✅ 已修：auto_retry 兜底通道跳过 HTTP 状态类（rate_limit/provider_unavailable，由 stderr obs 精确记），仅补非 HTTP 类（断流/超时）；回归测试锁定 total_failure 恰 1/恰 2 语义 |
| F3 | P2 | vendor stats 原样透传 `sessionFile`（服务器绝对路径）+ `sessionId` | ✅ 已修（并入 F1 白名单）；测试断言两键不出 API 面 |
| F4 | P2 | 安全负例测试存在恒真断言（`is None or True`） | ✅ 已修：改为按真实行为断言（零宽字符掺入 → `{"status":429,"retry_after":None}`，\u200b 非 \s 不匹配 retry_after 字段；行来源为宿主自有扩展+整数化值，容忍非注入面，已注释说明）；白名单 forbidden 集扩充路径/session 键 + 新增 vendor stats 白名单测试 |
| F5 | P2 | `_last_confirmed_model` 在 new_session/图片换代后不复位 → 观测错挂旧模型 | ✅ 已修：`_isolate_vendor_session` 与 #2213 换代路径同步复位（回落 spawn 基模型归属）；回归测试锁定复位 |
| F6 | P3-1 | `resolve_inherit_target(est_output_tokens=…)` 死参数 | ✅ 已删 |
| F7 | P3-2 | 非流式 turn 的 attempt 链恒 0/空 | ✅ 已修：非流式 drain 泵补同构计数/失败分类/成功回写（去重语义与流式一致）+ persist 元数据 + 生效模型锚 |
| F8 | P3-3 | `ModelApplicationResult.status` 注释缺新词表 | ✅ 已补全（含 degraded_inherit/inherit_routed） |
| F9 | P3-4 | governor 回流非幂等 vs persist 幂等契约 | ✅ 已修：collector 增 `governor_feedback_done` 一次性标志（重复 finalize 不双计） |
| F10 | P3-5 | 健康快照进程级跨租户计数暴露 | 📝 记录为披露取舍：白名单字段均为进程级运营元数据（无内容/无用户数据）；PR 正文披露；如产品要求可按会话用到的模型过滤（后续增量） |
| F11 | P3-6 | /runtime 首调可能触发池 spawn；3s 超时取消语义干净 | 📝 仅提示，无需改（R1 自证安全） |
| F12 | P3-7 | TEST_MATRIX journey 行未回填 | ✅ 已回填（本轮） |
| F13 | P3-8 | route codes 255 硬截可腰斩 | ✅ 已修：`_bounded_route_codes` 整条取舍再 join（列界内保留前缀整码）；测试锁定无残码 |

## R1 期间主 Agent 并行发现并修复

- **persist 静态方法调用缺陷**（R1 修复引入后被既有测试捕获）：`_bounded_route_codes`
  在 `_persist_turn_runtime_receipt` 内未加 `self.` → NameError 被增值面
  except 吞掉 → 账本行缺失。`test_stream_prompt_records_usage_on_settle`
  红 → 定位 → 修复 → 绿。（测试链价值实证：g37 settle 断言抓住了单测盲区。）

## Journey「回归」归因澄清（非代码回归）

- 现象：G37 全栈 journey 在分支上 J1/J3 失败（`Model not found:
  webgis/g37-a`、effective=`stub-model`），master 上同测通过。
- 根因：**主 Agent 测试隔离违规** —— 同一 worktree 同时后台跑 G37 journey
  与 stub-LLM journeys，两者共用 `REPO_ROOT/.pi/agent/models.json`
  （stub journeys 的 LLM_MODEL=stub-model，:452），互相覆盖 spawn 目录。
- 证明：干净 master（临时 detached worktree）单跑 PASS；分支 + 策略开关
  关闭单跑 PASS；**分支 + 策略开启单独重跑 PASS（35.03s）** —— 策略接线
  在真实全栈链路无回归。串行跑 journey 为后续纪律（TEST_MATRIX 记录）。

## 通过项确认（R1 原文要点）

- ChatEngine 零行为变化成立（candidate_models 缺省路径逐字等价、无其他调用方）；
  explicit 路径 typed 拒绝语义原样 + 对抗测试在位。
- `inherit_routed` 五处一致（模型约束/迁移/投影/前端双语/downgrade 归并）。
- `record_output_tokens` 求和 vs keep-latest 分叉有据且正确；陈旧 governor
  测试对齐属修正基线红测非弱化。
- stderr 读循环线程安全（health 全程持锁、sink 无 IO、读循环兜底）；RPC
  超时取消语义干净（late response 安全吞掉）。
- 迁移 additive + server_default + batch 兼容双库 + 单 head。
- provider-obs 内容仅整数化 status/retry_after，双侧边界钉死。
