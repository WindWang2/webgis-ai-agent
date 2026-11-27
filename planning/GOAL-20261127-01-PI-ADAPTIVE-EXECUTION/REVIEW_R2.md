# REVIEW_R2 — 复审（R1 修复后的再核）

> 复审人：主 Agent（对 R1 修复 diffs 重新逐行 + 关键 journey 重执行 +
> 冲突/基线复核）。R1 报告见 REVIEW_R1.md。

## R1 关闭证据复核

| R1 # | 关闭证据 |
|---|---|
| F1 (P1) | `session_model_views.py` 绑定校验 + `_VENDOR_STATS_FIELDS` 白名单；`test_runtime_vendor_stats_rejects_unbound_or_foreign_session`（foreign_bound/worker_busy 两例拒答）+ `test_runtime_vendor_stats_whitelisted_projection`（路径/id/未知键均剔除）全绿 |
| F2 (P2) | 泵去重分支 `_ar_kind not in ("rate_limit","provider_unavailable")`（流式 + 非流式两处同构）；`test_auto_retry_http_class_failure_not_double_counted` 锁定 total_failure 恰 1→恰 2 |
| F3 (P2) | 并入 F1 白名单；测试断言 `sessionFile/sessionId/unknown_vendor_key` 不出 API 面 |
| F4 (P2) | 恒真断言改为真实行为断言（`\u200b` 掺入 → retry_after=None，注释说明容忍理由） |
| F5 (P2) | 两处复位（isolate + #2213 换代）；`test_isolate_resets_confirmed_model_anchor` |
| F6-F9, F13 (P3) | 死参数删除 / 非流式 attempt 链同构 / 状态注释 / governor 一次性标志 / `_bounded_route_codes` 整条取舍——各自测试锁定 |
| 附加 | R1 期间引入的 `_bounded_route_codes` 缺 `self.` 缺陷：g37 settle 测试捕获 → 修复 → `test_stream_prompt_records_usage_on_settle` 回绿（88+26 项 affected 单测全绿） |

## 关键 journey 重执行（R1 修复后，串行）

- fixture 故障矩阵（真实 vendored RPC）：**5 passed（72.05s）**
- G37 全栈 journey（策略开启）：**1 passed（35.51s）**
- affected 单测：114 passed；bridge+governor：281 passed；Ruff（触及文件）零诊断

## 基线 / 冲突复核（R2 时点）

- `git fetch` 后 origin/master = `f34cb69dd`（无新合并）；分支为其后代 →
  无需 rebase；开放 PR = 0。
- `git diff --check` 干净；工作树无未提交 tracked 改动；vendor/pi gitlink
  对 origin/master 零 diff（pin 未动；工作树内未跟踪构建产物不入库）。
- 变更面：32 文件 +2834/−29；与 02–05 线无文件交集。

## R2 结论

R1 全部 P0/P1/P2 关闭（F10/F11 为记录性披露），无新增阻断；关键门在修复
后重跑全绿。可交付。
