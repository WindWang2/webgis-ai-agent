# 安全与执行策略

## Tier-3 / 破坏性红线（§38）

闸点唯一：`ToolRegistry._dispatch_impl` 的 ContextVar 检查
（`confirm_tier3()` 上下文管理器是唯一授权方式，SEC-F1）。已钉住的绕过面：

| 通道 | 防线 | 测试 |
|---|---|---|
| 工具名别名 | dispatch 首步折叠 canonical 名，闸在折叠之后 | `test_alias_cannot_bypass_tier3_gate_async` |
| Pi bridge | `dispatch_tool` 预闸 tier≥3 硬拒（belt）+ registry 闸（suspenders） | 既有 `test_pi_*` |
| 子代理 | `select_tools_for_subagent` tier-3 永远排除（`extra_tools` 也不行，SEC-F2）+ 冻结目录无检索扩权 | `test_subagent_and_replay_red_lines_remain` |
| 重放 harness | `replay_tools` 对 destructive/external 永不自动执行 | `test_replay_never_runs_destructive` |
| 管理员 API | `require_admin` + `confirm_destructive` → `confirm_tier3()` | 既有 route 测试 |
| 工作流 | 无 confirm 上下文 → registry 闸拒绝 | 既有 |

子代理角色库（Wave 7）叠加：`allow_mutation=False` 的角色按**描述符副作用类**
剔除 state_mutation / artifact_creation / external_side_effect / destructive 工具。

## 注入边界（§39）

- provider 错误体 → `sanitize_provider_error`（控制字符 / 伪 XML 围栏标签剥离、
  有界化）后才可回注模型；
- 上下文注入的不可信字段沿用既有 XML 围栏（`context/formatters.py`）；
- trace 元数据键敏感词 redaction + 值有界化（`bound_meta`）。

## 载荷安全（§40）

- NaN/±Infinity：dispatch 期预算化扫描实参树，命中即
  `VALIDATION_ERROR`（Python json.loads 默认接受，下游必炸 —— 在注入点拒绝）；
- 递归/巨型 JSON：既有 `_ESTIMATE_MAX_NODES` 预算化遍历 + oversized 旁路 +
  GeoJSON 浅层校验门（不变）；
- ref 解引用：白名单 skip_keys + 声明式 `ref_cursor` extra（不变）。

## 执行策略审计（§25）

`app/tools/policy_audit.py`：

- 注册期（register 内）：`sync_declared_async`（warning，元数据失真）、
  `heavy_inline`（**error** —— INLINE 在事件循环执行且不可抢占）、
  `inline_timeout_ineffective` / `heavy_no_explicit_timeout`（info）；
- `audit_registry_policies(registry)` 全库扫描；契约测试对活注册表钉
  **零 error 发现**（`test_live_registry_policy_scan_clean`）。

## 取消 / 泄漏语义（§26）

- to_thread worker 不可终止（既有约束）：预算放弃后线程跑完 →
  `_tool_thread_leaked_count` 计数、信号量由 done 回调在线程真实结束时归还；
- **修复**：`_tool_thread_semaphore` 原为模块级单例，绑定首个事件循环 ——
  循环轮转后所有 THREAD 工具抛 `bound to a different event loop`（竞态测试
  捕获的真实缺陷）。现按运行循环惰性重建（LLMHttpClientRegistry 同款先例），
  测试 monkeypatch seam 保留；
- 竞态测试：取消风暴下泄漏计数单调 + 信号量不变量 + 并行结果独立性。
