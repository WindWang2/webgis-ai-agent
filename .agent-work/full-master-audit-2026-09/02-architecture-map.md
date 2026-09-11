# 02 — Architecture Map（master@2aabdc43，主 agent 自读源码整理）

## 分层（自上而下）

```
HTTP / SSE / WS API          app/api/routes/*（31 文件：chat、pi_tools、mapspec_mutations、
                              ws、ws_collab、workflow_runtime、jobs、task、layer、map…）
                              app/core/*（config、security、lifescape）
Agent Runtime                 app/agent_pi_bridge.py（PiBridge/PiBridgePool 单例+回退）
                              vendor/pi（外部 Agent runtime，RPC over stdio）
                              extensions: app/extensions/webgis-tools（Pi 扩展，promptSnippet+webgis_execute）
Chat Services                 app/services/chat/*（context_assembler/builder/budget/policy、
                              tool_surface_v3、tool_retrieval、tool_semantic_retrieval、
                              execution_engine(legacy)、plan_orchestrator(legacy)、
                              turn_recovery、session_cancellation、pi_rpc_client/mapper）
GIS Harness                   app/services/gis_harness/*（101 文件：planner、planner_runtime、
                              runtime_bridge、goal/plan/product graph、recipes、
                              data_qualification、fallback_v3、recovery_ledger、
                              render_observation、visual_evaluator、completion、
                              workflow_compiler/instance/promotion、template_catalog、
                              registry_validation、durable_context、resume_*）
Tool Plane                    app/tools/*（57 模块 + registry.py + descriptor.py + categories）
                              app/services/tool_dispatch_service.py（统一调度：去重、
                              wave gate、map_action mint、display authoring、自愈）
                              app/services/tool_catalog.py（legacy 表面）+ tool_surface_v2
Registries (truth)            ToolRegistry（app/tools/registry.py）
                              CapabilityRegistry（app/lib/gis/capability_registry.py + capabilities/ 13 域包）
                              AlgorithmRegistry（app/lib/gis/algorithm_registry.py + algorithms/）
                              ArtifactTypeRegistry（app/lib/gis/artifacts.py）
                              ModelOps registry（app/services/modelops/registry.py + lib/modelops）
                              Workflow runtime（app/services/workflow_runtime/*）
                              Template/Component（gis_harness template_catalog + lib/cartography component_registry）
Projection (derived, read-only) CompiledRuntimeManifest（app/lib/gis/runtime_manifest.py：
                              tool+capability+algorithm 指纹化投影，registry 变化自动重编译）
                              GISWorldState / map_state（会话读模型）
Domain Services               cartography（lib/cartography 54 文件）、mapspec（services/mapspec
                              lifecycle_engine）、geo_analysis、spatial_decision、network、
                              temporal、raster、rag、explorer、report_export、publication
Data Plane                    data_fabric（65 文件，providers：postgis/wms/…、federation）、
                              lakehouse（18，cube）、geocompute（32，distributed dataflow、
                              worker capabilities）、modelops runtime（providers、GPU、
                              VRAM ledger、warm pool）、workflow_runtime（durable execution）
Persistence                   alembic migrations + PostgreSQL（async session）、
                              Redis（session_data_redis、distributed lock、SSE resume）、
                              内存实现（session_data.py MemorySessionStore，USE_REDIS=false）
Frontend (Next.js)            frontend/*：mapspec-compiler/runtime（desired-vs-observed reconcile）、
                              map-kit（MapLibre 封装）、map-commands、store（Zustand）、
                              SSE hooks、workbench UI、components/map 87 文件
Platform                      extensions_platform（45：extension SDK/marketplace/worker）、
                              jobs（durable jobs + cancellation）、collab（多用户）、
                              observability（lib/runtime evidence/trace）
```

## 双运行时事实（关键）

- **Pi 路径**（USE_NEW_AGENT=true，默认）：chat/stream → PiBridgePool(亲和 worker) →
  Pi 子进程 → 扩展 webgis-tools → HTTP 回调 /pi_tools → dispatch_tool →
  ToolDispatchService → ToolRegistry。规划链（classify_followup/should_plan/
  make_plan/ToolCatalog/CanonicalPlan/decision_log）**只存在于 legacy 路径**；
  Pi 以 webgis_execute 代理 + promptSnippet 自主选工具（chat.py:1017-1022 架构注记）。
- **legacy 路径**（回退）：ChatExecutionEngine → tool_pipeline → 同一 ToolDispatchService。
- 两条路径共享：ToolDispatchService（单一执行真相）、session_data、GISWorldState、
  harness 评估面（cartographic session evaluation 在 cartography_runtime）。

## 注册表规模（实测）

- Tools（ToolRegistry）：~300+（tier1/2/3 分层，metadata+descriptor+execution_policy）
- Capabilities：139（13 域包，validate() 干净）
- Algorithms：213（含 ResourceEnvelope/BackendVariant/NumericalTolerance，validate() 干净）
- ArtifactTypes：SEED_ARTIFACT_TYPES 种子集
- ModelOps：lib/modelops（descriptor/compatibility/resources/planning…）+
  services/modelops（registry/providers/GPU engine/loaded_cache）
- CompiledRuntimeManifest：三注册表投影 + 指纹缓存（planner memo 键含 manifest 指纹）

## 已知分层倒置/收敛轨迹（历史审计后已改善）

- bridge 不再 import api routes（CORE-03 已修，经 engine_instance）
- cartography runtime 已抽 services/cartography_runtime.py
- 统一调度已收敛到 ToolDispatchService（legacy+Pi 同管线）
