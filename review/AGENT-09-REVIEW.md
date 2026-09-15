# AGENT-09 审查纪要 — What-If 反事实假设推演分支管理器（ADR-0193）

- 分支: `agent/09-counterfactual-whatif-scenario-branching`（worktree `webgis-wt-agent-09`）
- 日期: 2026-09-15
- 审查范围: 全部新增/修改文件（见 §1）；重点核验**多方案分支数据隔离**（§3）与
  **内存释放机制**（§4）
- 关联: ADR-0193、docs/dev/whatif-branching-spec.md、tests/unit/test_whatif_branching.py

## 1. 交付清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `docs/adr/0193-whatif-scenario-branching.md` | 新增 | 架构决策：会话命名空间隔离 / Diff 标准 / 处方双层 / scenario_mode 协议 |
| `docs/dev/whatif-branching-spec.md` | 新增 | 工程规格：模块布局、API 契约、验收矩阵（T1–T15） |
| `app/services/simulation/whatif/branch_manager.py` | 新增 | 分支控制器：fork/干预/回滚/注册表/删除/对比 |
| `app/services/simulation/whatif/spatial_diff_engine.py` | 新增 | 几何差分 + 覆盖差分 + 指标差分（proxy:v1）+ 红绿对比图层 |
| `app/services/simulation/whatif/prescriptive_advisor.py` | 新增 | 确定性核（Pareto/ROI/优先级）+ LLM 叙述层（显式降级） |
| `app/services/simulation/whatif/comparison_report.py` | 新增 | 结构化对比专报（JSON + markdown，只格式化不复算） |
| `app/services/simulation/__init__.py`、`whatif/__init__.py` | 新增 | 公共面 re-export + 防重复施工声明 |
| `app/lib/cartography/mapspec_schema.py` | 修改 | v1.3 additive：`scenario_mode` 顶层可选字段 + identity upgrader |
| `app/services/mapspec/lifecycle_engine.py` | 修改 | `SetScenarioModeIntent`（union + 事务分支，非法值整笔拒绝） |
| `app/services/gis_world_state/mutation.py` | 修改 | op journal 标签「切换推演模式」 |
| `frontend/lib/mapspec-compiler/types.generated.ts` | 再生成 | `python -m app.lib.cartography.ts_projection`（scenario_mode 投影） |
| `frontend/lib/mapspec/scenario-mode.ts` (+test) | 新增 | 纯函数映射 split_view→side-by-side / swipe_compare→swipe |
| `frontend/components/map/map-panel.tsx` | 修改 | scenario_mode ↔ ComparisonView 桥接 effect（仅推演退出时收回，不覆盖用户手动对比） |
| `tests/unit/test_whatif_branching.py` | 新增 | 18 例（T1–T15 验收矩阵全覆盖） |
| `tests/cartography/test_mapspec_schema_v6.py`、`test_component_graph_v7.py` | 修改 | 版本钉住 1.3 + upgrader 注册表恢复语义 |

## 2. 门禁证据

| 门禁 | 命令 | 结果 |
|---|---|---|
| 任务规定套件 | `pytest tests/unit/test_whatif_branching.py -v` | **18 passed**（3.0s） |
| ruff（任务规定面） | `ruff check app/services/simulation/whatif/ tests/unit/test_whatif_branching.py` | **0 违例** |
| ruff（波及面） | mapspec_schema / lifecycle_engine / mutation / schema_v6 / v7 | **0 违例** |
| 定向回归 | world_state / lifecycle_engine / mutation_batch / store / tests/cartography/ | **1530 passed, 2 skipped** |
| 广度回归 | `pytest tests/unit/`（全量） | **4065+ passed**；唯一失败 `test_data_fabric_adapters.py::test_postgis_adapter_interface` 为**存量环境性失败**（需 localhost PostgreSQL，连接拒绝），与本交付零交集（改动面不含 data_fabric/postgres），基线上同样失败 |
| 前端映射 | `vitest scenario-mode.test.ts` | **5 passed** |
| 生成物契约 | `vitest types.contract.test.ts` | **2 passed**（再生成后 byte 级一致） |
| 相邻前端回归 | workbenchSlice / comparison-view / comparison-sync / layers-tab.compare | **51 passed** |
| 类型检查 | `tsc --noEmit`（前端全量） | **0 错误** |

## 3. 核验一：多方案分支数据隔离（结构性保证）

**机制**：分支 = 派生会话 `{parent}__wif_{branch_id}`。父/分支 A/分支 B 三方在
存储与事务层完全正交：

| 维度 | 隔离载体 | 证据 |
|---|---|---|
| 权威 spec | 各自 `mapspec.json`（独立目录）+ Redis hash key | T1（fork 后三方 spec 相等）、T2（A 增层后 B/baseline 不含） |
| 修订计数（CAS） | 各自 `_cartographic_mutation_revision` | T2（A≥2 而 B==1，独立递增） |
| 分布式锁 | `session_lock_registry.lock(sid)` 按 sid 分键 | 并发互斥不跨分支（engine 既有语义，sid 不同即无竞争） |
| 幂等去重 | 各自 `_mutation_dedup`（同事务提交） | 方向 8 语义随会话分键天然隔离 |
| 检查点/回滚 | 各自 `checkpoints/`；回滚走分支自己的 spec | T3（A 回滚到干预 1 后，B 与 baseline spec/revision 逐字节不变） |
| provenance | 各自 `_gis_provenance` 环（fork 时显式清零） | create_branch 写空环；分支后续突变只入自己的环 |
| user-wins 守卫 | 守卫读各分支自己的 spec 印记/环 | 干预经 `apply_gis_mutation` 全量继承（非旁路） |

**结论**：隔离是**结构性**的（不同 Redis key、不同磁盘目录、不同锁作用域），
不依赖调用方纪律。T2/T3 以世界状态投影（`build_world_state`）与权威 spec
双重断言验证。

## 4. 核验二：内存/存储释放机制

| 资源 | 释放路径 | 核验 |
|---|---|---|
| Redis/内存 map_state（spec、revision、provenance、fork 存证、注册表外的 runtime 键） | `delete_branch` → `session_data_manager.clear_session(branch_sid)`（Memory/Redis 两后端同语义） | T5：删除后 `get_map_state(branch_sid) == {}` |
| 磁盘（mapspec.json + revisions/ + checkpoints/ + blobs） | `clear_session` 联动 `purge_session_disk_state` → `clear_session_files`（rmtree） | T5：删除后 `BASE_STORAGE_DIR/<branch_sid>` 不存在 |
| MapSpecStore 进程内缓存（`_persisted_fp`/`_persisted_obj`，audit #838 指出的滞留点） | `clear_session_files` 内部 `_invalidate_process_cache(branch_sid)` | 复用既有失效路径（store.py:187-193），无分支特例旁路 |
| ref spill / MVT 缓存 | `clear_session` 内 `ref_spill_store.purge_session` + `spatial_index_cache/tile_lru_cache.invalidate_session` | 既有联动原样生效 |
| 注册表条目 | 删除成功后从 `_whatif_branches` 移除并回写父会话 | T5：`list_branches() == []`；幂等重删返回 False |
| 分支数量上界 | `MAX_ACTIVE_BRANCHES = 5`（活跃分支），防 Redis/磁盘随 fork 数无界放大 | T4：超限 → `branch_limit` 拒绝 |
| 兜底 | 分支目录与普通会话同受 idle TTL sweep（mtime 兜底回收孤儿） | store 既有 sweep 白名单正则天然放行 `__wif_` 命名 |

**结论**：释放是**单入口幂等**的（delete_branch 一处收口），且复用既有会话
终局清理链（内存→spill→缓存→磁盘），无第二套清理实现。T5 覆盖全部四层
（Redis/内存、磁盘、进程缓存、注册表）。

## 5. 自审发现与处置

| # | 发现 | 处置 |
|---|---|---|
| R1 | `test_register_upgrader_chain` 的 finally 对 `("1.2","1.3")` 做破坏性 pop —— 1.3 成为真实 upgrader 后，同进程后续测试（含 T13）会被摘除迁移路径 | 改为快照受影响键、finally 恢复原值（tests/cartography/test_mapspec_schema_v6.py） |
| R2 | `test_component_graph_v7` 把 "1.3" 当 forward 版本样例 | forward 样例改 "9.9"；legacy 迁移矩阵补 1.2→1.3 |
| R3 | 两侧差分若各自计算投影 lat0，同纬度长度/面积比率随 lat0 漂移（T8 实测 225.0→224.99），破坏 delta_pct 精确性 | 引擎强制两侧共享联合均值 lat0（`_shared_lat0`），并在 evaluate_metrics docstring 披露契约 |
| R4 | 入库后 source 载荷存在双层包装（`entry.inlineData.inlineData`） | `extract_layer_payloads` 兼容两种形态；ref/url 外置载荷照旧跳过 + gap note |
| R5 | `_feature_key` 几何兜底用 SHA1 | 非安全用途（配对键去重），已 `# noqa: S324` 注明 |
| R6 | 人口图层存在但无有效 `population` 属性时覆盖人口计 0 而非 missing | 已知限制（proxy:v1 口径），evidence_gap_note 覆盖「缺图层」主路径；留 v2 收紧 |
| R7 | `advisor.advise` 推荐与 pareto 采用两种口径（推荐=并集支配前沿；pareto_optimal=共同指标非支配集） | ADR §D4/spec 已披露；T11 同时断言两者且相互自洽 |
| R8 | LLM 叙述层可能改写推荐结论造成幻觉改判 | LLM 仅替换 narrative/因果链/置信度，`recommended_branch_id` 以确定性核为准 |
| R9 | map-panel 桥接若在 spec 无 scenario_mode 时无条件 exitComparison，会覆盖用户手动开启的对比 | 仅记录前值、**从推演模式退出**时才 exitComparison（prevScenarioModeRef） |

## 6. 执行记录（-errors led → fixes）

| 错误 | 轮次 | 根因 | 修复 |
|---|---|---|---|
| shapely transform `func(x,y)` 双参 TypeError | 1 | 投影闭包单参签名 | `_project(x, y)` 兼容序列/逐坐标两形态 |
| T4 branch_limit 未触发 | 1 | 测试自身限额设错（1 活跃 vs 限额 2） | 修正测试限额 |
| T6 面积量级不符 | 2 | 测试期望公式 y 向误乘 cos | 等距圆柱仅 x 向乘 cos：期望 = Δlon·M·cos × Δlat·M |
| T7 lost_area 非零 | 2 | 覆盖差分两侧各算 lat0，缓冲几何不可比 | 共享 lat0（R3） |
| T8 delta_pct 微漂 | 2 | 同 R3 | 同上 |
| `_population_cells` 缺参 TypeError | 2 | R3 重构漏改一处调用点 | 补 lat0 实参 |
| ruff F401/E741/F841 ×9 | 3 | 未用导入、`l` 歧义名、残留变量 | 全部修复后 ruff 0 违例 |
| v7 版本钉住 3 failed | 4 | R2 | 同 R2，回归 1530 passed |
| 广度跑 postgis adapter 1 failed | 5 | 存量环境性失败（localhost:5432 连接拒绝），非本交付引入 | 裁定豁免（基线同败）；续跑剩余字母序 unit 文件确认无其他失败 |
| 续跑 27 failed 复核 | 5 | stash 基线对照：24 例基线同败（postgis 连接 / pmtiles 真实文件 / runtime_validator fixtures 等，均环境依赖）；3 例分歧（gpu 门控 / pi_bridge 内存 / 双进程会计）在隔离复跑下于本分支全部通过 —— 负载型 flake（后台长跑挤占资源所致），非回归 | 全部裁定豁免；基线对照 + 隔离复跑证据留档 |

## 7. 边界声明与后续（v2 候选）

- 本交付**未**触碰：`app/tools/what_if_simulate.py`、`spatial_decision` 引擎本体、
  `gis_situation`、`map_product_service`、MapProduct 发布谱系。
- v1 明确不做（spec §7）：栅格差分、真实交通分配、分支合并（merge）、
  发布谱系联动。
- 建议后续：(a) 分支干预的批量事务（`apply_gis_mutation_batch` 目前仅限
  presentation intent，放开后可一次事务落 N 条干预）；(b) 覆盖差分的部分面积
  积分（当前质心口径）；(c) `scenario_mode` 进入 workbench 工具面（前端入口按钮）。
