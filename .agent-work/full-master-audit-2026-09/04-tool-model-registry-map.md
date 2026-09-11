# 04 — Tool / Capability / Algorithm / Model 注册表地图

## Truth sources（V8 前现状）

| 注册表 | 位置 | 身份 | 写者 | 读者 | 校验 |
|---|---|---|---|---|---|
| ToolRegistry | app/tools/registry.py | tool name（+version/contract_version） | 各 register_*_tools（启动 lifespan 注入 set_tool_registry） | dispatch、catalog/surface、manifest 投影 | register 期 tier/side-effect 同门失败；descriptor coverage 脚本 |
| CapabilityRegistry | app/lib/gis/capability_registry.py + capabilities/13 域包 | capability id | 域包 seed（代码） | planner/recipes/plan graph、runtime_bridge 投影 | validate()：artifact/fallback 引用存在性（139 个，绿） |
| AlgorithmRegistry | app/lib/gis/algorithm_registry.py + algorithms/ | algorithm id | 域包 seed | AlgorithmResolver（capability→algorithm→tool）、qualification | validate(available_tools)（213 个，绿；tool 面漂移时报 dangling） |
| ArtifactTypeRegistry | app/lib/gis/artifacts.py | artifact type id | seed（代码） | capability/algo/工具 IO 声明 | has() 检查 |
| ModelOps Registry | app/services/modelops/registry.py（文件 store，owner_scope 目录 + 跨进程文件锁） | (name, version) | modelops_tools/seed | engine/service、planning.py | descriptor 校验（task_types 词汇、bands、resolution range） |
| Workflow runtime | app/services/workflow_runtime/* | workflow/step id | workflow_compiler（harness）、API | 执行 runtime | workflow_schema、catalog generator |
| Template catalog | gis_harness template_catalog.py + product_templates | template id | 代码 seed | template_selector、composer | registry_validation.py |
| Component registry | lib/cartography component_registry | component id | 代码 seed | composer、前端 | parity（历史 CA-P1-2 修复） |
| CompiledRuntimeManifest | lib/gis/runtime_manifest.py | 投影指纹 | compile（registry 变化自动重编译；set_tool_registry 触发 refresh） | planner memo 键、retrieval、preflight | validate_runtime_manifest_strict |

## 关系现状（capability→algorithm→tool）

- 单向解析链：plan/recipe 引用 capability id → AlgorithmResolver 解析 algorithm →
  tool（execution 意图）。AlgorithmRegistry.validate(available_tools) 检查 tool 存在性。
- 反向（tool→capability）：历史 audit #1075 修复了反查图缺口（tool_to_capability），
  现由 runtime_bridge/_role_capability_map 等维护。Agent A/B 复核双向一致性。
- fallback：CapabilityDescriptor.fallback_capabilities + tool 层 fallback_v3。

## Model 的现状（V8 核心差距）

- ModelOps 有完整 descriptor（task_types 词汇、input_bands、band_order、
  resolution_range、class_schema、compatibility、resource estimate）与 registry
  （owner_scope 隔离、跨进程锁、复用 evaluation）。
- 但 capability projection 不含 model 实体：planner/qualification 解析到
  capability→algorithm→tool 即止；model 候选（sensor/bands/GPU/VRAM/可用性/可靠性）
  不进入统一检索与资格判断，`modelops_run_inference` 是唯一入口（普通工具）。
- Phase D 的 Unified Capability Graph 将把 Model/Workflow/Template/Component/
  ExecutionBackend/Provider 作为节点接入同一 derived projection（read-only，
  entity id + kind + source registry + fingerprint + relationships）。

## V8 前已知缝隙（Agent B modelops-architecture.md 将补充）

1. Intent→capability→algorithm→model→compatibility→resource→backend 的中间三段
   （model candidates、compatibility、resource estimate）无统一投影。
2. ExecutionEstimate 缺统一抽象：tool cost/latency_class/memory_class、algorithm
   ResourceEnvelope、ModelOps estimate、GeoCompute capacity 分散。
3. reliability 反馈以工具为中心，缺 model/provider/workflow/backend 维度。
