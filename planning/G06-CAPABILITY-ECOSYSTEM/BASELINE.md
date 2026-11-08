# G06 CAPABILITY ECOSYSTEM — M0 实时基线审计

审计时间：2026-11-07（GMT+8）｜执行宿主：原生 /goal + 自管理 Oracle 循环
（`goal-loop` / `planning-with-files` 技能在本仓库 **不存在**，已核实 `.claude/skills/` 与 `.agents/skills/`，未捏造安装）

## 1. 仓库与 worktree 事实

| 项 | 值 |
|---|---|
| 目标仓库 | `WindWang2/webgis-ai-agent` |
| 实时 `origin/master` | `585812ee4e2d75612f2b124e6fe6587af3deca5c` |
| master 提交时间 | `2026-11-07T17:54:14+08:00` = Merge PR #2010 |
| Prompt 快照 SHA | `585812ee4…` → **本次执行开始时刷新后一致**（不视为永久值） |
| 目标 branch | `zcode/goal-gis-capability-ecosystem-20261107`（新建，之前 local/remote 均无） |
| 独立 worktree | `/home/kevin/projects/webgis/webgis-wt-gis-capability-ecosystem-20261107` |
| 检出命令 | `git worktree add -b … ../webgis-wt-… origin/master` ✅ |
| 起始 HEAD | `585812ee4`（clean，无未提交改动） |
| 主工作树状态 | 分支 `zcode/review-g3dv2-fixes`，含 `m vendor/pi` + 未跟踪 `.claude/skills/*` —— **未 stash/reset/clean/force-push，完全未触碰** |
| gh 认证 | `WindWang2` (keyring)，`gh 2.102.1`，网络可用 |

本地验收环境：主工作树 `.venv`（Python 3.13.17 / pytest 9.1.1 / pydantic 2.13.4 /
numpy 2.4.6 / rasterio 1.5.0 / shapely 2.1.2 / networkx 3.6.1 / scipy 1.18 / ruff / mypy）。
从本 worktree 的 cwd 运行时 `app` 解析到 **本 worktree**（已验证 `app.__file__`），不是主工作树旧代码。
基线：`pytest tests/unit/gis/test_execution_catalog.py --no-cov -q` → **16 passed**；
`python -m app.lib.gis.execution_catalog_docs --check` → **up to date**。

## 2. 在途 PR / 分支 / Review / Issue（实时 `gh` 查询）

- **Open PR = 8，全部是 dependabot**，改动仅 `pyproject.toml` / `requirements*.txt` /
  `frontend/package.json` / `pnpm-lock.yaml` / `.github/workflows/*`。与本方向 **零文件冲突**。
  (#1978–#1980, #1982–#1986)
- **最近 45 个 merged PR** 已读列表；与本方向直接相关的：
  - `#1972` 类型化分析产物封套 + 栅格/网络新族（51 files，Review COMMENTED 记录 5 项分析正确性修复）
  - `#1973` Pi v1.5.0 vendor/Host（35 files，Review COMMENTED 记录 jiti 运行镜像修复）
    → **Pi v1.5.0 已集成，严禁作为本方向"新功能"**
  - `#1974` 情境交互、`#1975` 出版制图、`#1976` 全量集成（100 files）
  - `#1989` Pi/GIS 可用性（37 files，含 2 条 follow-up comments：包裹工具拒绝、城市范围锁定）
  - `#1993` 地理范围与版面求解、`#1997` catalog 漂移修正、`#1998` 工具载荷预算（8 files）
  - `#2008` Story 浏览器旅程、`#2009` session 锁重入、`#2010` 会话恢复命令可用性
- **并行 goal 分支（未开 PR，本地 worktree）冲突矩阵**：

| 分支 / worktree | 相对 master 的改动面 | 避让决策 |
|---|---|---|
| G01 `zcode/goal-pi-native-gis-runtime-20261107`（3 commits） | `app/agent_pi_bridge.py`, `pi_compat.py`, `pi_surface_metrics.py`, `pi_turn_state.py`, `app/lib/runtime/evidence.py`, `health*`, `scripts/pi_doctor.py`, `planning/G01-*`, 3 个测试 | **本线完全不改** PiBridge / `pi_rpc_client` / Pi Extension 宿主生命周期 / `pi_native_surface.py`；只做只读消费者 adapter |
| G02 `zcode/goal-gis-execution-compiler-20261107`（**0 commits**） | 尚无改动 | 不建立第二 Planner/TaskGraph；本线只供给元数据与预检 |
| G03 `zcode/goal-geodata-readiness-fabric-20261107`（worktree，3+ commits） | data fabric / explorer 桥 / 分发资格闸 / 语义身份证 | 不重构 Data Fabric、摄入、STAC/COG/OGC、数据语义注册 |
| G04/G05 | 已合并 PR #1974/#1975 | 不新建 MapSpec / 渲染器 / 模板选择器 / 情境账本 / 前端地图交互 |

- **Issues #1999–#2007 全部 OPEN**（`gh issue view` 已读正文）：
  - `#2005` 网络距离分区汇总聚合的是**行程秒数**而非米（`families.py` 的 `total_cost`）
  - `#2006` 带 summary 的工具结果丢掉分区数值（`slim_tool_result` 白名单不含 `data`）
  - `#2007` OpenAPI 快照 / 生成质量文档过期 → 已知 **线上 CI 历史债务**，不等待、不修改 Actions
  - 其余 #1999–#2004 为出版物/选择集/指北针，属 G04/G05 范围
  - **决策**：`#2005/#2006` 作为「科学可信回归输入」纳入本线 CAP-O06 的**只读对拍与诊断披露**；
    **不抢修整份 Issue**（不改 `families.py` 的聚合口径、不改 `llm_result_formatter.py` 的压缩器），
    避免与在途独立 issue 修复撞车。本线新增原语的结果封套自身保证「摘要含关键数值或可分页 ref」。

## 3. 现有能力真值清单（present / partial / planned / missing / false-advertised）

判定口径：**以注册代码、执行 body、返回结构为准，不采信文档措辞**。逐项证据见 `RECON.md`。

| 能力 | 状态 | 精确证据 |
|---|---|---|
| `CapabilityRegistry` / `AlgorithmRegistry` / `AlgorithmResolver` | ✅ present | `app/lib/gis/capability_registry.py:29` `CapabilityDescriptor`；`algorithm_registry.py:242` `AlgorithmDescriptor`（含 `scientific_preconditions` / `fallback_semantics`）；`algorithm_resolver.py:39` `AlgorithmResolution` |
| `ExecutionCatalog` + `generation_fingerprint` | ✅ present | `execution_catalog.py:530`（`compile_execution_catalog` :630） |
| `tool_capability_conformance` **八消费面** | ✅ present | `tool_capability_conformance.py:83-95` `CONFORMANCE_FACES`；`:127-138` `FALLBACK_CODE_XWALK`；`:635` `audit_tool_conformance`；`:683` `audit_registry_conformance`；`:738` `collect_live_facts` |
| `CompiledRuntimeManifest` + `fingerprint` + `is_stale_plan` | ✅ present | `runtime_manifest.py:121`, `:171`, `:294` |
| `method_use_contract` / `parameter_contracts` / `science_final_gate` | ✅ present | `method_use_contract.py:47`；`parameter_contracts.py:28` `PARAM_UNIT_VOCABULARY`（含 `seconds`）；`science_final_gate.py:48` `NON_EQUIVALENT_SEMANTICS` |
| `scientific_preconditions` 五值 + deferred | ✅ present / ⚠️ partial | `scientific_preconditions.py:27,32,53`；**无统一 `eligible/ineligible/not_evaluated` 三态** |
| `SpatialSemanticContract` | ✅ present | `app/services/gis_harness/spatial_semantics/contract.py:79`（**不在 `app/lib/gis/`**）；`vocabulary.py:82` 六态；`validate.py:57` `SpatialOperationSpec` |
| `ToolRegistry` 唯一执行真相 + 指纹链 | ✅ present | `registry.py:547 register`, `:1338 _dispatch_impl`（planned/tier3/policy 单点闸）, `:1125 registry_fingerprint`；`app/tools/__init__.py:11-150` `_TOOL_MODULES` |
| `ToolDispatchService` | ✅ present | `tool_dispatch_service.py:277,870,875` |
| 检索：词法 + 离线语义 + V6 hybrid + 有界 K | ✅ present | `tool_retrieval.py:352 rank_tools`；`tool_semantic_retrieval.py`；`semantic_retrieval.py`；`tool_surface_v3.py:79-83` `DEFAULT_K_MIN=10/MAX=30/_MAX_K_MAX=64` |
| Pi 原生工具面（7 natives + `webgis_execute` 代理） | ✅ present | `pi_native_surface.py:23,33,58,76`；`CONTEXT.md:461-476` 裁决「`webgis_execute` 不是 Tool」 |
| 扩展平台：包级 + 能力级双层认证 | ✅ present | `certification.py:25`；`capability_certification.py:365,722,875`（6 stage + `_report_hmac`） |
| 扩展签名 HMAC-SHA256 / Ed25519 / 指纹 / 分级激活 | ✅ present | `signing.py:48-63,110,141,278`；`host.py:89 ExtensionState`, `:133 HostPolicy`, `:634 activate`, `:1588 deactivate`, `:1714 reload`, `:1842 upgrade`, `:2074 health` |
| 扩展 `version_pins` | ✅ present | `host.py:179,1917`；`settings_bridge.py:99` |
| 扩展 `lease` 语义 | ❌ **missing** | 全 `app/extensions_platform/` 无 lease（仅 connection `release()`） |
| `pack_catalog` / `capability_certification` 索引 | ✅ present | `pack_catalog.py:27,111`；`execution_catalog.py` `build_certification_index_from_host` |
| ModelOps `GeoModelDescriptor` / `ProviderCapabilities` | ✅ present | `app/lib/modelops/descriptor.py:236`, `capabilities.py:164` |
| 五要素资格（loaded/healthy/compatible/budget/permission） | ✅ present / ⚠️ partial | `loaded_cache.py`, `provider_health.py`, `compatibility.py:115 qualify`, `resources.py:60`, `descriptor.py:293 credentials_ref` —— **分散，未聚合成单一可用性门** |
| GPU/CPU fallback / 推理 provenance | ✅ present | `descriptor.py:114-124 allow_cpu_fallback`; `errors.py:174,179`; `lineage.py` append-only |
| `network_od_matrix` OD 成本矩阵 | ✅ **present（必须避让）** | `app/tools/network_tools.py:485,503,531` → `engine.solve_od_matrix`，`MAX_OD_MATRIX_PAIRS=10_000` |
| 插值 LOOCV / `ValidationMetrics(method="loocv")` / 残差分位数 | ✅ **present（必须避让）** | `advanced_spatial.py:136,189-205,1019`；`uncertainty.py:159`；`interpolation.py:466` |
| 条件 / 多分位数**分区栅格**统计 | ❌ **missing（真实缺口 #1）** | `advanced_spatial.py:45` `ZonalStatsArgs` 仅 `geojson`+`raster_path`；`raster_ops.py:234-254` stat 白名单 `count/min/max/mean/sum/std`，其余 key → `None` + warning。`sar_temporal_stats` percentiles 是 SAR 时间维、`rs_cube_tools` quantiles 是特征摘要、`SGS` 是条件模拟，**均非多边形分区 × 条件/分位数** |
| 线网服务覆盖**一致性校验** | ❌ **missing（真实缺口 #2）** | 全 `app/tools/` 无 `consistency*` 注册点；现有只有 `network_service_area` / `service_area_simple` / `isochrone_network` / `network_accessibility` 等**生成**能力 |
| **`CapabilityAvailabilitySnapshot` / 可信状态词表** | ❌ **missing（真实缺口 #3）** | 全 `app/` Grep `native_verified\|available_unverified\|declared_only\|temporarily_unavailable\|CapabilityAvailabilitySnapshot` → **0 命中**；现存仅声明态 `CapabilityStatus = native/planned/unavailable`(`capability_registry.py:16`) 与 `ToolStatus`(`descriptor.py:34`) |
| **统一三态预检 `eligible/ineligible/not_evaluated`** | ❌ **missing（真实缺口 #4）** | 三套语义并存：五值+deferred、六态 `VERDICT_VOCABULARY`、`interpolation_compare.py:96 eligible: bool` |
| **false-advertised** | ✅ 无 | 全层贯彻 `not_evaluated` / honest failure / fail-closed；`backend_selection.py:337` 显式拒绝虚构 GPU/分布式路径 |

## 4. 全 registry 8 面对账真实分布（本方向判定规则的输入）

```
collect_live_facts(bootstrap=True) → audit_registry_conformance
tool_count = 397, truncated = False
by_overall = {'warning': 184, 'pass': 213}   # 无 fail / 无 not_evaluated
top_reasons = metadata_incomplete(158), result_size_unknown(76),
              binding_unbacked(63), domain_keywords_missing(5),
              ref_offload_without_ref_contract(4)
registry_generation = 0200c6340d555ea3…
```
→ 可用性投影会得到**真实分层**（213 个可标 `native_verified` 候选，184 个降级
`available_unverified`），不是空快照；且 reason codes 全部来自既有 8 面（**无第二套码**）。

## 5. 冲突矩阵与避让决策（最终）

| 禁区 | 本线动作 |
|---|---|
| `app/agent_pi_bridge.py`, `pi_rpc_client.py`, `pi_native_surface.py`, Pi Extension 生命周期 | **只读**；Pi 侧通过新增稳定 adapter 消费可用性投影，不改 G01 文件 |
| 新 Planner / TaskGraph / 第二 registry / 第二 ExecutionCatalog / 第二 fingerprint 体系 | **不建**；所有新模块是既有权威源的只读投影 / 扩展 |
| Data Fabric / STAC/COG/OGC / 数据语义注册 | **不碰** |
| MapSpec / 渲染器 / 模板 / 出版布局 / 前端地图交互 / 情境账本 | **不碰** |
| `docs/catalog/execution-catalog/*` | **只用既有生成器** `python -m app.lib.gis.execution_catalog_docs` 重生成，不手工编辑、不新写生成器 |
| `network_od_matrix` / 插值 LOOCV | **不重复实现**，改用确认的真实缺口 #1/#2 |
| 扩展包级/能力级认证、签名、HMAC、pin | **复用** `certification.py` / `capability_certification.py` / `signing.py` / `host.py`，禁止造第二认证体系 |
| `GeoModelDescriptor` / `ProviderCapabilities` / `CompatibilityReport` | **复用**，不新造平行类型 |
| `registry._dispatch_impl` 单点闸 / `webgis_execute` 非 Tool | **不改**；新原语走标准 `@tool` 注册 + dispatch |

## 6. 确定的可用性缺口（本线要修的，逐条可复现）

1. **GAP-A** 无跨层只读 `CapabilityAvailabilitySnapshot`：声明支持 / stub / 真实注册工具 / 可交付产物 在现有目录里混在 `status="native"` 一个词里；「历史通过 ≠ 当前环境可执行」无表达。
2. **GAP-B** 无可信状态词表与显式未验证原因；缺依赖无 `not_evaluated` / `temporarily_unavailable` 区分。
3. **GAP-C** 检索重排不感知可用性：向量相似度高的不可用工具仍可进入候选面。
4. **GAP-D** 无统一三态科学预检；秒/米、经纬度/投影的量纲混用无单一可验证门（`#2005` 即其后果）。
5. **GAP-E** 两项原生计算原语真实缺口（条件/多分位数分区栅格统计、线网服务覆盖一致性）。
6. **GAP-F** 五要素 provider 资格分散，无只读聚合门；扩展停用/权限收缩后的 fail-closed 无单一证据面。
7. **GAP-G** 结果摘要契约无跨 registry→Pi→dispatch→slim payload 对拍面（`#2006` 即其后果）。
