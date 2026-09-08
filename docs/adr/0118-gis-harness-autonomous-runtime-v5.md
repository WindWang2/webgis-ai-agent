# 0118. GIS Harness Autonomous Runtime V5 — 可恢复、可诊断、渲染可证

**Date:** 2026-09-09
**Status:** Proposed
**Branch:** `feat/harness-v5-autonomous-runtime`
**Baselines:** ADR-0104（Harness V4，PR #1156）· 审计 `.agent-work/harness-v5/00-baseline.md`

## Context

V4 已交付 runtime projection / context policy / subagent team / 观察回路 /
18 阶段链骨架，但 Phase-0 审计（file:line 证据见 baseline）确认五个结构性缺口：
durable trace 只在进程锁内（多 worker 有文档化丢行窗口）；observation 是纯结构
投影（无 rendered-state 逐层 telemetry、chart 只验槽位在场）；失败面只有泛化
TOOL_ERROR（CRS 裸错逃逸、两套 disjoint FailureClass）；session TTL 之外无恢复
路径；subagent 无 token 记账。Epic 要求一次复杂 GIS 任务在真实失败下能持续执行
Understand→…→Resume，且每步有可持久化/审计/重放/恢复的证据链。

## Decisions

1. **D1 Durable Trace V5**：`trace_store` 跨进程 `flock`（非 POSIX 降级进程锁并
   披露）；每会话单调 `seq`；`(turn_id, total_records)` 幂等去重；trim 永不丢
   FINAL_VERDICT；registry start 即 pin（有界 FIFO ≤1024）、settle 成功 unpin
   —— LRU 驱逐不再丢链。零 DB 表（session-plane 与 project-plane 边界维持）。
2. **D2 统一失败分类**：`HarnessFailureClass`（11 类）为 harness 词汇；
   planning/geocompute 枚举经**适配器**映射（不替换）；`classify_and_remediate`
   在 dispatch 错误 seam 单点接入（CRS 标记优先于 planning 委托——pyproj 裸错
   不再折叠为泛化 TOOL_ERROR）；`RemediationLedger` 进程级有界记账
   （LRU ≤512），全表 `max_attempts ≤ 3`，耗尽 → `abort_with_disclosure`。
3. **D3 CRS/经度硬化**：`app/lib/gis/longitude.py` 纯函数（约定检测 pm180/e360/
   ambiguous 不猜等价；几何级 AM 判定=相邻顶点跳变；GeoJSON AM 拆分 —— west
   碎片保 180、east 平移回负半周）；`to_utm_gdf_with_note` 把 CRS 语义构造异常
   收口为 typed `InvalidCRS`（**KNOWN-GAP #1 修复，xfail 转正**）。
4. **D4 Progressive DatasetProfile**：`deepen_profile` 显式 cheap→deep 入口
   （cheap provenance 保留、deep 失败回退、修订绑定失效既有契约扩展到缓存层）；
   `longitude_facts` 进 DatasetProfileV3/DatasetProfile，resolver 词表 additive
   （`longitudeConvention`/`crossesAntimeridian` 仅真实证据在场发射），planner
   fact bundle 透传。
5. **D5 Rendered-state Observation**：observation 契约 additive 扩展
   （`layers[].source_status/render_complete/feature_count`、`charts[]`，
   全部 optional —— 旧客户端零新 finding）；finalization「requested intent ↔
   actual rendered」逐层核对；`chart_required` 升级为数据级核验（rendered +
   data_points>0；telemetry 缺席 → 诚实 warning）；3 个新 finding code 进
   runtime 自愈词表（needs_repair 语义）。
6. **D6 Subagent Accounting**：子代理运行绑定专属（不注册的）TurnEvidence，
   引擎既有 usage 通道经 ContextVar 继承落进子累加器；`SubagentBudget.llm_usage`
   （join 幂等）；结果统一出口携带 `budget_usage` + `lineage`
   （parent_turn_id/depth/role）；父 evidence 单次 roll-up。provider 不回报
   usage → `reports=0` 诚实缺席。
7. **D7 Retrieval V5 评测**：66 条人工金标开环语料（direct/near_duplicate/
   hard_negative/ambiguous，zh+en，与索引和 capability 反查不同源）；
   precision@1 在去 CORE 常驻段的排序上计算；实测钉门：p@1 0.65、r@5 0.81、
   r@10 0.87、invalid_selection 0.33（**暴露线不是达标线** —— 纯词面检索
   撞 hard-negative 50%，作为 V5 基线如实记录）。
8. **D8 Project-level Resume**：`workflow_resume_anchors` 表（migration 0032，
   独立 revision）；锚点=恢复指针非第二真相（关键 chapter 块 + trace 游标 +
   ref 清单，全部有界）；恢复=新 session（`resume-` 前缀）+ plan/chapter 还原 +
   `resumed_from` 披露；ref 重水合尽力而为（旧 session / RefSpill），
   `missing_refs` 诚实披露；授权 fail-closed（匿名不可恢复）。
9. **D9 Live-failure Corpus**：八类故障注入（provider timeout/transient DB/
   empty-partial/地图源失败/重启/stale workspace/取消/重试耗尽）确定性期望 +
   预算阶梯终止证明；端到端恢复场景（真实 dispatch seam CRS 故障 → typed
   diagnose → remediation 重试 → 成功 → 渲染 telemetry verified → trace 留证）。

## Consequences

- 无第二事实源：trace/resume/telemetry 全部附着既有通道（trace JSONL、
  session store、observation POST、session-plan alias）。
- 兼容：V4 无 seq 的 JSONL 文件可读可续写（新记录 seq 从 1 起，历史不伪造）；
  observation 新字段 optional；两套原 FailureClass 不动。
- 已知限制（诚实记录）：RemediationLedger / tool_metrics 同为进程级口径；
  retrieval 开环 p@1 0.65 说明纯词面检索有真实上限（语义检索 hook 是既有
  扩展点，非本 Epic 范围）；chart telemetry 依赖前端注册表（旧构建缺席 →
  槽级校验兜底 + warning 披露）。
