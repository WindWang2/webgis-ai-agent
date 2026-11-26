# Baseline — 2026-11-26

- 基线 SHA：origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（merge #2266，2026-11-20）
- 分支：`zcode/goal-gis-model-workflow-authoring-20261125`；worktree：`webgis-wt-gis-model-workflow-authoring-20261125`
- 开放功能 PR（避让）：#2272 method_experiment（39 文件）、#2273 data_fusion（46）、#2274 paper_scale（40）、#2275 scenario_harness（40）；依赖 PR #2267–#2271。
- 共享接线交叉：`app/tools/__init__.py`（4 PR 全碰）、`app/main.py`（2272/2273/2275）、`frontend/app/page.tsx`+`frontend/lib/i18n/messages.ts`（2272/2273）、`app/models/__init__.py`（2273）。
- Migration：master 单 head `0109_data_join_runs_job_id_bigint`；#2273 占 `0110_data_fusion_authoring`；两个 sibling goal worktree 无新增迁移。
- Pi pin：本任务不负责 Pi vendor pin（方向 01 的职责）；按现状观察。

## 三层权威（侦察结论）

1. **GIS 方法知识库**（只读消费）：`CartographyRecipe`+WorkflowProfile V2（25 个 recipe_packs 领域包、15 数据角色、科学义务、7 维完成契约、语义回退）；`compile_workflow` 15 阶段（query→recipe→资格→typed DAG→包）。
2. **V4/V5/V6 harness runtime**（会话级执行权威，不重写）：`TypedWorkflowGraph`（端口/CRS/单位/副作用）→ `WorkflowPackage`（不可变、semver、指纹）→ `PackageRegistry`（owner/org/project 作用域、draft/published/deprecated）→ `InstanceStore`（两级 CAS+租约+journal）→ `Driver`（波次/绑定门/复用指纹/重试/取消/补偿/子工作流）。REST 13 端点齐全（含 changes dry_run、clone、inspector）。
3. **项目持久工作流**（`workflow_engine.py`）：Workflow/WorkflowRevision（不可变快照）/WorkflowRun（snapshot+manifest+部分完成步骤）+ replay/resume/rerun_from_step/compare_runs；从成功 SessionPlan 确定性 promote（`workflow_promotion.py`，blockers 门槛）。

## 技能/扩展现状

- 轨迹归纳链（induction 四步+三重沙盒门）完整但只有离线 CLI 入口，未接运行时检索/执行。
- `SkillLibrary` 只读 core YAML；skill_jobs 面（prepare/start/presets/drift 复核）成熟；TRUSTED_PACKS=("core",) 写死。
- 扩展平台：manifest→certification→marketplace 全链但是运维 CLI 面；`WorkflowPackSpec` 可投影 recipe（ns 前缀+编译期校验）。
- 工具：`ToolRegistry`（descriptor 校验白名单、三指纹）+ `ToolDispatchService`（调度唯一拥有者、tier3 确认闸）；Pi 经 `NATIVE_TOOL_NAMES` 固定清单 + `/pi-tools` HTTP 面调用。

## 可执行性事实（WP6 依据）

driver 执行阶梯：测试钩子 > cartography 真实渲染（kind=cartography，matplotlib→PNG→blob）> science 真实聚合（`_WIRED = category_breakdown/admin_aggregation/rate_aggregation`，data_fabric AggSpec count/sum/distinct_count）> 派发面 > geocompute in-process（transform 可执行 op：buffer/clip/dissolve/overlay/filter/reproject；aggregate 内联诚实不可执行）。data_input/output = 绑定传递（bound_ref 必须先绑）。未接线 analysis → `NODE_NOT_EXECUTABLE` 诚实阻断。

## 缺口 → WP 映射（经三线侦察证实）

| 缺口 | WP | 落点 |
| --- | --- | --- |
| 无用户 authoring graph 入口（编译唯一入口是 query 文本；package_id=recipe_id） | WP1/WP2 | 新 `model_authoring` 域，包 identity `umodel_` 前缀 |
| 无提交前整图静态验证端点（验证分散在编译期/准入期/绑定门） | WP2 | `validate.py` 汇聚 capability/端口/参数/CRS/角色/资源 |
| 无参数实例化 REST 通路（RunRequest 仅 deadline_s；`run_instance(node_params)` 是内部签名） | WP2/WP5 | 实例化 adapter + 参数解析（`$param:` 引用） |
| 无可视化编辑器/参数面板/连线校验 UI | WP3 | 自绘 SVG 画布（lineage-graph 先例）+ undo 框架扩展 |
| 包无 draft→发布→撤回用户面（status 二值无产品流）、无导入导出 | WP4 | drafts 表（新迁移）+ PackageRegistry 发布 + 小型模型文档导入导出 |
| 无面向用户的运行/回放/局部重算产品面（inspector API 已有，UI 只有列表徽章） | WP5 | 复用 instance/events/recompute-plan REST + 画布 overlay |
| induced 技能未接检索/执行；包不能作为技能被检索 | WP4 | 发布模型经 scoped 检索面（不做全局技能库扩张） |

## 边界声明

- 不重写 workflow_runtime scheduler/driver；不加 V7 状态机；发布复用 PackageRegistry/WorkflowPackageRow（第二 registry=禁止）。
- 不实现 #2272–#2275 的功能域；共享文件仅最小接线。
- Pi vendor pin 不改（不归本任务）；无 Pi live 证据时如实标注。
