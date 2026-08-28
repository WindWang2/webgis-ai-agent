# Repository Map — 2026-08-27 audit (base: master@7573f35)

本文档由六路并行源码审计产出（Reviewer A–F），描述 `webgis-ai-agent` 当前真实架构。
近期上下文：#1012（floating panels / composable templates / component tools / LayerVisibilityTransaction / finalize_display verification）、#1013（12 个基线红灯 triage）、#1014（Map Product Runtime v3：COW lifecycle、layer identity、invariant hardening）。

## 1. 顶层结构

| 目录 | 规模 | 职责 |
|---|---|---|
| `app/` | 378 py | FastAPI 后端：Agent runtime、工具注册、MapSpec 生命周期、SSE、Celery |
| `frontend/` | 423 ts/tsx | Next.js 工作台：MapLibre 渲染内核、SSE 解析、Zustand store、组件运行时 |
| `tests/` | 581 py（~5200 用例） | unit / integration / cartography / perf / benchmarks / jobs / fixtures |
| `docs/` | architecture.md + 71 篇 ADR + 领域文档 | 架构与决策记录 |
| `vendor/` | Pi agent（node 子进程） | JSON-RPC agent loop（规划/重试/compaction 在 vendor 内部） |
| `app/extensions/webgis-tools/` | index.mjs | Pi 侧扩展：注册单一代理工具 `webgis_execute`，HTTP 回调后端 |
| `scripts/ci-local.sh` | 9 lane | 本地 gate（与 production.yml 契约锁定） |

## 2. Agent 运行时拓扑（Reviewer A）

```
POST /api/v1/chat/stream
  └── chat.py::_use_pi_bridge() = USE_NEW_AGENT && bridge alive && !process_died
        ├─ Pi 路径（opt-in，默认 OFF，agent_pi_bridge.py:57 os.getenv）
        │    PiBridge 单例 → PiRpcClient(node 子进程 JSON-RPC)
        │      → vendor agent loop
        │      → webgis_execute 回调 POST /pi-tools/execute（HMAC turn token）
        │         → dispatch_tool → ToolDispatchService.dispatch（与 legacy 同一服务）
        │      → pi_event_mapper 纯函数 → SSE
        │      → 全局 turn 锁（跨 session 串行，bridge:1550）
        └─ Legacy 路径（默认）
             ChatEngine(壳) ⊂ ChatExecutionEngine（app/services/chat/execution_engine.py, 143KB）
               plan_orchestrator（LLM plan + _synth_plan_from_harness 零 LLM 合成）
               → ToolCatalog 裁剪 → LLM 循环（60 rounds / 900s 预算）
               → ToolExecutionPipeline → ToolDispatchService → ToolRegistry

共享核心（两路合一）:
  ToolRegistry（单例）｜ToolDispatchService（dedup/ref/ref落存/MapSpec authoring/slim/error 契约）
  制图闭环：dispatch 证据 → PiAgentHarness(app/lib/harness/) → evaluate_cartographic_session
            （当前位于 agent_pi_bridge.py:972-1417，约 900 行，legacy 经动态 import 反向依赖）
  GIS Harness 领域层 app/services/gis_harness/（intent/recipes/planner/components/tools）
```

关键事实：
- **双 runtime 确认**。`USE_NEW_AGENT` 默认 false，所有部署文件未开。
- ChatEngine 独有/被 Pi 反向借用的能力：TaskTracker、标题生成、clear_session、技能系统、
  子代理、map_state 持久化、schema 预算裁剪、孤儿修复。
- Pi 独有：vendor 内部规划/自动重试/compaction、全局串行模型（300s 预算 vs legacy 900s）。

## 3. 状态模型（Reviewer B）

真相源 = **后端 MapSpec**（磁盘 `mapspec.json` 权威 + Redis `map_state.mapspec` 缓存，先盘后 cache）。
`mutation_revision` 权威在 `map_state._cartographic_mutation_revision`。前端一切为投影。

| 存储 | 谁写 | 何时 |
|---|---|---|
| 磁盘 mapspec.json + revisions(20) + checkpoints(20) | MapSpecLifecycleEngine.apply_mutation（分布式锁内） | 每次成功 mutation |
| Redis map_state hash（mapspec/layers/revision/observation/review/viewport） | engine 提交段；observation 端点 | save 成功后 |
| 前端 session-cursor（committed/pending/revision） | SSE 事件/突变响应/restore | mutation 前后 |
| useHudStore layers 行（不持久化） | 用户乐观写 / applyCommittedMapSpec / syncSpecLayersToStore | 交互时 |
| MapLibre runtime style | composeLiveMapSpec → MapSpecRuntime diff | 每次输入代数变化 |
| 组件乐观 override | FloatingChrome 拖拽收尾 | 手势期间 |
| ref-source-resolver cache | 后台拉取 /layers/data/{ref} | ref-only 源 |

CAS 覆盖：用户路径全部必填 expected_revision（superseded→409）；agent 仅
webgis_component_update 可选 CAS，其余 agent 工具（map_product/layer_upsert/set_view/layout_set/...）
完全绕过（last-writer-wins）。

## 4. 制图管线（Reviewer D）

```
MapRequestIntent(intent.py) 
  → model_library(15 MapModel) + classify(Jenks/quantile/…) + palettes
  → recipes(11) / product_templates(7) / composition_templates(8) / component_templates(27 variants)
  → MapProductPlanner（确定性，plan 携带 algorithm_selections/fallbacks 证据）
  → component_resolver/composer → CartographyComponent 实例
  → MapSpecLifecycleEngine → MapSpec
  → QA：semantic_checks(40 规则 DSL) + quality_loop(AUTO_SAFE ≤2 轮修复) + project_memory
  → 前端 mapspec-compiler + map-components registry（catalog parity 测试）
  → 导出 exporter.ts / pdf_renderer.py / mapspec-to-svg（双孪生 parity）
```

legacy 双轨：`app/tools/templates.py`（61 tmpl_* + 22 composite_* 预设，产出 legacy layout 形状）
与 `app/tools/cartography.py`（create_thematic_map）在 mapspec_store.layer_upsert 汇合。

## 5. 数据平面（Reviewer E）

- ref: 双层缓存（内存 LRU 200/50MB + Redis TTL 4h）+ ref_payload_cache（5s/256/128MB）+ 鉴权双通道
- MVT：纯 Python 编码器、STRtree 索引、字节感知 LRU(256refs/256MB)、ETag/304、single-flight
- raster：两条互不相通路径 — A 瓦片流（raster_tile_service）／B colormap 烘焙 PNG（converter）。
  无 RasterArtifact/RasterStyleSpec；无 COG/overview 构建；样式改动=重算。
- Celery：无 Redis 时 eager 兜底；重计算全部 to_thread；未发现事件循环上的 gpd/rasterio 直调。
- MapSpec 无服务端图层数/字节上限。

## 6. 测试体系（Reviewer F）

- 后端 ~5200 用例：主 lane（not perf/cartography/real_services，cov≥75）+ perf lane（7 文件）+ cartography smoke（7 文件带 marker，目录 15 个中 8 个无 marker）
- 前端 vitest ~1695 用例，coverage 硬阈值（lines75/func70/stmt75/branch60）
- golden 两层：planner 级（test_golden_cases_orchestration.py）+ 产品级（test_golden_cases_v2.py G1/G2）
- Pi/agent 主回路全 mock 化；`USE_NEW_AGENT` 未钉入 _ENV_BASELINE（脏 shell 可切路径）
- 9 个 Runtime Scenario（headless probes）nightly-only（REQUIRE_BROWSER=1）

## 7. 调用链（目标链路的真实现状）

```
User → Frontend(chat-tab) → POST /chat/stream(+map_state)
  → [Pi bridge | ChatEngine] → planning → tool selection
  → ToolDispatchService → ToolRegistry → GIS tools
  → ref: 提货券 + MapSpec authoring（fingerprint 证据）
  → SSE（token/tool_result/mapspec/mutation_revision）
  → use-sse-stream → map-action-handler / session-cursor
  → composeLiveMapSpec → MapSpecRuntime diff → MapLibre
  → observation（runtime-evidence → /chat/sessions/{sid}/observation）
  → cartographic gate（fingerprint + ack 收敛判定）→ finalize_display
```
