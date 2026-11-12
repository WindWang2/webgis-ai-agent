# PR — G22 最新 Pi 与长会话 GIS 状态连续性

> 分支：`zcode/g22-pi-state-continuity-20261111T151408Z`（BASE `b8f2a6ecc`）
> 规划/证据：`planning/G22-PI-STATE-CONTINUITY/`（RECON/PLAN/INTERFACES/
> DUPLICATION_MATRIX/ACCEPTANCE/TEST_EVIDENCE/LOOP_LEDGER/REVIEW）
> **不合并**：提交后等待 review；未等线上 CI。

## 功能增量

1. **P0 — vendor pin v1.5.1 → v1.5.2**（pin-only；消费面 C1–C16 零漂移，
   矩阵 §4 正式四栏裁决 + §5 版本史；floor 保持 v1.5.1）。A01/A23。
2. **P1 — turn 交接门与取消隔离**（清偿 G12 review P2-1）：RPC 写/响应相位
   拆分（issue/collect）；abort 目标以 entry 对象同一性在门内「校验+写出」；
   登记变更同门序列化 → 同会话迟到 clear_queue/abort 不再可能命中后继 turn
   （A07/A09）；无目标清场保留且不再记空 turn id。A07–A09。
3. **P2 — 权威 GIS 状态恢复目录**：用户约束全层投影（workbench 锁/隐藏层/
   G16 标注覆写/G15 active frame/agent undo 栈）+ durable jobs 状态如实 +
   失效 source ref 披露；预算挤压时通用目录让位、约束永不消失（A13–A18）。
4. **P3 — 压缩/恢复对账**：A17 权威覆盖 summary 伪造；`--no-session` 契约
   钉死；A21 会话切换隔离；Redis active_turn 实例前缀 + lifespan 启动清扫
   （多 pod 不误杀）。
5. **P4 — 重连与副作用去重**：A19 幂等键跨 turn 稳定钉死（+既有纯重放/CAS
   引用）；A20 迟到回调 409 零执行。
6. **P5 — 观测**：N4 修复（J7 计数 separators + 反 vacuous 锁）。
7. **P6 — 恢复可理解性 UI**：恢复横幅（revision/失效引用/任务状态/人工
   约束保留计数；技术 ID 仅 title 诊断）+ 后端只读 recovery 摘要。
8. **P7 — 证据**：A24 soak（结构平坦性）、A22 浏览器恢复旅程、A23 回退
   演练、TEST_EVIDENCE 全录。
9. **解堵 master（独立 commit，可单独摘取）**：补齐 G10/G16/G17/G19 工具
   已声明但漏注册的 12 个 capability id 种子（strict manifest 启动
   fail-fast 解除——晨间合入批次集成破损，干净 BASE 复现）+ api_contract
   字段快照机械再生。

## 独立评审记录（review → fix → review → fix → 收窄确认）

- 第一轮：**BLOCK**（P1×2 + P2×1 + P3×4，全部带实证；见 REVIEW.md）。
  修复 commit「fix(g22): 独立评审修复轮」：标注 list 权威形状、打包器
  预算化、`_drain_control_rpcs` 落地、abort 门 fail-safe、F19/实例 id/
  断言强度、文档更正。
- 第二轮：P1×2/P2/P3×4 全部 FIXED；#2 打包器判 **PARTIAL**（重维度
  1638–1695 > 1600 残余）。修复 commit「fix(g22): 二轮复审残余清偿」：
  打包器 v2（预留披露 + 全局降级收缩，≤1600 构造性不变量 + 属性式
  2900+ 维度扫描）；drain 单次有界（双 finally 不叠加）；drain/fail-safe
  补测；ADR D1 残余段。
- 第三轮（收窄确认）：见 REVIEW.md「第三轮」节。
- 评审过程中由反 vacuous 锁/属性扫描暴露并修复的本方 bug 亦如实记录
  （N4 needle、扫描 body 排除、双 drain 叠加）。

## 与在途 PR 的关系（提交前 fetch 复核）

- origin/master 未动（BASE 仍 `b8f2a6ecc`），无需 re-merge。
- #2090（G28）也注册了 `j17-*` 旅程名并触 `chat-tab.tsx`/`sse.ts`/
  `registry.ts`/`test_pi_stub_llm_journeys.py`：本 PR 已将恢复连续性旅程
  更名为 `j22-restore-continuity`（见下「提交时处理」），其余为不同区域
  的 additive 补丁，预期无语义冲突、仅少量上下文行冲突。
- #2092（G25）触 `app/api/routes/chat.py`：本 PR 仅 `?recovery=1` 一个
  additive 附加块（query_params 原始读取 + JSONResponse 分支），与
  G25 的 schema 改动正交。

## 排除项（明确不做）

- 不追 upstream main；不发明 session_after_compact 等不存在的 hook。
- 不去掉 `--no-session`（两套持久会话竞争）；不建 fork 产品入口。
- 不接管 G23 job runner / G28 完成裁决 / R1 预算/记忆/安全合集 /
  goal-harness-recovery 恢复决策域。
- 不修 `test_response_model_coverage` 晨间端点 response_model 债
  （BASE 复现记录于 TEST_EVIDENCE；owner 方向清偿）。
- 不将 #2061 issue 状态改闭（修复复验证据已记录，关闭归 owner）。

## 证据（命令与结果见 TEST_EVIDENCE.md）

- pi_doctor 6 域 PASS + vendor 契约/golden 13 passed + stub-LLM 旅程
  J1–J8（v1.5.2）；v1.5.1 基线同套绿。
- 新增测试：turn 隔离 7 / 恢复目录 9 / 对账 6 / 重连去重 4 / soak 1 /
  恢复摘要+前端（P6）若干；mock 契约迁移 6 文件。
- 全量 `-k pi` 单元终态：1881+ passed（余 response_model 基线债，非本 PR）。

## 性能 / 兼容 / 回退

- 性能：交接门为每 turn 一次无竞争锁 + 每 abort 一次，微秒级；soak 平坦性
  证明无结构增长。pi_doctor rpc 域时延无回归。
- 兼容：`request()` 语义不变；Redis active_turn 新值格式对旧读者兼容
  （turn 段解析）；mock 客户端需补 issue/collect（迁移已含）。
- 回退（A23 演练）：gitlink → `dd30101c` + `build-vendor-pi.sh --reinstall`
  + doctor/契约复跑；floor 不动。G22-P1 门为纯宿主逻辑，kill 开关不需要
  （行为只收紧不放宽）。

## 外部依赖 / 未测项

- 浏览器 E2E 依赖本机 Playwright（A22 旅程；不可用则如实留 blocked）。
- 未测项：真实商业模型 smoke（无凭据，按任务书不请求）。

## 残余风险

- 迟到取消极端场景：①wedged 控制写持门 >5s → 后续 abort **整体跳过**
  （fail-safe，绝不冒险命中后继 turn）；②vendor rpc 模式 stdin 并发分发
  ——门保证字节序，teardown 另有界等待 abort **响应**（5s）压缩 vendor 内
  竞态窗口，响应超时残余记录于 ADR D1。
- capability 种子为最小事实描述，owner 方向后续可迁移各自域模块。
