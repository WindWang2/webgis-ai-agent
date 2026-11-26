# WP1 能力消费台账（v1.6.1 @ ef4a1cfc，代码实证 2026-11-27）

> 判定来源：三个只读探查子代理逐行报告 + 宿主 grep 零命中证据 + vendor 源码/docs。
> 状态词表：CONSUMED / PARTIAL / NOT-CONSUMED(可用) / UNREACHABLE / RISKY / NO-NEED。

| # | 上游能力 (v1.6.x) | 状态 | 证据（file:line） | 本线处置 |
|---|---|---|---|---|
| 1 | `models.classify()`（decision model，codemode-only） | NOT-CONSUMED(可用) | app/ 零调用；`DECISION_MODEL_CLASSIFY` 仅诊断词表 `pi_compat.py:150,168`；生成的 `models.json` 无 classifier 条目 `pi_rpc_client.py:642-663`；settings.json 无 defaultTools `:664-672`；vendor `docs/models.md:178` 说明唯一可达路径=codemode+已配置 classifier | **WP2**：opt-in 有界决策（classifier 配置注入 + 扩展 codemode 适配 + 宿主确定性闸） |
| 2 | `responseId` 传播 | NOT-CONSUMED(可用) | app/ grep 零命中；usage 收集器只看 message_end | WP2 决策输出子集携带（可验证性） |
| 3 | dialog `blockedKind` | UNREACHABLE(RPC) | TUI terminal program-status（vendor extensions.md:2893-2989 区段）；app/+extension 零命中；RPC headless 无 terminal | **WP4**：不假冒；Web 保留显式确认协议；审计三态→既有结构映射并补负例 |
| 4 | `notices.*` 设置 | NO-NEED(TUI) | vendor settings.md:20-23 = TUI display toggles；宿主 settings.json 只写 defaultProvider/defaultModel `pi_rpc_client.py:664-672` | 不接入（记录理由） |
| 5 | provider 429/retry（auto_retry 事件 + after_provider_response） | CONSUMED(观测) | `pi_event_mapper.py:446-496` → SSE notes + typed 诊断；extension `index.mjs:617-632` stderr → `PiStderrDiagnostics` `pi_rpc_client.py:277-312` | **WP5**：接入可操作用户级原因/恢复提示 + 预算/重试关联（复用既有诊断，不建新日志系统） |
| 6 | compaction（session_before_compact/failed 钩子 + compact RPC） | PARTIAL | mapper `:499-510` SSE notes；extension before_compact 返回 `{}`（不注入 summary）`index.mjs:586-603`；宿主从不主动触发 compact RPC；权威重建=GIS_STATE_REBUILD `providers.py:601-720`；C23 wire 契约有专测 | **WP4**：压缩前后义务/digest 有限重建验证 + 长对话压缩旅程（stub）；评估宿主是否需要安全触发 compact（保守：不新增触发面，除非旅程证明必要） |
| 7 | codemode（QuickJS 沙箱） | CONSUMED | extension alwaysActive `index.mjs:200-208,430-437`；嵌套车道契约 `test_pi_vendor_contract.py:207-210`；合法性闸 `pi_bridge_surface_gate.py:98-102` | WP2 复用为 classify 执行通道 |
| 8 | tool_search（BM25+分词+$ref 解析） | CONSUMED | dump v4 deferred 长尾 `pi_native_surface.py:477-483`；merge 语义 `pi_surface_receipt.py:291`；契约车道 `test_pi_vendor_contract.py:891-955` | WP3 复核嵌套 ID/取消/错误证据 |
| 9 | `--no-builtin-tools` 最小权限 | CONSUMED | spawn args `pi_rpc_client.py:696-699`；契约 pin `test_pi_vendor_contract.py:287`；负例 `test_pi_domain_tool_conformance.py:140-142` | WP1 保留证据即可 |
| 10 | `extension_error` 事件（v1.6.0） | CONSUMED(观测) | mapper `:518-556` 记录不进 SSE；`test_pi_v16x_diagnostics.py:31-59` | 无增量 |
| 11 | abort 批量终态（C17） | CONSUMED(隐式) | 诚实 step_error fallback `pi_event_mapper.py:361-385`；单测 `:346` | 无增量 |
| 12 | `entry_appended` 事件（C21） | NO-NEED | mapper 封闭词表静默 `:151` | 无增量 |
| 13 | `takeUnreportedDiagnostics`（SDK-host API） | UNREACHABLE(RPC) | grep 零命中；C18 记录不经 RPC | 无增量（stderr 环已覆盖） |
| 14 | socket 重试（ECONNRESET 等，C20） | CONSUMED(上游内) | vendor 内部；宿主不重复实现 | 无增量 |

## 结论

- 本线可产品化的真实缺口：#1+#2（决策）、#5（失败可观测产品化）、#6（压缩治理
  验证）、#3（阻塞语义审计+负例，不新建协议）。
- schema 线（WP3，独立于能力表）：C20 两钉子可在单一来源修复
  （`query_local_poi` 无 args_model → 补；`webgis_component_update`
  `Optional[Dict]` → 结构化）；strict 分类机制不存在（待建）。
