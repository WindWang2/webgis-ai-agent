# Development Plan — Phase B/C（2026-08-27）

原则：correctness > state consistency > GIS semantics > architecture > performance > maintainability > UI。
小批次、可验证、可回滚；已实现的目标只验证不重做。

## Phase B — 修复批次（P1 → P2）

| # | 批次 | 内容 | 关键文件 |
|---|---|---|---|
| B1 | fix(state) | remove_layer 409 superseded 重试 + pending 保持（ST-P1-1）；postPresentationWithRetry 双 superseded 类型谎言（ST-P3-2 顺带） | user-mutation.ts, layerCommands.ts |
| B2 | fix(state) | 用户 mutation 串行链 + applyCommittedMapSpec 跳过 pending 层（ST-P1-2） | user-mutation.ts |
| B3 | fix(layers) | finalize show 集 durable 对称（ST-P2-1）；agent upsert 保留用户 presentation（ST-P2-2） | layerCommands.ts, lifecycle_engine.py |
| B4 | fix(test) | Scenario8 语义锁定 + source 修剪不变量（TE-P1-1）；component-mutation 前端专测（TE-P1-2）；USE_NEW_AGENT 钉基线（TE-P2-4） | tests/ |
| B5 | fix(perf) | build_layer_schema descriptor 优先（DA-P1-1）；MemorySessionStore O(1) 字节记账（DA-P2-1）；SSE 挂载双写 no-op 短路 | layer_schema.py, session_data.py, layersSlice.ts |
| B6 | fix(harness) | bridge 分层倒置（AH-P1-2）；USE_NEW_AGENT 入配置中心 + .env.example（AH-P2-2）；turn 预算注释/对称（AH-P2-1）；Callable import（AH-P3-2）；死代码清理（AH-P3-1 部分：index.ts） | agent_pi_bridge.py, config.py |

中间验收：ruff / pytest unit+integration+cartography / vitest / typecheck / next build。

## Phase C — 架构开发（依赖序）

| # | 批次 | 内容 | 依赖 |
|---|---|---|---|
| C1 | refactor(harness) | 制图会话运行时（~900 行：_harnesses 注册表、evaluate_cartographic_session、context persist/hydrate、runtime repair）从 agent_pi_bridge 抽出 → app/services/cartography_runtime/；bridge 与 tool_pipeline 同引一个模块（AH-P1-1）；transcript helper 统一（AH-P1-3）；Pi 扩展补 finalize_display 收口契约（AH-P2-5） | B6 |
| C2 | feat(state) | GISWorldState 基础：app/services/gis_world_state/ — 统一读模型（viewport/basemap/layers/components/interaction/provenance 投影）+ GISMutation 门面（identity resolution → expected_revision → runtime projection → durability → postcondition 的统一入口与不变量）；用户优先策略单点化。不推翻 MapSpec，演化它 | B1-B3 |
| C3 | feat(cartography) | Cartographic Planner：CartographicIntent 投影写层（CA-P1-1）；choose_classification 分布驱动裁决（消费 CLASSIFICATION_METHODS + field stats，CA-P2-2）；VisualizationPlan 一等工件（intent→model→classification→palette→composition 每步 choice+reason，可序列化入 plan 证据） | C2 |
| C4 | feat(cartography) | Visual QA：detect_collisions（zone 容量+singleton）接入 evaluate_cartography_semantics，VISUAL_OVERLAP 从 not_evaluated 变真检查；finalize 修复预算 MAX_REPAIR_ATTEMPTS 客户端总预算（FE-P2-3）；组件目录 parity 锁定 position/priority（CA-P1-2） | C3 |
| C5 | feat(raster) | Raster data plane 基础 contract：RasterArtifact descriptor（band/dtype/nodata/stats/overview/CRS 结构化）+ RasterStyleSpec（palette/stretch/bands/opacity/resampling 进 MapSpec paint 侧）+ tile cmap 接线（DA-P2-2 死参数）+ band selection 参数化；样式改动≠重算 | 独立 |
| C6 | test(golden/perf) | G6 用户隐藏→对话不覆盖（端到端锁定 user-interaction-wins）；大 workspace benchmark（50-100 层 mutation/compile、100-turn 会话、session reload 冷启动） | C2-C4 |
| C7 | docs | architecture.md 重写（GIS Harness / lifecycle / desired-vs-observed / 组件运行时 / quality gate / 记忆 / dispatch 统一）；ADR：制图运行时抽出、GISWorldState、Cartographic Planner、Cartographic QA、Raster contract | C1-C5 |

## 明确不做（本轮）

- 不翻转 USE_NEW_AGENT 默认值（收敛能力与契约后再翻转，rollback path 保留）
- 不删除 ChatEngine（降级为 compatibility adapter 的前提是 Pi 补齐 TaskTracker/标题/技能/子代理等，超出本轮）
- 不重写 templates.py legacy 轨（只记录 + 汇合点保证语义）
- 不实现完整 COG 转换管线（只落 descriptor/contract 与 overview 检测）
