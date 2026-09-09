# 10 — Review Findings（Review Round 1/2 记录）

> 开发主体完成后填写。Lens A：GIS Harness architecture / workflow correctness / state machine / partial recompute / artifact lineage。Lens B：cartography / render observation / visual quality / frontend-runtime / UX。
> 每条：severity（BLOCKER/CRITICAL/MAJOR/MINOR）+ 位置 + 描述 + 处置 + 验证。

## Round 1（2026-09-09，Lens A/B 双路，结论：打回→修复→177 passed 全绿）

Lens A（架构/正确性）：MAJOR 3 —— plan_repairs 满 6 actions 后 break 吞 refused（改仅停追加，分类继续）；verify-stale 只翻 stages 执行侧不可见（resumed verify.stale_nodes 进 analysis 硬输入＋行缺席 resumed 章节终验显式 pending）；降级分支 repair_class 跨词表混装 retry/replan（归一化 16 类＋detail 留痕＋输出侧断言）。MINOR 4、QUESTION 3（bridge 截断 400 有意设计已注释＋同族测试；package 非三元组确定产物不进门；零证据一律 unknown）。

Lens B（制图/观测/前端）：CRITICAL 1 —— quality_loop 与 lib runtime_repair 精确匹配绕过层族语义（改复用 is_entity_locked＋族变体用例）。MAJOR 4 —— MapSpecResult 缺机器码（加 error_code 精确透出 layer_locked 单码，组件锁复用单码＋载荷，前端不动）；观测“实测 rect”实为提交态 placement 投影（文档诚实化＋坐标系钉死）；overlap 只指 pair 首方（改每对双 finding 互指，原子截断）；V6 三块无总预算准入（truncated 非空可观测 warning，零行为变化）。MINOR 6（视觉未接线标注、env 实时读、锁拒绝 schema 差异记录、verdict 复述互锁、floats 切片、rect 校验＋tie 全序）、QUESTION 5（turn/组装互斥已注释；batch 逐 intent 记录；余为有意设计确认）。

遗留 follow-up（非 blocking，另起任务）：chat.py 观测持久化丢 canvas（offscreen 生产恒缺证据）；MapSpecBatchResult 未加 error_code；derive_runtime_block“memo”表述与实现不符。

## Round 2（2026-09-10，上半 PASS＋下半 2 MAJOR 已修→150 passed 全绿）

上半（性能/资源/并发/兼容）：PASS，无 BLOCKER/CRITICAL。有界性 11 项全成立（derive 窗口 64/边 128、artifact_index 96、lineage 8、floats 32、v6 块 2048/4096/2048、语义索引 512、findings 24、repair 账本 32、anchor 128、visual 默认关）；兼容 5 项全 ✅（anchor v2 纯 additive、lockedComponentIds 缺席空、新字段零漂移、resumed pending 不污染旧流程、熔断）；MINOR 3（ledger RMW 漂移注记、语义 opt-in 阻塞注记、env 快照→实时读）＋QUESTION 3 均已处置。

下半（安全/隔离/隐私/seam）：MAJOR 2 已修 —— canvas 持久化丢失（observation 落库加有界 canvas＋DTO 归一＋键集合测试，offscreen 生产复活）；error_code 三条 egress 只通一条（store adapter 转发＋BatchIntentOutcome 加码进 to_dict）。QUESTION 4（跨 user 共享 anchor 不在 roadmap／恢复无行不变式钉死／匿名可建不可恢复既定语义／顺序 IO 有界）全部注释＋测试锁定。MINOR 2（canvas DTO 无界、anchor 无 TTL）随 MAJOR-1 修其一、其一另起任务。

## Claim Honesty（§54，2026-09-10，逐项 production-wired 核查）

- production wired：runtime bridge 三触发点、lineage 索引、recompute 闭包、UnifiedFindings/终验、repair planner＋防循环、lock guard 全路径、resume verify、V6 三块、canvas 持久化＋offscreen、error_code 透出。
- opt-in（默认关闭，显式 env 开启）：语义检索（TOOL_RETRIEVAL_SEMANTIC 注入，无静态引用是设计）、visual evaluator hook（GIS_VISUAL_EVALUATOR）。
- planned：visual evaluator 生产接线（seam 无调用方，m1 已标注，独立 roadmap）。
- limitation：观测 rect 系提交态 placement 投影（非 DOM 实测，M2 已诚实化）；anchor 无 TTL/GC（MINOR-2 另起任务）；lockedComponentIds 前端未持久化（后端兼容缺席=空）；匿名锚可建不可恢复（既定语义已注释）。
- degraded：无（未达标的能力均标 planned/limitation，未宣传 native）。

## 开发期已发现并已处理

| 日期 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| 2026-09-09 | 环境 | `~/.kimi-code/config.toml`（仓库外） | subagent 通道 opencode-zen 缺 x-opencode-session 头被 400 拒；muse-spark 不支持 effort=max | 补 custom_headers + support_efforts/default_effort + secondary_model.default_effort=high（已验证可用） |
