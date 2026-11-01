# PR_OVERLAP_MATRIX — GOAL 03 temporal map products

基线 master `6c5e71c2c`（= PR #1875 合并点）。规则：每个 M 里程碑开始与提交 PR 前重新 fetch 复查。

| PR | 主题 | 交叠面 | 判定 | 动作 |
|---|---|---|---|---|
| #1868 (merged) | 多源数据准备与语义绑定 | 数据准备/ref 语义 | reuse | 只消费 ref/descriptor 既有接口 |
| #1869 (merged) | 分析工作流编排 | temporal 工具已被编排 | reuse | 不新建编排层；temporal_view_* 为薄控制工具 |
| #1870 (merged) | 制图语义决策/候选 | 表达模型层 | reuse | 分级 breaks 语义对齐其表达模型词汇 |
| #1871 (merged) | atlas durable jobs | M6 时序页导出 | **extend(additive)** | AtlasPolicy.driver + "time"、AtlasPageBinding + time 窗口字段、manifest + 时间证据；不重写 job/PDF 引擎 |
| #1873 (merged) | 要素编辑 | 无直接交叠 | avoid | 不触碰 |
| #1874 (merged) | map_operation.v1 事务 | 效果词表 | avoid(受控) | 不向 EFFECT_KINDS 加时间效果（任务书禁止未审查 effect）；独立 temporal_view_* 工具 + 既有 CAS |
| #1875 (merged) | linked views + aggregate/分页 | where AST/aggregate/paged hooks | **extend(additive)** | 复用 where AST 通道与 hooks；时间参数为 additive 可选项；不复制聚合实现 |
| #1836/#1835/#1834/#1830/#1829 (merged) | 3D 栈 | 无 | avoid | 不触碰 |
| #1762 (merged) | G05 任务验收 | F 编号 | extend | 新 F 编号以最新主线为准（F29+，见 TASK_PLAN M9） |
| open issues #1807-#1866 (audit 系列) | 审计缺陷 | #1837 直接相关 | avoid+登记 | #1837（cube window transform 不平移）不认领；本任务 raster 路径仅 x0=y0=0 全窗读规避，新代码不复刻同类缺陷；其余不碰 |
| #1841-#1843 (security) | 信任边界 | 无 | avoid | 新代码不复制同类信任边界（时间工具全部经 require_owned_session/属主校验复用） |

共享文件最小变更清单（提交前须再 diff 确认无新 PR 触碰）：
- `app/lib/cartography/mapspec_schema.py`（+temporal 字段/版本 1.8/导出面）——additive
- `app/lib/cartography/quality_loop.py`（cartographic_projection + "temporal" 一行）——additive
- `app/services/mapspec/intents.py` / `mutation_registry.py`（+SetTemporalViewIntent/codec/handler 注册）——additive
- `app/services/mapspec/mutation_handlers.py`（+_handle_settemporalview）——additive 函数
- `app/tools/__init__.py`（+1 注册行）——additive
- `app/services/gis_harness/requirement_ir/contracts.py`（TimeSpec additive 字段）+ `normalize.py`（短语映射）
- `app/lib/cartography/publication_ir.py` / `atlas_job_spec.py` / `app/services/atlas_batch/{planner,manifest}.py`（time driver additive）
- `frontend/components/map/map-panel.tsx`（最小插槽：temporal controller 挂载 + 时间过滤合成）——插槽式
- `frontend/lib/map-commands/catalogue.ts`（+temporalCommands 域片）
- `frontend/lib/map-components/viewport-aggregate.ts` / `paged-table-data.ts` / `lib/data-plane/paged-features.ts`（additive 时间参数）
- `app/lib/cartography/ts_projection.py` 产物 `frontend/lib/mapspec-compiler/types.generated.ts`（再生）
- openapi/api-docs 快照（若 REST 面变化：本任务零新端点，预期不动）

新独占目录（本任务所有权）：
- `app/services/gis_harness/temporal_products/`（契约服务/帧推导/时间过滤编译器/工具面逻辑）
- `app/tools/temporal_view_tools.py`
- `frontend/lib/temporal-map/`、`frontend/components/map/temporal-*`
- `tests/unit/gis_harness/temporal_products/`、`tests/unit/tools/test_temporal_view_tools.py`、前端 vitest 配套

复查记录：
- 2026-11-01（M0）：fetch 后 origin/master 仍 `6c5e71c2c`，无 >#1875 PR，无 open PR 交叠。

## 复查记录（终）
- 2026-11-01（PR 创建前）：origin/master 仍 6c5e71c2c；新 open PR **#1876**（GOAL 01 spatial semantics，`zcode/goal-spatial-semantics-contract-v1`）—— 文件交叠仅 `app/tools/__init__.py`（各自 +1 注册行）与 `app/tools/categories.py`（各自 +1 分类条目），纯 additive 并行添加，合并时无语义冲突；其余零交叠（其 spatial_semantics/ 目录 vs 本 temporal_products/；其 graduated-legend vs 本 temporal-*）。
