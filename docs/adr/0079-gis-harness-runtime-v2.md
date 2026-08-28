# ADR-0079: GIS Harness Runtime v2 — 原子状态、编译清单与统一地图运行时

- 状态：Accepted（2026-08-29）
- 关联：ADR-0058（CAS revision）、ADR-0071（cartography runtime）、ADR-0072（GISWorldState / user-wins）、ADR-0073–0078
- 分支：`feat/gis-harness-runtime-v2`
- 审计基线：`docs/research/gis-harness-runtime-v2-audit.md`

## 背景

audit5 之后系统具备全部 v2 组件（Pi Host / SessionPlan / GIS Harness /
GISWorldState / GISMutation / MapSpec / Cartographic Runtime），但五个系统性
缺陷横跨组件（审计编号 F1-F4 / H1-H3 / R1-R4 / FE1-FE4 / P1-P6）：

1. **revision 复活回退**（F1）：磁盘复活路径恢复 CAS 令牌晚于 engine 的
   prior 捕获 → N→1 单调性破坏 + 重放 CAS 通过。
2. **锁纪律不对称**（F2/F3）：仅 MapSpec engine fail-closed；6 条共享写路径
   在降级锁下继续写共享 Redis。
3. **两事务 commit**（F4）：spec+revision 与 runtime layers 分属两笔事务，
   crash 窗口留下世代错配。
4. **finalize 逐层事务**（H1）+ **隐藏集洗白**（H2）：N 层 = N 个完整事务
   （409 风暴根因）；agent 决策经 user 路由提交为 `presentation_owner="user"`。
5. **registry 无编译清单**（R1-R4）：无启动校验、网络工具 parity 缺口、
   plan 无 registry 指纹（#1084）。
6. **前端运行时层生命周期**（FE1-FE4）：setStyle 永久抹掉 custom-* 覆盖层、
   观察证据身份过滤错误、label 不上活地图、样式编辑不持久（#1077）。

## 决策

### D1 — 原子状态与锁（Phase 1）

- **权威载入先于 CAS**：`apply_mutation` 把 `get_mapspec(state_hint)` 提前到
  revision 捕获/CAS 之前；磁盘复活路径把恢复的令牌回写 hint（F1）。
- **单事务 commit**：`commit_mapspec_state`（Redis WATCH/MULTI + 内存等价）
  把 spec + revision + 指纹 + runtime layers 的 read-modify-write 合并为一次
  原子提交（F4）；WS 并发 layers 写经 WATCH 重试保留合并语义。`save_mapspec`
  以 `layer_op` 携带层操作并回报 `layers_persisted`；测试替身退回旧序列。
- **回滚不回拨令牌**：`_rollback_to_snapshot` 以 prior+1 持久化恢复的旧 spec
  —— 方向约束为 rev ≥ spec 世代（安全侧），失败尝试的 N+1 可能已被观察。
- **fail-closed 共享写**：SessionPlan、chat 观察/ACK/delete、cartography
  运行时、plan 执行全部 `fail_on_degraded=True`（无 Redis 部署不受影响——
  degraded 仅指"已配置但不可达"）；ACK/观察/delete 写前复检 `lock.lost`；
  `LockDegradedError`/`LockLostError` 映射结构化 503。

### D2 — GISMutationBatch（Phase 7）与 presentation ownership（Phase 2）

- `apply_presentation_batch`：N 个 presentation patch 一个事务（单锁/单读/
  逐 intent 锁内守卫/单校验/单 checkpoint/revision 恰 +1/单 save）；
  refused / not_found 项跳过并逐项上报；全 refused 批不落盘。
- **finalize_display 服务端化**：展示集 + 隐藏集同以 `origin=agent` 落盘
  （H2 根因修复）；boundary 语境与已隐藏层不参与；user-owned 层由守卫拒绝
  并如实上报。前端 FINALIZE_DISPLAY 命令只做本地呈现（durable:false）。
- **upsert 家族守卫**（H3）：legacy ring 用户隐藏对 agent upsert 同样生效
  （仅已存在层族）；AUTO_SAFE 可见性修复同步 intent 印记（H5）。

### D3 — Compiled GIS Runtime Manifest（Phase 3/4）

- `app/lib/gis/runtime_manifest.py`：启动时把全部 registry 编译为不可变快照
  （capabilities/algorithms/tools/map_models/components/templates/recipes/
  product_templates + O(1) 反查图）。编译是只读投影——registry 仍是注册权威。
- **cross-registry 校验分级**：fatal（悬空 capability/全候选缺失/dangling
  alias/recipe 悬空）→ lifespan fail-fast（`GIS_MANIFEST_STRICT=0` 逃生）；
  warning / planned 记日志。当前源编译 0 fatal。
- **网络 parity 修复**（R2）：closest_facility / accessibility / od_matrix /
  location_allocation / route_optimization 五个 capability 就位；拓扑服务区
  （network_service_area）优先于等时圈/速度表；shortest_path 不再退化到
  isochrone 工具族。
- **指纹与 STALE_PLAN**（#1084/R3）：SHA-256 over canonical（sort_keys）投影
  —— 跨进程/重启确定、内容敏感（候选顺序参与：顺序即解析优先级）。
  `MapProductPlan.manifest_fingerprint` 编制时落章；恢复时与当前 manifest
  比对，不一致 → `[SessionPlan]` 投影标注 `STALE_PLAN=true` + replan 建议。
  历史计划（无指纹）不判 stale。
- **解析去重**（R4）：planner `capability_tool_map` 读 manifest 预排序视图；
  `plan_from_intent` 以（query, recipe, template, available_tools,
  project_verified, manifest 指纹）memo（深拷贝进出、有界 64）。

### D4 — Layer Runtime 统一（Phase 5）

- **Runtime Layer Mount Registry**（FE1/#1078）：renderer 挂载缝记账全部
  `custom-*` 覆盖层（原始定义，GeoJSON 记 raw data）；basemap setStyle 后按
  插入序重放（sources→layers）；删除反注册（无复活）；会话 id 变化清账。
- **观察证据家族键联合**（FE4）：期望子层匹配 `hud.id ∪ _mapspecLayerId`
  （精确 + `__` 前缀）—— committed 平铺 id 与 HUD 展开子层两种形态都命中。
- **label 上活地图**（FE2）：`addLayerSafe` 为 `layer.label/labelField` 挂
  `${id}-label` symbol 子层（编译器方言），删层同族拆除。
- **持久样式通道**（#1077）：`PatchLayerStyleIntent`（paint 顶层键合并进层族，
  不触碰 cartographic_intent）经用户突变路由 `patch_layer_style`（CAS）；
  前端面板对 spec 层启用规范键控件（颜色/描边/宽度/半径），滤镜类控件保持
  禁用（规范未建模，不发明语义）。
- **source GC 维持既有契约**：sources 是数据登记项、由 ref 生命周期治理
  （#1014 TE-P1-1，scenario_8 锁定）——v2 明确不在此处加 GC。

### D5 — hot path 与组件布局（Phase 6/8/9）

- 守卫环 / chat 准入 / harness-context 读取走 `get_state_field` 单字段通道
  （P1/P2/P3）；内存后端定向浅拷贝替代全量 deepcopy（P6）。
- ACK 批 Lua 经 `register_script`/EVALSHA（P5）。
- **ComponentLayoutRuntime**：槽位模型 + 元数据（defaultSlot/priority/
  exclusive/stackStepPx）+ 确定性求解器；顶槽堆叠与 bottom U-2 对称
  （chart/statistics/annotation 不再同点互压）；FloatingChrome 键盘可达
  （方向键 8px、Shift/Alt 24px、role=region、aria-keyshortcuts）。

## 后果

- 正向：revision 单调性在复活路径成立；共享写在锁不可证明时 fail-closed；
  finalize N 层成本与 N 无关（PC-1 契约）；registry 漂移启动即暴露；旧计划
  对新 registry 显式 stale；basemap 切换不再丢覆盖层；观察证据反映真实地图。
- 代价：commit 路径多一个后端能力接口（测试替身需退回旧序列）；strict
  manifest 使"带伤启动"不再可能（可用 env 逃生）；finalize 隐藏集裁决从
  前端移到服务端（HUD-only 层仍由前端局部收敛）。
- 测试：`tests/unit/test_runtime_v2_*.py`（atomic-state / mutation-batch /
  manifest）+ `tests/perf/test_runtime_v2_perf_contracts.py`（-m perf）+
  前端 custom-overlay-registry / layout-runtime / basemap 重挂验收。
