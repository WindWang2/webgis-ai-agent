# PROGRESS — GOAL-20261127-01

> 每轮迭代追加：轮次 | 动作 | 验证结果 | 通过/未通过 | 下一步（goal-loop 纪律）

## 轮 1（2026-11-27）：启动审计 + worktree + 权威梳理

- git fetch/rev-parse：origin/master=f34cb69dd（=参考快照）；vendor/pi=ef4a1cfc（=上游最新 tag v1.6.1，ls-remote 实证）✅
- 开放 PR=0、开放 issue=0；#2272–#2289 全部已合并 ✅
- worktree+分支创建：/home/kevin/projects/webgis/webgis-pi-adaptive-execution-20261127 ✅
- vendor/pi 子模块 init @ v1.6.1 ✅
- 自读权威文件：pi_compat.py、model_routing_bridge.py、routing.py、usage_ledger.py、
  session_model_service.py（前 160 行）、session_budget.py、dispatch_adapter.py、
  pi_event_mapper.py:400-530、index.mjs:545-655、agent_pi_bridge.py:3340-3520、
  model_runtime/health.py 头、provider_health.py 头、pi_surface_metrics/receipt 头、
  pi_durable_cancel.py ✅
- 核心缺口定位（PLAN.md「已实证的核心缺口」G1–G4）✅
- 2 个 Explore 子代理在跑：A=调用图+vendor 事件语义；B=路由/治理权威消费矩阵
- 下一步：等子代理报告 → DEDUP_MATRIX 定稿 → S2 实现开写

## 轮 2（2026-11-27）：WP1 定稿 + WP2/WP3/WP4/WP5 实现

- 两个 Explore 子代理报告落稿 → DEDUP_MATRIX 定稿（B1–B10 缺口）✅
- S2a+S2b 提交 b87030971：candidate_models 候选池 + turn_strategy +
  session_model_service 接线（inherit_routed 状态、strategy_codes 有界
  SSE）；单测 11 项 + G37 回归 49 passed ✅
- S2c 提交 62c5502c0：provider-obs 结构化 stderr 通道（extension/
  parse/sink）+ 桥接健康表回写 + auto_retry 兜底 + 真实 RPC fixture 门
  F1/F2（四链证据）✅ vendor contract 18 passed ✅ 相关单测 155 passed ✅
- S3 提交 2327fc88e：migration g5201（单 head）+ attempt 链三列 +
  governor record_output_tokens 真实 usage 回流（B4）+ budget 投影修复
  （B6）+ 陈旧 governor 测试对齐；单测 30 passed ✅
- S4+S5a 提交 1ffde6470：GET /sessions/{id}/runtime 聚合投影（健康白名单
  + vendor session_stats 对账 D6）+ 前端预算/运行事实/双语 + 故障矩阵
  F3 500/F4 401/F5 stream-cut ✅ tsc/eslint 干净 ✅
- WP5 压测：pool soak 2 workers/4 sessions/40 turns 不变量 PASS
  （0 泄漏/0 越序，p50=53ms p95=88ms）；governor 全包 209 passed ✅
- 安全负例 5 项 passed（解析注入/有界洪水/列宽/白名单）✅

## 轮 3（2026-11-28）：归因 + R1/R2 + 终验交付

- master 预存缺陷定性：app 启动 strict manifest 失败（14 dangling
  capability_id，#2288 整合遗漏；模块与 master 零 diff 证明预存）→
  不扩线，journey 以文档化开关降级运行 + PR 披露
- G37 journey「回归」归因：**主 Agent 并发跑两个 journey 的隔离违规**
  （共享 .pi/agent/models.json）——三向对照证明无代码回归
  （分支策略开启 35.03s / 关闭 37.52s / 干净 master 39.39s，全 PASS）
- R1 独立审查（子代理，非作者）：1×P1 + 4×P2 + 8×P3 → 10 项修复 +
  3 项记录性披露（REVIEW_R1.md）；R1 期间引入的 self. 缺陷被既有
  g37 settle 测试捕获并修复（commit 30c14613e）
- R2 复审：R1 关闭证据逐项复核 + 关键门修复后重跑（fixture 5 passed /
  G37 journey 35.51s PASS / affected 114 + bridge+governor 281 passed）
  + 基线冲突复核（master 未动、分支为后代、0 开放 PR、diff --check 干净、
  vendor pin 零 diff）→ REVIEW_R2.md 结论：可交付
- 交付：**PR #2290 已创建**（base master，head zcode/goal-pi-adaptive-execution-20261127，正文含全部要求项）；临时 detached 基线 worktree 已清理

