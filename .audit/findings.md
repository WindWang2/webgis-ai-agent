# Findings — 2026-08-27 六路审计（base master@7573f35）

分级：P0 数据错误/状态破坏；P1 明确 bug/race/高概率用户可见；P2 性能/架构债务；P3 UX/文档/低风险清理。
所有 finding 均由源码审计确认（文件:行号），非 grep TODO。

## 汇总

P0: 0 ｜ P1: 9 ｜ P2: 24 ｜ P3: 15

---

## 01-agent-harness

### AH-P1-1 共享制图会话运行时寄居在可选传输模块内
- Files: `app/agent_pi_bridge.py:617-1507`（~900 行：_harnesses 注册表、evaluate_cartographic_session、
  cartographic harness context persist/hydrate、runtime repair 推进）；legacy 入口
  `app/services/chat/tool_pipeline.py:227-243` 动态 import bridge。
- Root cause: ADR-0022 只抽了 RPC client 与 event mapper，session 级证据/评估留在 "Pi bridge"。
- Effect: 默认路径（legacy）运行时依赖名为 Pi bridge 的 112KB 模块；bridge 任何 "Pi 专属" 重构会炸 legacy。
- Fix: 整体迁至 `app/services/cartography_runtime/`（见 PHASE C1）。
- Tests: 现有 pi bridge / tool_pipeline 测试迁移后保持绿。

### AH-P1-2 分层倒置：bridge（services 层）import API 路由
- Files: `app/agent_pi_bridge.py:447,1813,1947,2075,2351`（from app.api.routes.chat import get_engine）。
- Root cause: engine_instance.py 已提供 try_get_chat_engine()（#893），bridge 未改用。
- Fix: 改用 `app/services/chat/engine_instance`。

### AH-P1-3 转录/标题落库逻辑三份拷贝
- Files: `app/api/routes/chat.py:573-596`（非流式 Pi）、`chat.py:770-812`（流式 _persist_pi_transcript）、
  legacy `_save_msg_async`。
- Fix: 抽 `persist_turn_transcript` 单一 helper。

### AH-P2-1 turn 预算不对称 + 过期注释（Pi 300s vs legacy 900s；execution_engine.py:1765 注释仍称 900 对齐）
### AH-P2-2 USE_NEW_AGENT 逃离配置中心（agent_pi_bridge.py:57 os.getenv；config.py 无此键；.env.example 缺）
### AH-P2-3 Pi 全局串行锁 = 多 session 排队（bridge:1550；test_pi_bridge_lock.py 钉死）— 文档明示约束
### AH-P2-4 Pi 中途失败不回退（仅 init 失败/进程死亡后准入回退；docs 声明过宽）
### AH-P2-5 finalize_display 收口契约只在 legacy SYSTEM_PROMPT（chat/prompt.py:97-104）；
  Pi 扩展 promptSnippet（extensions/webgis-tools/index.mjs:45）未提 → Pi 路径收口靠模型自觉
### AH-P2-6 skill_name 在 Pi 路径被丢弃（chat.py:830-836）
### AH-P2-7 四套规划抽象并存（plan_orchestrator / plan_mode / planning/ / gis_harness.planner），运行时归属隐式
### AH-P2-8 registry.py:683-696 硬编码三个工具名特判（存量未迁移声明式 ref_cursor）
### AH-P3-1 死代码：extensions index.ts 死副本、chat_engine.py 纯壳、chat/planner.py 壳、harness_runner 位置误导
### AH-P3-2 agent_pi_bridge.py:1981 用 Callable 但未 import（注解惰性不炸运行时）
### AH-P3-3 提示词治理分裂（legacy prompt.py vs vendor 侧）

## 02/03-world-state / layer-map-state

### ST-P1-1 remove_layer 409 superseded 后僵尸复活（无重试、pending 被清）
- Files: `frontend/lib/mapspec/user-mutation.ts:211-268`（superseded 分支回灌含被删层的服务端真相、不重试）；
  调用方 `frontend/lib/map-commands/layerCommands.ts:362-375`。
- Repro: 用户删层恰逢 agent revision bump → 本地已删、服务端仍在 → finally 清 pendingRemoved →
  composeLiveMapSpec 重新编入 → MapSpecRuntime reconcile 复活图层；HUD 行已删 → 面板无行、地图有层。
- Fix: superseded 后带新 revision 重试一次（复用 postPresentationWithRetry 模式）；仍失败 re-mark pendingRemoved + 回滚 store 行。
- Tests: mock 409→200 序列断言重试携新 revision；断言 compose 输入不含被删层。

### ST-P1-2 用户 mutation 路径无串行化：并发 toggle 互 409 + 全层覆盖回滚在途乐观态
- Files: `frontend/lib/mapspec/user-mutation.ts:69-124`；UI fire-and-forget（layers-tab.tsx:351,354 等）；
  放大器 `applyCommittedMapSpec`（user-mutation.ts:43-62）把响应 mapspec presentation 应用到全部 HUD 层。
- Effect: 两并发 POST 同 expected_revision → 后到 409 → 用户操作被回滚；层 A 响应把层 B 在途乐观值改回旧值。
- Fix: 用户路径复用 durabilityChain 串行；applyCommittedMapSpec 跳过仍有 pending 的层。

### ST-P2-1 finalize_display show 集 durable:false 与 hide 集 durable:true 不对称 → reload 丢 agent 展示决策
- Files: `frontend/lib/map-commands/layerCommands.ts:535` vs `:544`。
- Fix: show 集对“当前 spec 为 none 的目标”补 durable。Tests: hide→finalize show→重载断言仍可见。

### ST-P2-2 agent 整层 upsert 无 CAS 且不保留用户 presentation
- Files: `app/services/mapspec/lifecycle_engine.py:550-558`（整层替换）；pipeline.py 只保 source entry。
- Effect: 用户隐藏的层被 agent 重跑同 id upsert → visibility 回默认，用户决策静默丢失。
- Fix: upsert 命中已有 id 时保留既有 layout.visibility/paint.opacity（agent 未显式给出时）。

### ST-P2-3 SetLayoutIntent.components 整表替换绕过组件 CAS（lifecycle_engine.py:748-805）
### ST-P2-4 提交顺序窗口：失败 rollback 不清理已创建 checkpoint（lifecycle_engine.py:947-984）
### ST-P3-1 setMapSpecRevision 无单调保护（use-sse-stream.ts:503-513，迟到 SSE 可回退 revision）
### ST-P3-2 postPresentationWithRetry 双 superseded 返回 'retry' 且 pending 已清（visibility-transaction.ts:146-158）
### ST-P3-3 parkStaleLayers 无 durability、pending 无代际（turn-focus.ts:45-65）
### ST-P3-4 ws_service 感知 handler 直写 map_state 是死代码旁路（ws_service.py:109-128）
### ST-P3-5 patch_layer_presentation 不同步 runtime layers registry → agent 上下文读到过期 visible
  （lifecycle_engine.py:567-598；消费方 context_builder.py:186、layer_manager.py:64）

## 04-cartography

### CA-P1-1 cartographic_intent 只读不写 —— 意图→spec 语义断链
- Files: 读方 `app/lib/cartography/semantic_checks.py:1705-1733`、`quality_loop.py:51,240`；全仓无生产者。
- Effect: RESULT_VISIBILITY 对隐藏层永远 not_evaluated；QA 无法区分“故意隐藏”与“结果层被误藏”。
- Fix: lifecycle/webgis_map_product 提交图层时写 cartographic_intent（expected_visible/role）— 作为
  Cartographic Planner 的第一块投影（PHASE C3）。

### CA-P1-2 组件目录五源并存（Literal/描述符/工厂表/前端副本/composer 内第三份 position 表）
- Files: components.py:16-32,583-600；component_registry.py:46-193；component_composer.py:36-62；
  frontend map-components/helpers.ts:22-33。
- Fix: parity 测试锁定 position/priority 一致性；composer/工厂表从描述符派生。

### CA-P1-3 三套产品级组合抽象 + 兜底重叠（composition_templates ∥ template_registry 22 预设 ∥ product_templates ∥ recipes ∥ build_default_components）
### CA-P1-4 布局（重叠）QA 是空壳
- Files: layout_constraints.py:35-56（resolve_collisions no-op 注释自认）；detect_collisions 仅单测调用；
  VISUAL_OVERLAP 恒 not_evaluated（semantic_checks.py:2312-2320）；前端 36px 底部堆叠启发式（helpers.ts:56-60）。
- Fix: detect_collisions（zone 容量 + singleton 重复）接入 evaluate_cartography_semantics（PHASE C4）。

### CA-P2-1 导出与显示一致性仅参数级（exporter 不消费 variant；pdf_renderer 第三套版式）
### CA-P2-2 分类方法元数据无消费者（CLASSIFICATION_METHODS.best_for / MapModel.recommended_classifiers）
  — 分布驱动分类裁决缺失（PHASE C3 补）
### CA-P2-3 webgis_map_product 工具层重写组件绑定（tools.py:576-594；type_role_map 第三份硬编码 :395-399）
### CA-P2-4 CompositeMapSpecBuilder 产出 legacy layout 形状（composite_builder.py:236-242）
### CA-P2-5 component_registry legend-family 特判 compatible（registry.py:264-271）
### CA-P2-6 resolver 内 "density" in id 子串匹配代替声明字段（resolver.py:57-59）
### CA-P3-1 模板命名误导（composite_* 暗示计算能力，实为样式预设）
### CA-P3-2 Jenks n>1000 均匀降采样（重尾断点系统性偏差）
### CA-P3-3 statistics_panel 前端超集（emphasis 字段后端不校验）
### CA-P3-4 RENDERER_EXEMPT 手工集合会腐化
### CA-P3-5 classify 未知 method 静默兜底 equal_interval

## 05-frontend

### FE-P2-1 自动聚焦提交动画前旧相机值（map-panel.tsx:256-267 fitBounds 后同步 getCenter → set_view 真相错误）
### FE-P2-2 自动聚焦绕过相机仲裁（viewCommands 有 isUserGesturing 仲裁，focusLayer 直发）
### FE-P2-3 制图修复回路缺客户端总预算（use-cartographic-observation.ts:13,156-165；环淘汰>16 后旧 repair 可重派）
### FE-P3-1 hover queryRenderedFeatures 无 try-catch（重挂窗口 dev 噪声）
### FE-P3-2 runtime sync reconcile 可与异步链交错（仅 recovery 场景）
### FE-P3-3 renderer 注册表模块级全局（多 map 场景互清）
### FE-P3-4 SSE resume 去重只覆盖带 id 事件
### FE-P3-5 selection-highlight.ts 生产死代码
### FE-P3-6 底图自愈看门狗触发面窄且零测试

## 06-sse-api

（SSE resume/replay/心跳体系经 chaos 测试覆盖良好；无独立 P1。相关：ST-P3-1 revision 单调性。）

## 07-data-artifact / 08-performance

### DA-P1-1 build_layer_schema 全量物化而非 descriptor
- Files: `app/services/chat/context/layer_schema.py:57`（get() 完整 Redis GET+解析+O(features) 扫描），
  descriptor 已含 geom_types/bbox/count/field_schema。
- Effect: 每出现新大 ref 的 turn 付全 payload 解析（50k 要素 ≈171ms+内存）。
- Fix: descriptor 优先，缺失字段才物化补齐。

### DA-P2-1 MemorySessionStore.store() 每次写对全部存量条目重估字节（session_data.py:79-88，事件循环上）
### DA-P2-2 raster tile cmap_name 死参数 + 样式=重算（raster_tile_service.py:183,189；converter 烘焙 PNG）
  — RasterStyleSpec 缺失（PHASE C5）
### DA-P2-3 get_map_state HGETALL 携带整个 mapspec（1-12MB spec 每 2s L1 过期重解析）
### DA-P2-4 _STATS_CACHE.clear() 全清式淘汰（512 raster 后重读风暴）
### DA-P3-1 update_layer_in_state 写放大（每 UI 事件重序列化整个 layers 数组）
### DA-P3-2 get_session_metadata L1 命中路径整包 deepcopy
### DA-P3-3 gd_poi bbox Python 侧 envelope 扫描（10 万 blob/请求）
### DA-P3-4 _find_feature_by_id 线性扫描（100k click 全扫）
### DA-P3-5 MapSpec 无服务端尺寸/层数护栏

前端渲染性能：SSE 挂载 addLayer+updateLayer 双写无 no-op 短路；观测采集先于去重判定；
MapSpecChrome memo 永不命中（components prop 每渲染新引用）；isMvtSourceId O(sources×layers)。

## 09-raster

- 无 RasterArtifact / RasterStyleSpec / COG overview；两条渲染路径互不复用；band selection 写死前 3 波段；
  唯一样式旋钮 opacity 0.85。nodata 语义正确（#596/#537）。（PHASE C5 建基础 contract）

## 10-tests

### TE-P1-1 Scenario 8 测试断言与文档相反
- Files: `tests/unit/gis_harness/test_map_product_runtime_v3_e2e.py`（docstring "prunes orphan sources"，
  断言 `"src_district" in spec_after["sources"]`）→ 孤儿 source 修剪不变量零锁定。
- Fix: 锁定真实语义：remove_layer 后未引用 source 可保留（CoW 快照），但需新不变量：
  任一被引用图层不得指向缺失 source；被移除层的独占 source 必须可追踪清理。

### TE-P1-2 frontend lib/mapspec/component-mutation.ts 零专测（floating-chrome.test 整体 vi.mock 它）
- Fix: 专测 CAS 409 静默收敛 / override reconcile / superseded 清理。

### TE-P2-1 verification:"frontend_runtime" 是硬编码承诺（layer_manager.py:192 无前端在场也返回）
### TE-P2-2 cartography lane 只覆盖 tests/cartography/ 47% 文件（8/15 无 marker）
### TE-P2-3 e2e scenario 4/5 称 reload-safe 但从不 reload；用户隐藏（origin=user+CAS）无单条 e2e
### TE-P2-4 USE_NEW_AGENT 未钉 _ENV_BASELINE（脏 shell 可切 Pi 路径）
### TE-P2-5 不变量矩阵缺口：SSE reconnect 后 desired==observed 收敛、G6（用户隐藏→对话不覆盖）无端到端锁定

## 11-docs

### DO-P1-1 architecture.md 落后代码 3 个大 feature（无 GIS Harness/lifecycle/desired-vs-observed/组件运行时/
  quality gate/制图记忆/ToolDispatchService；工具数 34→75、表数 22→23 漂移）— PHASE C7 重写
### DO-P2-1 gis-harness.md 缺 #1012 内容；cartographic-closed-loop.md 缺 ADR-0069/0070 内容
### DO-P2-2 USE_NEW_AGENT 不在 .env.example
