# G19-TYPED-FIELD-COMPUTE — INTERFACES & 共享接线清单

本文件即本任务的**契约登记**（不占用 ADR 数字编号：仓库 `docs/adr/` 已有 0286/0287 重号，且 G15/G16/G20/G21 会各自拍定编号；用无碰撞命名避免冲突）。

## 1. 所有权边界

| 面 | G19 独占 | 明确不碰 |
|---|---|---|
| 后端域 | `app/services/data_preparation/field_compute/**`（新） | `data_preparation/{plan,executor,service,vocabulary,field_proposal,spatial_receipt}.py` 不改 |
| API | `app/api/routes/field_compute.py`（新，独立 router）+ `app/schemas/field_compute_schema.py`（新） | 既有 7 个 `/api/v1/data-preparation/*` 端点不改 |
| 工具 | `app/tools/field_compute_tools.py`（新） | `data_preparation_tools.py`、`registry.py`、dispatcher、Pi bridge 不改 |
| 前端 | `frontend/components/sidebar/data-preparation/field-compute-panel.tsx`、`frontend/lib/api/field-compute.ts`、`frontend/messages/*/fieldCompute.json`（新） | `prepare-flow.tsx` 不改；主题/全局布局/侧栏骨架/面板宽度不改 |
| schema | 无 MapSpec schema 变更（本任务不产出地图文档字段） | G15 frame / G16 label / G20 raster / G21 flow 子模型 |
| ADR | 无编号契约（本文件 + 模块 docstring） | 不占用任何 ADR 数字 |

## 2. 共享接线位置（最小 additive，逐条可审）

| 文件 | 改动 | 兼容约定 |
|---|---|---|
| `app/main.py` | +1 `from app.api.routes import field_compute as ...`，+1 `app.include_router(..., prefix="/api/v1", tags=["字段计算"])` | 新 router 路径前缀 `field-compute/*`，与既有 `data-preparation/*` 不重叠；不改既有 include 顺序语义 |
| `app/tools/__init__.py` | +1 注册项 `("app.tools.field_compute_tools", "register_field_compute_tools")` | 加在 `data_preparation_tools` 之后；新工具名 `field_compute_*` 不撞 registry 现有名 |
| `frontend/lib/i18n/messages.ts` | +2 import（zh/en `fieldCompute.json`）+ `AppMessages` 类型 1 字段 + 两处 messages 映射 | 不重排既有键；缺键时 TS 类型检查会报错，两个 locale 同步提供 |
| `frontend/components/sidebar/data-sources-tab.tsx` | +1 import + 在 `activeSubTab === 'prepare'` panel 内 `<PrepareFlow/>` 下方挂 `<FieldComputePanel/>`（一行 JSX） | **不新增 subtab、不改 tab 按钮/宽度/主题**；与 #2032 的冲突面仅这一处 panel 内部，PR 中列出 |
| `frontend/messages/{zh-CN,en-US}/fieldCompute.json` | 新文件 | 键集合一致（实测 137/137） |
| `scripts/generate_endpoint_scope_matrix.py` | +1 条 `DOMAIN_RULES` 规则 `"field-compute": ("gis:read","gis:write")` | 新域端点必须显式 scope；纯新增一行，不改既有规则 |
| `tests/quality/snapshots/openapi.json` | 由 `API_SNAPSHOT_UPDATE=1` 刷新 | additive 新端点；无 path 删除/破坏性变更 |
| `docs/api-docs.md` | 由 `scripts/gen_api_docs.py` 刷新生成区 | 生成器产物，不手写漂移 |
| `docs/dev/endpoint-scope-matrix.csv` | 由 `scripts/generate_endpoint_scope_matrix.py` 重新生成 | 9 个新端点补齐矩阵行 |
| `docs/field-compute-contract.md` + `app/services/data_preparation/field_compute/gen_help.py` | 新增（面向用户帮助 + 契约驱动生成脚本） | 函数表由 `function_help()` 生成，新增函数须升版本并重跑 |

**生成物归属**：以上 4 个生成物/规则按**本分支当前版本**独立完成并随 PR 提交，不把必需工作留给合并者；合入其他并行 PR 后按最新主干重新校正（`INTERFACES.md §6`）。

**冲突段披露**：唯一与并行 PR 共改的共享文件是 `app/main.py`、`app/tools/__init__.py`、`frontend/lib/i18n/messages.ts`、`frontend/components/sidebar/data-sources-tab.tsx`；改动均为**纯新增行**，无格式化/重命名/删除。合入其他 PR 后按当前主干重新校正。

## 3. 消费既有契约（不复制）

- `session_data_manager.get/store`（`app/services/session_data.py:356/621`）——受权 ref 读取与 `prefix="fc"` 新 ref 写入；**不 overwrite 原件**。
- `app/lib/gis/measurement.py::CANONICAL_UNITS` / `UnitDimension` / `derive_field_semantics` / `legend_unit_display` ——单位词表与证据分级；字段名只作 `metadata_derived` 提示。
- `app/lib/gis/crs_safety.py::classify_crs` / `recommend_metric_crs` ——CRS 资格闸；`pyproj.Geod` 做大地测量面积/长度；shapely 仅用于 local metric 平面面积。
- `app/lib/cancellation.py::current_token` / `OperationCancelled` ——协作取消。
- `app/services/artifact_registry.py::register_artifact`、`app/services/dataset_semantics/provenance.py::record_transformation`、`derive_descriptor_from_v3` ——additive 登记（Windows 基线缺陷下降级披露）。
- `app/tools/registry.py::tool`、`app/tools/upload_tools.py::_resolve_session_id` ——标准 `@tool` 与会话归属。
- `app/lib/data/profile.py::profile_features` ——输出画像（可选，失败降级）。
- 前端 `listUploads`、`dataFabricApi.fetchRefGeoJSON`、`useHudStore.addLayer/updateLayer`、`useToastStore`、`useT` ——与 `prepare-flow.tsx` 同一范式。

## 4. 本任务公开契约（版本号入指纹）

- `IR_VERSION = "g19-ir-1"`，`FUNCTION_SET_VERSION = "g19-fc-1.0"`，`RECEIPT_VERSION = 1`。
- `compute_fingerprint = sha256(canonical{input_ref, content_hash, schema_fingerprint, scope, columns[IR], function_set_version, field_units, timezone, error_policy, unselected_policy, crs/crs_source})[:16]`。
- 计划状态：`compiled → previewed → executed (| cancelled | rejected)`；失败计划可重新编译，已执行计划不可回退。
- 输出封套（工具/API 共用）：`{success, status, output_ref?, source_ref, formula_summary, units, scope, row_count, computed_rows, null_count, error_count, errors_sample[], precision_policy, persistence, next_actions[]}`；`unknown / not_evaluated / blocked / partial` 保持真实语义，不冒充 success。
- 范围：`all_rows` / `selection`（必须完整、稳定 ID、selection_revision；截断一律拒 `SELECTION_UNRELIABLE`）/ `predicate`（closed 谓词子集）。`unselected_policy` 默认 `null`（全体行保留，未选中行新列为 null）；`subset_export` 是独立显式布尔，默认 false。
- 错误策略：`abort`（遇首个行级错误终止整次）或 `null`（错误行结果=null 并披露计数与有限示例）；默认 `null`（与准备域 additive 降级模式一致），计划中可见。
- 持久化：`persistence="session_ref"`（默认，会话级，如实披露生命周期）或 `"project_artifact"`（显式 `project_id`，走既有项目产物登记；未成功则 `partial` 而非 success）。

## 5. 对 G12–G21 / 开放 PR 的兼容

| 关系 | 处理 |
|---|---|
| G17（导入） | 只读「已可读 ref」；用当前 master 的普通 GeoJSON/上传夹具验收，不依赖其新协议 |
| G18（导出） | 不等其下载器；`session_data_manager.get` + 现有读取接口验证回读 |
| G14（要素编辑/ID/lineage） | 先用既有稳定 ID；无可靠 ID 时拒绝精确 selection 更新，不伪造数组下标身份；不写原件/undo journal |
| G13（资格/可用性） | 工具按既有 `@tool` 描述契约声明能力，执行继续走既有 dispatch 门控；不改其政策 |
| G12（Pi runtime） | 不改 pin/RPC/extension；Pi-only 下工具经既有 dispatch 调用，桥不可用由宿主如实 `unavailable/503` |
| G15/G16/G20/G21 | 不改 MapSpec schema 的 frame/label/raster/flow 子模型；单幅制图沿用既有路径 |
| #2032（UI 重设计） | 只加功能专属子组件与一处局部挂载，见 §2 冲突段 |
| #2022（G10 刷新） | 不做自动刷新；提交前版本重核与 `SNAPSHOT_CHANGED` 是独立的计算一致性语义 |
| #2021（G11 简报） | 不碰 |

## 6. 合并前后校正

PR 前 `git fetch origin --prune`，按最新 `origin/master` 与最新 PR/review 处理本分支兼容；共享文件若被并行 PR 改动，只做最小冲突解决（保留双方新增行），并重跑受影响回归。不 cherry-pick 他人未合并提交，不强推已推送分支。
