# G43 LOOP_LEDGER

## 轮次记录

### L1（2026-11-15，主实现轮）
- base 655f769ca（G40 已合并）；worktree zcode/g43-turn-context-control-20261115T124713Z。
- WP01-WP10 全量实现：source_policy / receipt v2 / turn_context_store（两表+迁移）/
  provider 过滤（单渲染源）/ 预拼块 typed 拒绝 / preview 只读投影 / bridge 双路径
  receipt 生命周期 + SSE / chat 路由消息关联 / REST 六端点 / @tool / 前端面板+工作台+i18n。
- 测试：U/DB/API/FE 全绿（48 backend + 9 FE）；ruff（本域文件 0 错误）；
  tsc --noEmit 0 本域错误（cartography-result-card 2 处为基线既有，#2123）。
- **真实 Pi 旅程 J1–J5 全绿**（stub LLM 供应商边界记录完整 prompt）：
  J1 auto 装配回执（含真实 version_token）+ 边界含双条目；
  J2 排除 2024/优先 2025 → 回执分列 + 边界缺席证据；policy_version 冻结；
  J3 tool_search→webgis_turn_context_receipt 真实 dispatch（step_result 含回执）；
  J4 token cap=1 → not_sent(model_budget_rejected) + stub 零请求；
  J5 非流式同语义（turn_receipt 披露 + 同策略源去向一致）+ 历史消息 context_receipt 去重。
- 迁移：空库 → head ✓；g40 head → g43 head ✓（两新表 + messages.context_receipt 列）。
- vendor/pi 从本地 module store 物化（同 pin b4afdddf）+ build-vendor-pi.sh 构建 ✓。
- 旅程环境注：USE_REDIS=false（Redis 16379 不可用；rate limiter/PiTurnRegistry 官方回退路径）。

### 决定与偏差
- J1 stub 边界断言 any()：同 key 第二请求是标题生成旁路调用（仅原始消息）。
- J3 经 tool_search 发现链（tier-2 动态面生产真实路径；直调/proxy 均被面闸正确拒绝）。
- 前端 Playwright 未运行（资源约束）——如实记录，见 TEST_MATRIX 未测面。

## 下一步
- commit → 独立 review（未参与实现的 subagent）→ 修复 → 复审 → push → PR。
