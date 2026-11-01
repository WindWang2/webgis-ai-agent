# EVIDENCE — GOAL 05

基线 origin/master `6c5e71c2c`；worktree `zcode/goal-gis-harness-recovery-v1`。

## 交付物（commit 序列）

| commit | 内容 |
|---|---|
| d304f9675 | M0 基线勘察（BASELINE/PR_OVERLAP/TASK_PLAN/DECISIONS） |
| a3a4940c6 | M1 GISRecoveryDiagnostic.v1（28 reason 码 + 六处置 + 五族诊断构建器） |
| 0b5a8b014 | M2 RecoveryPolicy 纯决策（风险×处置×预算副作用守卫 + ToolRiskProfile 复用 SideEffectClass） |
| 4f560d04e | M3 接线（TDS recovery 键 + LLM note 双引擎同源 + step_error SSE + last_recovery + [GIS Recovery] 投影 + REPAIR 阶段 + 存量键名 bug 修复） |
| 4dc703055 | M4 GoalVerification 对账层（8 slot×4 态 + false-PASS 守门 + user-wins） |
| f35c1ffb0 | M5 RetryDirective（选择性重试 + fail-closed） |
| c2bd3cb50 | M6 四段解释 + 四类处境 |
| 6baecf2f3 | M7 恢复语料 29 案例×21 类别×三 oracle 族 |
| 321325c06 | M8 性能/安全回归 + last_recovery 形状门 |
| dd70dd657 | M9 E2E 5 场景 + 三处真实缺陷修复 |
| 19c97a2e1 | chore: 未用 import |

## 测试证据（命令与结果）

命令基式：`.venv/bin/python -m pytest <path> -q --no-header --no-cov`

| 套件 | 结果 |
|---|---|
| tests/unit/gis_harness/recovery_policy/（全部 6 文件） | **185 passed**（含 29 语料案例参数化） |
| tests/unit/lib/test_goal_verification_reconcile.py | **19 passed** |
| tests/unit/evaluation/test_recovery_corpus.py | 含于上（36 项参数化+门） |
| M8 双跑门（性能/安全） | **两次均 10 passed**（中位<2ms/p95<8ms/有界/零泄漏） |
| 存量回归 tests/unit/gis_harness（全目录） | 2947 passed / 4 failed —— **4 失败为基线存量**（test_component_composition×2 + test_semantic_v2_families×1 + test_workflow_guards×1，git stash 复现核对） |
| 存量回归 tests/cartography + goal_satisfaction 族 | 3076 passed / 1 failed（mutation_parity refresh_data_revision_stamp —— 基线存量，stash 复现核对） |
| E2E 门 | 5/5（3 恢复成功 + 2 安全终止，真实 ToolDispatchService seam） |

## 顺带修复的既有缺陷（E2E 门暴露，均有失败测试先证）

1. **粗分类器 429/5xx 错预算**（failure_taxonomy.py `_TIMEOUT_MARKERS`）：5xx 消息落 `unknown`（max=1）、429 经 planning 委托折 `budget_exhausted`（max=0，首错即 abort）——与"瞬态可退避"语义相悖；并入 timeout 词汇（11 类词表未扩）。
2. **TDS 记忆候选读错键**（tool_dispatch_service.py:1083）：`_hf.get("failure_class")` 但 payload 键为 `class` → 恒记 unknown。
3. **continuation 回显他会话 ID**：fail-closed 理由原文含 owner session 前缀（跨租户泄漏面）→ 移除，只说 "belongs to another session"。
4. **policy 预算门误终结用户路径**：预算耗尽在处置分支之前 → needs_user_data/approval 被 abort_with_disclosure 吞掉；改为只钳制 retryable 处置。

## 性能基线（M8，确定性双跑）

- 诊断+裁决端到端：median < 2ms、p95 < 8ms（200 次采样）
- recovery payload 全量序列化 < 2.5KB；LLM note ≤ 240 字符（上下文预算增量有界）
- 重复裁决零状态增长（50 次输出尺寸漂移 < 8 字节）

## 安全证据

- 跨会话 ID 零泄漏：E2E STOP-1 + M8 test_cross_session_id_never_in_note
- 签名面无 map_state/身份参数：inspect.signature 断言（#1843 类边界不复制）
- 伪造 reason 拒绝落账：M8 test_forged_recovery_reason_rejected（封闭词表形状门）
- 破坏性/付费/导出/写永不自动重试：策略矩阵 + corpus forbidden + E2E STOP-2 三层

## 未验证项（诚实披露）

- 真实远端 429/5xx 集成（需外网）：以确定性 fixture 覆盖，未对真实 provider 验证。
- Pi 子进程端到端（需 node/pi runtime）：Pi 路径经 TDS 同源 llm_payload/raw_result 覆盖，未起真实 Pi 进程。
- 前端证据卡渲染：SSE 字段 additive 下行已测，前端 UI 消费未改（按 M6 边界不新建控制台）。
