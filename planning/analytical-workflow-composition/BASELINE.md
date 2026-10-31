# BASELINE — 多图层 GIS 分析流程组装与结果证据链（goal-02）

- 基线：`origin/master = 4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（PR #1867 合并，2026-10-28 21:53:48 +0800）
- worktree：`../webgis-worktrees/gis-analysis-workflows`，分支 `zcode/gis-analytical-workflows-vnext`
- 网络注记（2026-10-29）：国际 TLS 全断（直连 + 本地代理 7890 均失败），gh keyring token 失效；本地 refs 已含基线（与任务书审计锚点一致）。open PR/issue 在线核对挂起，PR 创建前必须重试复核。

## 1. 生产主链（全部亲证，file:line 见勘察报告与 INTERFACE_CONTRACTS）

`POST /api/chat/stream` → PiBridge（vendor/pi 子进程）→ Pi 回调 `POST /api/chat/tools/execute` → `dispatch_tool`（app/agent_pi_bridge.py:468）→ `ToolDispatchService.dispatch`（app/services/tool_dispatch_service.py:308）→ ToolRegistry 工具。`USE_NEW_AGENT=True`（app/core/config.py:302）。

## 2. 能力现状归档（production / partial / scaffold-only / absent）

| 能力 | 判定 | 证据 |
|---|---|---|
| Requirement IR（requirement_document.v1，UserLock/patch/CAS） | production | requirement_ir/contracts.py:440；tools.py:710-726 真实持久化 |
| Recipe 注册表（166 recipe / 154 family / 12 composite / 7 scenario） | production | recipes.py:1929；recipe_packs/ 27 包 |
| WorkflowProfile DSL V2（15 data roles / 义务 / 七维完成契约） | production | workflow_schema.py:34-50,162 |
| Recipe 资格/回退裁决 | production | recipes.py check_eligibility:616, resolve_fallback_chain:814 |
| 数据角色匹配绑定（打分 + descriptor 指纹新鲜度） | production | dataset_semantics/role_binding.py:268-337；data_qualification.py:402 |
| SessionPlan 进度（Pi 工具回调打勾） | production | session_plan.py:119,733 |
| workflow_instance 运行态投影（stale/blocked/事件维度） | production（纯投影，非执行器） | workflow_instance.py:517 derive_workflow_instance |
| workflow_runtime V5/V6（instance/driver/reuse/recompute/CAS/租约） | production-but-idle：代码完备，autorun=0，仅显式 API，chat 不驱动 | service.py:34-36,205 run_instance；dispatch.py（geocompute 适配器通道） |
| Workflow V4 编译器（typed_dag/methodology） | partial：chat 内仅展示级证据 | plan_orchestrator.py:670 _compile_v4_evidence |
| 36 个高级空间工具（含插值族全科学证据链） | production | advanced_spatial.py；geo_analysis/ 58 文件 |
| 真实路网等时圈（networkx Dijkstra + STRtree 投影） | production | geo_analysis/network.py:32-375 |
| networkx 引擎族（routing/OD/allocation/E2SFCA/重力/Huff） | production | app/services/network/ + network_tools.py 10 工具 |
| artifact ref/lineage/supersede（会话内） | production | artifact_registry.py:87-110；session_data.py:366 |
| 分析→MapSpec converter + webgis_map_product | production | analysis_cartography_converter.py:299；tools.py:790 |
| G02 三段科学守门 + MethodUseContract | production（final gate 披露性、非硬阻断） | science_final_gate.py:134；product_compiler.py:348 |
| G05 验收 lab（61 场景） | test-asset（无生产运行时） | tests/acceptance/gis_agent/ + scripts/g05_task_acceptance.py |
| skill procedure 库（9 核心包 YAML） | declarative production / executor absent | skills/procedure_ir.py:196 |
| 工具级分析复用（shape 指纹） | production | analysis_reuse.py:170,266；tool_dispatch_service.py:718-748 |
| 步骤级选择性重算（对用户可达） | **absent**（recompute_plan 仅显式路由；ref 失效只标 ledger 不传播） | ref_lifecycle.py:175；api/routes/workflow_runtime.py:246 |
| 多步分析编排（Agent 可调用的生产执行缝） | **absent**（Pi 逐工具裸调；无步骤契约/产物封套/receipt） | 断点详见 §4 |
| AnalysisWorkflowContract / AnalysisArtifact 标识符 | absent（全仓 0 命中） | 全仓 grep |

## 3. 相邻已合并 PR（防重复建设的地基，全部只在其上 additive）

#1759 G01 数据集语义就绪、#1760 G02 科学契约、#1761 G03 工具对账、#1762 G05 任务验收 lab、#1763 G04 模板资产、#1756/#1755 h01/h02、#1771/#1773/#1829/#1830/#1834/#1835/#1836/#1867 g3d 系列（其中 **#1867 g3dv2-c 是同构先例**：run_3d_analysis 编排=capability 门→registry.dispatch 真实分发→MethodUseContract 摘要→staleness 绑定→嵌套结果提升铸 ref）。

## 4. 七个真实断链（多步分析用户视角；勘察 A 报告）

1. SessionPlan（可见进度）与 workflow_runtime V5 instance（可重算 DAG）不连通；chat 无任何代码驱动 `run_instance`。
2. Recipe DSL/V4 编译器不在生产执行热路径；缺「把 WorkflowProfile/typed_dag 交给执行器」的生产调用。
3. 局部重算对用户不可达：ref 失效 → instance 变更 → MapSpec 图层刷新的传播函数不存在。
4. harness_kernel `patch_plan`（runtime.py:1121）只是标记层。
5. G02 final gate 披露性不阻断；MethodUseContract 生产消费仅 scene_orchestration_tools.py:763 一处。
6. G05 验收是测试资产，线上无守门。
7. skill procedure 无 step 执行器；义务只在 eligibility/obligation 评估层。

## 5. 五工作流族缺口（勘察 B 报告摘要）

| 族 | 已有 | 缺口（本任务增量） |
|---|---|---|
| 行政区设施分布 | distribution.py 12 recipe；spatial_aggregate（显式分母/诚实 NaN）；h3_binning | M:N join 后的聚合收口与 cardinality 披露；工作流级组装/重算；分母缺失→typed blocked 不伪装密度 |
| 公共服务可达性 | accessibility.py 6 recipe；真 Dijkstra 等时圈；network_tools 10 工具 | 无路网→typed blocked（不得 15min 数值结论）；步骤链组装；跨步产物复用键 |
| 风险暴露 | risk.py 6 + exposure.py 4 recipe；overlay/buffer/zonal/dasymetric | 缓冲→叠加→暴露统计的链式契约；阈值/置信度警示；overlay 链 evidence 块 |
| 多期变化 | change_detection.py 6 recipe；detect_raster_change | 矢量双期对齐校验（年份/CRS）→差分→变化面积/百分比 + 不确定度披露 |
| 自适应统计分区 | statistics.py 10 recipe；moran/lisa/EB | H3/网格/行政组合链不产生虚假归属（分母/边界契约） |

横向缺口：矢量叠加族工具无 scientific_evidence/stats 披露（与插值族不对齐）；多步链无步骤级契约封套（输入角色→输出角色→单位/CRS 合同）。

## 6. 环境事实

- 系统 python 3.14.8 无依赖；**一律用主仓 venv**：`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.13.17，geopandas 1.1.4/shapely 2.1.2/networkx 3.6.1/h3 4.5.0/rasterio 1.5.0/scipy 1.18.0/pytest 9.1.1）。
- node v24.21.0 / pnpm 10.34.9；worktree 无 frontend/node_modules（需要时 symlink 主仓）。
- 测试入口：`pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 -q`；`python scripts/g05_task_acceptance.py run --profile smoke`；`scripts/ci-local.sh --fast`。
- 他人 worktree（webgis-wt-g3dv2-a-data / webgis-wt-g3dv2-b-scene / /tmp/master-check）只读不动。
