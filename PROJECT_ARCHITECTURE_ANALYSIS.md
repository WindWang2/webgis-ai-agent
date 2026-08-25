# PROJECT_ARCHITECTURE_ANALYSIS — WebGIS AI Agent 真实架构分析

> 生成时间: 2026-08-24 · 方法: 只读代码级验证（非文档转述），全部结论附 `file:line` 证据
> 审查范围: `app/`(约 10.8 万行 Python) + `frontend/`(约 7.3 万行 TS) + `tests/`(约 11.9 万行) + `vendor/pi` + `deploy/`

---

## 1. 系统总览事实（与文档宣称的差异已核实）

| 维度 | 文档宣称 | 代码实测 |
|---|---|---|
| 工具生态 | 34 组 | **37 个静态注册模块 / 159 个工具**（`app/tools/__init__.py:11-50`；日志实证 `[ToolInit] Registered 159 tools`） |
| 数据库表 | 22 张 | **23 个 `__tablename__`**（`app/models/*.py`） |
| 地图指令 | 20 个 | 20 个（`frontend/lib/map-commands/catalogue.ts`，一致） |
| Agent 执行 | ChatEngine 规划循环 | **双引擎并行**: Pi 子进程(`USE_NEW_AGENT`) vs 自研 ChatEngine，由 `app/api/routes/chat.py:758,322-337` 逐请求选择，Pi 崩溃自动降级 |
| MVT 路径 | `/layers/{id}/tiles/...` | 实际 `/api/v1/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt`（`app/api/routes/layer.py:200`） |
| "Agent 唯一真理来源" | 禁止旁路端点 | 实为"旁路 REST + system-message-bridge 事后回告"（upload/layer/project/mutations 均为直接 REST） |

---

## 2. 一次对话请求的完整真实调用链

```
用户
 ↓ 输入
frontend/components/sidebar/chat-tab.tsx:293  (handleSend, IME 保护)
 ↓ map_state 快照(视口/图层/选中要素≤5属性)
frontend/lib/hooks/use-sse-stream.ts:1027-1112 → useMapBridge.ts:303-653 (send)
 ↓ POST /api/v1/chat/stream (+Last-Event-ID 续传, X-Session-Token, project_id)
app/api/routes/chat.py:721 chat_stream
 ├─ _guard_body_session(:767) 会话所有权校验 → 立即归还 DB 连接(:799-803)
 ├─ _use_pi_bridge(:758) ──┬─ true → Pi 路径(:821-936)
 │                         │    agent_pi_bridge.py stream_prompt(:1872)
 │                         │    vendor/pi Node 子进程(JSON-RPC, pi_rpc_client.py:191)
 │                         │    唯一代理工具 webgis_execute → POST /pi-tools/execute
 │                         │    (双重凭证: 共享密钥 + HMAC turn token, pi_tools.py:99-130)
 │                         │    → 与 legacy 完全相同的 ToolDispatchService → ToolRegistry
 │                         └─ false → Legacy ChatEngine(:938-978)
 ↓ Legacy 引擎: 规划-执行-反思循环
app/services/chat_engine.py:39 (壳) → chat/execution_engine.py:1507 chat_stream
 ├─ 整轮会话锁(:1534-1550, 等锁发 keep_alive)
 ├─ 规划: _maybe_plan(:1597-1613) → plan_orchestrator.py:563 orchestrate_plan
 │        → should_plan 门控(:260) → make_plan(:449, 单次 LLM) → 能力校验(:519)
 │        → CanonicalPlan 持久化(plan_store) → plan_ready SSE
 │        ★ gis_harness 附着点: plan_orchestrator.py:477-489 (resolve_map_request_intent
 │          + recipes.select_candidates, 仅 additive 挂 gis_intent/recipe_id, 不接管执行)
 ├─ 执行: for round in range(max_rounds)(:1665)
 │        ToolCatalog.select_schemas 选工具子集(:1668) → 流式 LLM(:1714)
 │        → 并行工具波: asyncio.create_task(tool_pipeline.execute_tool_call)(:1830-1842)
 │          + asyncio.wait(FIRST_COMPLETED) 按完成序消费(:1887-2100, 含抢占式取消)
 │        → 成功才 mark_step_done(:1946-1984); 失败结果带 correction_hint 回填
 │          (Exception-as-Thought, tool_dispatch_service.py:325-328)
 ├─ 反思: 下一轮 LLM 调用即反思(工具结果已回填 messages)
 └─ 熔断: no-progress 熔断(:2128-2172)、空响应诚实失败(:2182)、max_rounds 上限
 ↓ 工具分发(两引擎共用)
app/services/chat/tool_pipeline.py:57 → tool_dispatch_service.py:233 dispatch
 ├─ 原子去重哨兵(:249-276, 在飞/已完成区分)
 ├─ app/tools/registry.py:466 dispatch
 │   ├─ tool_metrics JSONL 落盘 + tier-3 门禁(:655)
 │   ├─ 透明 ref: 解引用(:674-698, 递归+别名, skip_keys 白名单)
 │   ├─ Pydantic 校验(:708) → execution_policy INLINE/ASYNC/THREAD(:51)
 │   └─ 重计算 → submit_durable_job(app/services/jobs/submit.py:42)
 │        → DB analysis_tasks 落库(幂等键) → celery apply_async(:153)
 │        → Celery worker(app/services/spatial_tasks.py) 心跳/取消/重试
 ↓ 结果回流
tool_dispatch_service.py:344-366 大 GeoJSON → session store 铸 ref:geojson-<hex16>
 → SSE 只发 step_result{geojson_ref, ref_descriptor, command(s)}
 ↓ 前端渲染
useMapBridge.ts:451-551 命令分发 → map-action-handler.tsx:16-177 (20 命令目录,
 终态必达 + ACK) → use-sse-stream.ts:604-748 addLayer(_refId/_tileUrl/_descriptor)
 ↓ 大数据分流(VECTOR_TILE_THRESHOLD=5000, use-sse-stream.ts:702)
 ├─ >5000 要素 & mvt_capable → MVT 瓦片 layer.py:200 (空间索引缓存+ETag/304+gzip LRU)
 └─ 其他 → GET /layers/data/{ref} 整包 GeoJSON
 ↓ MapSpec 期望状态渲染
frontend/lib/mapspec-compiler/compiler.ts:220 compileMapSpec → reconciler.ts diff
 → MapSpecRuntime.reconcileAsync 增量应用 → MapLibre
 ↓ 闭环回告
map-action-ack POST → evaluate_cartographic_session → repair_action 修复命令
system-message-bridge 隐式系统消息回告 Agent(导出产物路径等)
```

---

## 3. GIS Harness 层现状（`app/services/gis_harness/`, ~3139 行)

| 组件 | 文件 | 职责 | 在主链路的真实地位 |
|---|---|---|---|
| MapRequestIntent | `intent.py`(549行) | 确定性规则意图识别(城市表/正则/词表) + LLM hint 合并 | **仅 plan_orchestrator.py:477-489 附着, 建议性** |
| CartographyRecipe | `recipes.py`(593行) | 确定性制图配方选择与资格复检 | 同上 + agent opt-in 调用 |
| MapProductPlanner | `planner.py`(548行) | Intent→Recipe→MapProductPlan 两阶段(草稿→终稿), CAPABILITY_TOOLS 能力→工具解析 | 计划是"产品描述, 非工具脚本"(planner.py:13) |
| CartographyComponent | `components.py`(324行) | 指北针/色条/图例等可寻址制图组件 | 落 MapSpec.layout.components, 前后端同源 |
| Agent 前门工具 | `tools.py`(777行) | `webgis_map_intent` / `webgis_map_product` / `webgis_component_update` | **opt-in**: 靠 LLM 自愿调用, 非强制管线 |

**结论**: `Intent → Planner` 已实现且确定性; 但 `Intent → Planner → Workflow → Execution` 全链路**未接管执行**——工具选择权仍在 LLM(legacy function calling / Pi promptSnippet)。这是 Harness 优化的核心缺口(见 HARNESS_OPTIMIZATION_PLAN.md)。

另注: `app/lib/harness/pi_agent_harness.py`(2092行) 是**遥测/评估 harness**(工具调用证据、指标), 与 gis_harness(领域智能)是两层不同设施。

---

## 4. 工具生态与元数据现状

- 注册: `registry.tool(name, description, tier, domains, execution_policy, timeout, version, contract_version)` 装饰器(`app/tools/registry.py:274-388`), 从签名/Pydantic 自动生成 JSON Schema。
- tier 分层(:267-271): 1=总在目录; 2=域命中/会话粘性载入; 3=仅显式可见(rare/heavy/destructive)。动态筛选 `app/services/tool_catalog.py:152-190`。
- ref: 解引用: `registry.py:674-698`, 递归解析 ref 游标与别名(有 distinct 上限降级)。
- **元数据缺口**: 无显式 cost 字段(只有事后 tool_metrics JSONL); `version/contract_version` 全库默认 "1.0"/cv1 从未 bump(audit #829 自认)。

## 5. 状态管理(四类存储)

| 存储 | 实现 | 内容 |
|---|---|---|
| Session Store | `session_data.py`(Memory, LRU 200/session) / `session_data_redis.py`(Redis, TTL+L1 读穿) | ref 本体+descriptor、别名表、map_state(viewport 单调 seq)、cartographic observation/review、event_log、ACK 事件 |
| MapSpec | `mapspec/store.py`(磁盘原子写+指纹) + `checkpoint.py` + `lifecycle_engine.py`(校验失败即 rollback) | 声明式制图期望状态: sources/layers/view/layout.components/mutation_revision/fingerprint |
| Durable Jobs | `jobs/store.py`(DB analysis_tasks) | job 全生命周期 + session/owner/run/turn/tool_call 关联 + 心跳/幂等 |
| Project Workspace | `project_service.py` | 项目/数据集/工作流(版本化 DAG)/runs/artifacts/血缘 + 项目制图记忆(ADR-0069) |

## 6. 渲染管线

ref 提货券 → MVT(>5000 要素, 手写 protobuf 编码器 `app/services/mvt.py` 1645 行, ETag/304/single-flight/gzip LRU) / 整包 GeoJSON → MapSpec compiler(前端) → MapLibre 增量 reconcile → Canvas 合成导出(exporter.ts, A4/300DPI, 与服务端 mapspec_to_svg.py 有 parity 测试)。

## 7. Pi Agent 桥(`app/agent_pi_bridge.py`, 2243 行)

JSON-RPC 子进程驱动 vendor/pi; turn 生命周期(铸 turn_id/HMAC token、整轮持锁、心跳 8s/停摆 180s/整轮 900s 预算); 制图 verdict + 项目记忆注入唯一通道 `attach_turn_context`; 断连 abort RPC。**注意: Pi 路径刻意不走 CanonicalPlan 规划链**(#726 审计裁决)。

## 8. 模块依赖关系图(实测)

```
用户 → Frontend(Next.js) → API 网关(FastAPI /api/v1)
  ├─ chat_stream ─┬─ PiBridge(JSON-RPC 子进程) ── webgis_execute 代理工具 ─┐
  │               └─ ChatEngine(规划-并行执行-反思) ── tool_pipeline ─────┤
  │                                                                        ↓
  │                                              ToolDispatchService(去重哨兵)
  │                                                        ↓
  │                                              ToolRegistry(159 工具, ref 解引用, tier 门禁)
  │                                     ├─ INLINE/THREAD/ASYNC 直接执行
  │                                     └─ submit_durable_job → Celery worker(空间/遥感/网络分析)
  │                                              ↓                          ↓
  │                                     SessionStore(ref 铸造)     DB(analysis_tasks)
  └─ 渲染回流: SSE step_result → 命令分发 → MVT/GeoJSON → MapSpec reconcile → MapLibre
       → ACK → cartographic 评估 → repair_action 闭环
```

分层健康度: api→services→tools 单向为主, 但存在 **services/tools 反向 import api 路由 6 处**(详见 ENGINEERING_REVIEW.md E-2)。

## 9. 与目标架构的差距结论

目标: `LLM → GIS Intent → Task Planner → Workflow Runtime → Tools → Renderer`。

| 目标层 | 现状 | 差距 |
|---|---|---|
| GIS Intent Understanding | ✅ 确定性 resolver 已存在 | 已挂入规划, 但仅附着 |
| GIS Task Planner | ◐ LLM 规划 + harness 附着 | 高置信场景无确定性短路, 每回合多付一次规划 LLM 调用(H-1) |
| Workflow Runtime | ✅ durable jobs + project workflows | 覆盖好; legacy 回合无总时长预算(H-2) |
| Tool Ecosystem | ✅ 159 工具 + tier + schema | 缺 cost 元数据; 部分工具 domains 标注不全(H-8) |
| Template Cartography | ✅ recipes + product_templates + components | 前门工具可达性受限(H-8/H-9) |
| GIS Engine | ✅ Celery 隔离, 算法经 3 轮审计 | 数据获取环存在偏差(G-1)与坐标系混叠(G-2) |
| Interactive WebGIS | ✅ MapSpec 单一事实源 + 闭环 | 6 块 UI 不可达(U-1)等交互缝隙 |
| Cache | ◐ L1(map_state)/tool_cache/MVT LRU | ref payload 无进程内缓存(P-1); 无 analysis 级跨轮缓存 |

深入问题清单见五份审查报告: HARNESS_CODE_REVIEW.md / GIS_ALGORITHM_REVIEW.md / PERFORMANCE_REVIEW.md / UI_REVIEW.md / ENGINEERING_REVIEW.md。
