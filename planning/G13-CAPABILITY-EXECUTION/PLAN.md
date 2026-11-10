# PLAN · G13-CAPABILITY-EXECUTION

## 实现顺序（纵向路径优先）

### M1｜WP06 原语算法绑定（先行——最小改动解锁 native_verified 路径）
1. `app/lib/gis/algorithms/capability_ecosystem.py`：`ALGORITHMS` 增加两条真实
   AlgorithmDescriptor：
   - `raster.zonal.quantile_conditional`（capabilities=["zonal_statistics"]，
     tool_candidates=["conditional_quantile_zonal_stats"]，parameter_contract_ref 同名契约，
     crs_class=RASTER_GRID，科学前提/单位/复杂度/seed/fallback 声明）
   - `network.service_coverage.consistency`（capabilities=["service_area"]，
     tool_candidates=["network_service_coverage_consistency"]，crs_class=PROJECTED_REQUIRED）
   - 域包 docstring 更新（不再是"保持空表"）。
2. 计数断言更新：`test_g02_science_oracles.py:575` 253→255（注释说明 G13 +2）；
   `test_live_registry_audit.py` binding_unbacked 64→62（若确为这两个工具解除）。
3. 生成物刷新：`python -m app.lib.gis.execution_catalog_docs`；
   `scripts/gen_science_catalog.py`（如消费计数）。新增实质断言：候选可解析
   （capability_tool_map 含原语）、方法语义、科学前提预检三态。

### M2｜WP03 同代快照缓存
新模块 `app/lib/gis/capability_availability_cache.py`（本任务命名空间）：
- `get_availability_snapshot()`：进程级缓存；结构键 = (registry_fingerprint,
  catalog generation（复用 `get_execution_catalog` 单例代）, 扩展宿主状态摘要,
  认证摘要, 算法/能力注册表 id 集)。键读取全部 O(1) memo（registry_fingerprint
  自带 memo；host digest 复用 `_extension_state_digest` 的 status_report——
  该调用读宿主公开状态，本身轻量）。
- 单飞：`threading.Lock` + 双检；旧构建晚于新键完成时不得覆盖新代（记录
  building_key，完成时 key 不匹配即丢弃）。
- 失败回退：构建异常保留 last-good（标 `degraded_sources`）+ 最小重试间隔
  （默认 5s）防失败风暴；关闭清理（atexit 停止后台重建）。
- 命中路径零全量编译：热路径计数断言 compiler 调用次数=0。
- 显式失效 API `invalidate_availability_snapshot()`（测试/扩展停用路径）。

### M3｜WP04 候选检索接入
`app/services/chat/tool_surface_v3.py` `select()`：在 6.7 之后、7（contract 过滤）之前
插入「可用性投影」阶段（kill switch `GIS_AVAILABILITY_RERANK`，默认 on，失败零贡献）：
- 从 M2 缓存取快照；对 scores 中每个动态候选查记录：
  - `_BLOCKED_STATUSES`（declared_only/temporarily_unavailable/forbidden）→ 移入
    `selection.dropped`（reason=`availability:{status}:{unverified_reasons[0]}`），CORE
    前门/native 名单永不抑制；
  - native_verified → +boost、available_unverified → 温和降权（保留可见）；
  - 时间语境 × 米制工具名（复用 availability_rerank 词表）→ 抑制 + UNIT_DIMENSION_CONFLICT。
- suppressed/boost/降权理由进 `selection.reasons` + `selection_trace["availability"]`
  （有界）；`compute_turn_active_tools` 的 disclosure 增加 `availability_suppressed`。
- 与 `rerank_candidates` 共享同一底层判定（新增共享 helper，两处一致）。

### M4｜WP02+WP05 执行前复核
扩展 `pi_availability_adapter.py`：`summarize_tool_qualification(tool_name, snapshot,
registry, permissions=None)` → 有界组合视图（结构可用性/权限/科学画像缺席面/
registry+catalog generation/来源层标注）。
`tool_dispatch_service.py` 在既有 bind（1.42）之后追加复核（best_effort 包裹、
仅阳性事实拒绝、其余放行+证据）：
- 快照记录 `availability_status` ∈ blocked → typed `CAPABILITY_UNAVAILABLE`（拒绝码
  进 error_msg/slim_event；denial 文案含原因+下一步）→ 释放 dedup 占位；
- `tool_version_id` 与 registry descriptor 对拍（同名 schema 换代 → typed
  `STALE_TOOL_SCHEMA`，可重试=重新发现）；
- 权限面：仅当 descriptor.required_permission 且 situation/auth 提供否定性事实时拒绝
  （不新增第二权限系统，fail-closed 事实来自现有 situation 供给）。
- 拒绝复用既有 `ToolDispatchResult(status="error")` 通道与 decision_record 惯例。

### M5｜WP07 真实产物与复用
- `capability_ecosystem_tools.py` `_ref()`：不再伪造 `artifact://` URI——输出
  `content_fingerprint` + `stored:false`（诚实现状）；schema_version 升
  `capability-artifact/v2`（additive：加 `stored`，`ref` 变 Optional）。
- dispatch seam（`tool_dispatch_service.py` 正常路径）：识别
  `result["artifact"]["kind"]=="analysis_artifact"` 封套 → `_session_data.store(
  session_id, envelope, prefix="analysis")` 铸真 `ref:analysis-*` → 回填
  `result["artifact"]["ref"]=…, stored=true` + `result["data_ref"]` →
  `register_tool_artifact`（analysis_key 复用既有 compute_analysis_key 通道）。
- 下一轮消费：args 传 `ref:analysis-*` → registry `_resolve_references` 透明解引用；
  同参重跑命中 `find_reusable_artifact`。跨 session/伪造 ref → ValueError typed 拒绝。
- slim 载荷只留有界摘要 + ref（完整表不进模型上下文）。

### M6｜WP08/09/10 边界与披露（增量）
- WP08：`MAX_SCHEMES`（默认 8，超出 typed BUDGET_EXCEEDED）；不可达设施/吸附失败
  计数披露；`geometry_mask` 像元中心口径已在 science 披露（核对补缺）；
  N-07 死代码清理。
- WP09：缓存键含扩展宿主摘要（停用/撤权即换代）；复核层消费
  `extension_lifecycle_view` unavailable_namespaces（阳性缺席才拒）；无全局权限污染
  ——权限过滤只在请求侧 query/summarize 传 permissions 时生效。
- WP10：抑制原因/拒绝文案进 disclosure、chain emit（复用 TOOL_SURFACE stage）与
  denial_text（含 correction_hint）——同一失败在 API/Pi 文字/界面三面一致。

### M7｜WP11 验证矩阵（C01–C16）
新增 `tests/unit/gis/test_g13_capability_execution.py` + Pi stub 旅程集成测试；
故障反验：移除 selector 接点 → 候选验收失败；移除执行复核 → 选后撤权验收失败；
伪 artifact URI → 下一轮读取验收失败（均以 fixture/monkeypatch 注入，不提交破坏性改动）。

### M8｜WP12 独立 review → 修复 → 复审 → PR

## 性能纪律
- 热路径断言：同代命中 `compile_availability_snapshot` 调用数 = 0；重建并发 = 1；
- pytest workers 默认 1；重型 catalog 生成一次；不跑全仓 3 万测试。

## 已知边界
- 不改 Pi vendor/bridge/宿主（G12）；不建第二注册表/调度器/权限系统；
- 外部 provider 真实探测不做（缺事实=not_evaluated，与 G06 纪律一致）。
