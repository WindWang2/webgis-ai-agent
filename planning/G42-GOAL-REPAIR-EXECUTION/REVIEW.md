# G42 REVIEW — 独立评审与修复记录

## 第 1 轮（独立 reviewer，未参与实现；base..工作树全量 diff）

结论：**BLOCKING 2（P1×2）**，P2×5，P3×4；同时独立复核确认：4 个「既有生产 bug」
全部在 base 上真实存在（evaluator 缺 failed_rule、`Query[:N].all()` AttributeError
（2.1.8 实测复现）→ 交付证据族恒空、导出工具路径 lineage fail-closed、异步引擎缺
busy timeout）；REST 租户隔离/确认闸/预算不双扣/R37 零等待/goal 完整性/spec-skip
无破坏等重点面复核无缺陷；测试诚实性复核通过（R09 真实 G18 零提交、R11 三层单
claim、R37 旅程钉死）。

| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| 1 | P1 | `dispatch_spec` 落**原始 owner_token**（平台不变量：仅 SHA-256 摘要入库；G30 先例刻意省略） | `store._write_dispatch_spec` 剔除 owner_token；对账 actor 改按 Conversation 行权威派生（`_conversation_user_id`），匿名会话维持诚实等待披露 |
| 2 | P1 | Pi 工具 cancel **先执行后验归属**（跨租户可取消他方活跃 attempt；attempt_id 小整数可枚举） | `webgis_goal_repair_cancel` 归属校验前移到副作用之前 |
| 3 | P2 | status 工具的惰性对账在他方会话上先写后拒 | `webgis_goal_repair_status` 同款前移（零跨会话副作用） |
| 4 | P2 | `RUNNER_LOST_GRACE_S=300s` < 单 attempt 最坏时长（6×120s 等待）；跨 worker 会把活跃 attempt 误判 runner-lost | 宽限改 900s 且依据 `heartbeat_at`（看门狗持续刷新；缺省回退 created_at） |
| 5 | P2 | evaluator wrong-kind 分支（有地图回执但要求交付 / 反之）仍无 failed_rule → 常见形态无补全入口 | 两分支补设 `R_DELIVERY_RECEIPT_ABSENT`/`R_EXPORT_ABSENT` |
| 6 | P2 | receipts 修复把查询挪出 `with` 块（依赖会话 close 后可复用的偶然行为） | 重新缩进回 `with` 内（`.limit(N)` 修正保留） |
| 7 | P2 | waiting_delivery 惰性收口为 succeeded 时不计预算（慢交付可重复绕过） | 收口成功时 `loop_charge(session, "repair")` |
| 8 | P3 | 多进程 start 竞态可覆写活跃 attempt 的 dispatch_spec 快照（DB claim 仍防双执行） | 记录为已知边界（单进程部署面不触发；PR 说明披露） |
| 9 | P3 | `_next_retry_no` LIKE 未转义 `%`/`_`；幂等键 255 截断碰撞 | LIKE 已转义（escape="\\"）；截断碰撞需对抗性系统生成 id，记录为已知边界 |
| 10 | P3 | store 同步 IO 在事件循环；每历史卡各挂面板轮询；dispatch repeated 语义陷阱；repair「无可应用」披露措辞 | 记录为已知边界/后续打磨（不改行为） |

## 修复后新增验证

- 单测 25 passed；受影响既有回归 124 passed（goal_satisfaction ×4 套件 / delivery /
  tool_registry；仅剩已知 venv pyarrow 环境失败 `test_fabric_parquet_fast_path`）。
- Pi 旅程复跑通过 —— 期间发现并修复第 6 个真实缺陷：**attempt 执行体与原 turn 的
  settle 效果链并发写竞争**（sqlite 上退化为多写者锁排队；语义上 attempt 重终验也不
  应与原 turn settle 终验交错）。修复：Pi 回调启动的 attempt 先有界（120s）等待当前
  turn settle 再执行动作；旅程驱动同时改为每运行独立 sqlite 文件（跨运行累积态隔离）。
  修复后旅程 31.9s 通过（此前锁排队时 270s+）。
- 浏览器 j42×2 + j17 回归 3 passed。

## 第 2 轮（复审）—— **REREVIEW: PASS**

同一独立 reviewer 逐项复核（并实跑单测 34+35 与 evaluator 回归 47 项）：

- Fix 1–7、9 全部 OK（含：dispatch_spec 无任何 owner_token 消费方残留；匿名会话
  交付动作转为诚实 BLOCKED = fail-closed，符合「匿名证据面本就不可收集」的既有
  授权面；grace 的 ISO 回退方向安全；LIKE 转义顺序正确）。
- 新增 settle 等待逐条对照 G42 约束判定合法（零等待 start 保持 —— 等待在游离
  runner 任务内、不持任何 wave/writer/锁、无递归 prompt、预算不动、心跳持续），
  「合法时序修复而非掩盖」。
- 测试诚实性复核：GOAL_SATISFIED 拒绝与 verdict 翻转为真实端到端断言；回归即红。
- 两条非阻塞 nit 已采纳修复：settle 等待循环补 `handle.checkpoint()`（取消即时
  生效）；wrong-kind 分支补测试钉（`test_g42_wrong_kind_receipt_rules_generate_plan_actions`）。
- 结论：**无阻塞项；修复未引入新缺陷。**
