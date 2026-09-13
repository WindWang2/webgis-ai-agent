# ADR 0183 — GIS Spatial Reasoning Memory v1（方向 9）

- 状态：Proposed
- 日期：2026-09-14
- 分支：`harness/spatial-reasoning-memory-v1`
- 关联：ADR-0069（项目制图事实账本）、ADR-0119 D5（RecoveryLedger）、
  ADR-0139（V9 tenancy）、ADR-0150/0161（意图语义/可学习意图）、
  ADR-0170~0179（ads-v1 数据供给）、方向 2 Situation World Model（PR #1275，未合并）

## 上下文

「再看看医院」类追问要求 harness 复用上一轮已确认的地理范围、行政区边界、
数据集语义与版式决策，而不是每轮从零解析；同时 stale/错误记忆比没有记忆
更危险。仓库已有的记忆栈（ADR-0069 项目制图账本、V11 学习基座、
RecoveryLedger、ads pin/snapshot、artifact 账本）各管一段，但存在真实缺口：
resolved_place / dataset 语义 / 字段角色 / CRS 结论 / provider 失败 /
产品决策没有任何持久化，记忆读取只有一个无检索的文本块通道。

## 决策

1. **新建 `app/services/gis_memory/` + 单表 `gis_spatial_memories`（迁移 0072）**，
   覆盖 `session|project|user` 三作用域、10 类 GIS 记忆
   （resolved_place / boundary_ref / dataset_semantics / field_role /
   crs_resolution / analysis_artifact / successful_strategy /
   provider_failure / product_decision / user_cartographic_preference）。
   与 ADR-0069 是互补而非合并：那边记「怎么画图」（project 作用域制图先验），
   这边记「GIS 世界是什么样」。
2. **写侧收敛（不建第二套 preference DB）**：项目作用域的用户制图偏好
   路由到 ADR-0069 `record_fact(kind=preference)`；显式用户来源带
   `supersede=True`（ADR-0069 自身的「调用方已确认」纪律）。其余 9 类落新表。
3. **写入门 fail-closed（R2）**：closed-vocab 证据源矩阵（intent 解析/
   评审通过/dispatch 结果/dataset pin/数据剖面/用户显式），置信度分档门槛
   （显式用户 0.5、其余 0.6），TTL 自动解析 + 失效规则推导；模型自由文本
   永远不构成记忆。失败记忆必带 TTL（provider_failure 7d、CRS 24h）。
4. **矛盾显式化（R3）**：同 key 不同语义指纹 → supersede 链
   （新证据 active、旧证据 superseded、`supersedes_id` 可审计）；
   用户纠正必胜；learned-vs-learned 高置信者胜、弱证据不落库；
   dataset 版本推进 → 语义/角色记忆 `dataset_version` 失效。
5. **检索（R4）**：租户/作用域/active/未过期/敏感/版本兼容六级必过滤后，
   按 subject 匹配 + kind 权重 + 置信度 + 新鲜度 + 作用域优先级打分，
   有界 top-k（默认 8、硬顶 16），每条带可读理由。绝无整库进 prompt。
6. **Pi 接线（R5/R6）**：`[GIS_MEMORY]` 有界先验块在
   `_build_cartography_turn_context` 返回处拼接（单一注入通道纪律，
   verdict → cartography memory → gis memory 顺序）；
   `webgis_map_intent` 在 scope 未解析且无 hint 时以记忆兜底
   （`hint_applied` 披露 + 0.9× 置信折扣，fresh 永远优先）；
   dispatch 失败缝旁路 provider_failure 候选。工具热路径零 SQL——
   候选进 pending 缓冲，turn 端 `harvest_spatial_memory` 统一烙印
   org/user 落库并执行 GC（sweep + 作用域预算）。
7. **租户与安全（R8）**：org_id 恒由调用方烙印（`effective_org_in_thread`），
   `scoped_query` 兼容；session 作用域以 session_id 等值为界，工具侧经
   map_state 烙印（`_gis_memory_org`）fail-closed 读取；sensitive 记忆
   检索剔除 + 投影渲染双防线；凭证键剥除 + 值形态硬拒绝 + 用户路径遮蔽。
8. **评估（R9）**：32 场景 × 7 类轨迹离线回放，
   `score = 3×useful − 4×wrong − 4×stale`（wrong/stale 一票否决），
   另报 retrieval_precision / context_bytes_saved / tool_calls_saved。
   本版实测：useful=36、wrong=0、stale=0、precision=1.0、score=108。

## 不做（红线）

平台式 ChatGPT memory；raw prompt/CoT/credential 持久化；cache hit 冒充
语义记忆；第二套 recipe affinity / cartography feedback / dataset catalog /
artifact 账本；学习到的偏好越过用户显式指令；SituationCompiler 复制
（方向 2 未合并，`queries.py` 提供 narrow interface + fixture）。

## 后续接口点

- 方向 2（#1275）合并后：`MemoryProjectionInput` 可作为 SituationCompiler
  的一个事实源挂入；`queries.build_memory_projection` 签名稳定。
- `get_local_admin_boundary` 工具成功路径可产出更精确的 boundary_ref
  （当前 boundary_ref 记录 `local:admin:{level}:{name}` 确定性身份）。
- data-lifecycle GC 循环可接管 `sweep_expired` 的周期化（当前在 harvest
  位点同步执行，预算 session≤80 / project≤400 / user≤200 每 scope_id）。
