# G13-CAPABILITY-EXECUTION · BASELINE

- 基线：`origin/master = 816705218a5aa6202bf15d745cb31cddb3437f44`（2026-11-10，与任务书快照一致）
- 分支：`zcode/goal-g13-capability-execution-20261110T133500Z`
- 工作树：`/home/kevin/projects/webgis/webgis-worktrees/goal-g13-capability-execution-20261110T133500Z`
- 开放 PR（无关本线）：#2032 UI 重设计（避让）、#2022 G10、#2021 G11、#1979 tailwind、dependabot 若干。
- 相关已合并：#2016（G06 本线基础）、#2036（Pi-only 对话）、#2033（命令清单）、#2011–#2017（执行编译/runtime/资格/工作台/组合器/可靠性）。
- 相关开放 issue：#2071/#2072（扩展 worker 内存/工具碰撞——他线专项，不吞并）；#2005/#2006（科学回归输入，G06 已只读对拍）。

## 真实调用链（已核实，file:line 为基线锚点）

### Pi 会话 → 工具面 → 执行
1. HTTP `/api/v1/chat/stream` → `app/api/routes/chat.py:1125` → `PiBridge.stream_prompt`（`app/agent_pi_bridge.py:2117`）
2. `bind_turn_prompt`（`app/services/chat/pi_turn_context.py:216`）→ `_active_tools_block_for`（`pi_turn_context.py:395`）
3. `compute_turn_active_tools`（`app/services/chat/pi_native_surface.py:604`）
   → `DynamicToolSurface(registry).select(ctx)`（`app/services/chat/tool_surface_v3.py:511`）
   → tier 双检 → `_apply_surface_qualification`（situation 投影，`pi_native_surface.py:834`）
   → `apply_surface_byte_budget` → SurfaceReceipt → `[WEBGIS_ACTIVE_TOOLS:...]` marker
4. 扩展侧 `app/extensions/webgis-tools/index.mjs`：`before_agent_start` 解析 marker → `pi.setActiveTools(names)`（≤48）
5. 模型工具调用 → 扩展 `execute()` → HTTP `/pi-tools/execute`（`app/api/routes/pi_tools.py:51`）
   → `dispatch_tool`（`app/agent_pi_bridge.py:479`）
   → `resolve_pi_tool_call`（native/execute/reject 三分类，`pi_native_surface.py:76`）
   → `stale_surface_gate`（指纹 echo + turn receipt 范围，`pi_bridge_surface_gate.py:71`）
   → `ToolDispatchService.dispatch`（`app/services/tool_dispatch_service.py:282`，唯一执行权威）
6. dispatch 内既有闸：dedup 占位/释放（:340-372）→ situation 供给合并（:389）→
   `bind_tool_capability`（:419，ADR-0204，fail-open）→ Action IR bind（:536）→ registry 执行 →
   结果 ref 铸造（geojson/uncertainty/profile/heatmap，:1258-1314）→
   `register_tool_artifact`（:1391）→ `slim_tool_result`（:1558）

### G06 交付物现状（逐项核实）
| 模块 | 状态 | 生产消费 |
|---|---|---|
| `app/lib/gis/capability_availability.py`（快照编译+查询） | 真实 | **无**——仅 CLI `main()`；无缓存，全量 8 面审计每次重跑（G06 REVIEW F-08 记账） |
| `app/lib/gis/availability_rerank.py`（重排+validate_pi_call） | 真实 | **无**——无任何 `app/` 调用点（仅测试/CLI） |
| `app/lib/gis/method_eligibility.py`（三态科学预检） | 真实 | **无** |
| `app/lib/gis/provider_eligibility.py`（provider 资格） | 真实 | **无** |
| `app/services/chat/pi_availability_adapter.py`（Pi 投影） | 真实 | **无**——`validate_projected_call` 无人调用 |
| 两个原语 `conditional_quantile_zonal_stats` / `network_service_coverage_consistency` | 真实计算、真实 @tool 注册（`app/tools/__init__.py:117`） | dispatch 可达，但：①无 AlgorithmDescriptor 绑定 → planner face `binding_unbacked` warning → `available_unverified`（ADR-0287 D7：保持 253 计数故不加描述符）；②输出 `artifact://…` 合成 URI **无任何存储/读取方**（grep 证实零消费者），不落 session store、不进 ArtifactRegistry、不可翻页——`pageable:true` 是无兑现承诺 |

### 关键既有事实
- `compile_execution_catalog` 有单例缓存（`execution_catalog.py:711`），`registry_fingerprint()` 有 memo；但 `collect_live_facts` / `audit_registry_conformance` / `compile_availability_snapshot` **全部无缓存**（ADR-0287：~10s 量级）。
- AlgorithmRegistry 域包 `ALGORITHMS=[]`（`app/lib/gis/algorithms/capability_ecosystem.py:16`）；`@tool` 描述符的 `algorithms` 字段经 `_derive_capability_backfill`（`app/tools/registry.py:55`）从 `tool_to_algorithms()` 派生 —— **新增带 tool_candidates 的 AlgorithmDescriptor 会自动回填**工具描述符的 algorithms。
- 计数断言：`tests/unit/gis/test_g02_science_oracles.py:575` `contracts == count == 253`；`tests/unit/gis_tool_conformance/test_live_registry_audit.py:41` `"binding_unbacked": 64`。
- dispatch seam 只给 FeatureCollection/heatmap/profile 形状的结果铸 `ref:`（`tool_dispatch_service.py:1258-1314`）；`{data,artifact,science,budget}` 封套不命中任何铸造分支。
- ref 消费：registry `_resolve_references`（`app/tools/registry.py:1960-2053`）对 args 里的 `ref:` 透明解引用（跨 ref 失败抛 ValueError 带自愈指引）；`find_reusable_artifact`（`app/lib/gis/analysis_reuse.py:284`）做同参复用。
- 生成物：`docs/catalog/execution-catalog/*` 由 `python -m app.lib.gis.execution_catalog_docs` 生成；`scripts/gen_science_catalog.py` 锁定科学目录计数。

## 最小失败复现（WP01 验收）
- 现状：G06 可用性状态变化（如把工具标 `temporarily_unavailable`）**不影响** `DynamicToolSurface.select` 产出的候选名单——`tool_surface_v3.py` 全程不读快照。测试 `tests/unit/gis/test_g13_*` 将以注入 registry + 快照证明：同输入下，快照状态翻转 → 候选名单变化（当前 master 上不变化 = 复现缺口）。
