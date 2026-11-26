# WP3 实现简报（交给实现 agent）— 系列地图作者计划与跨产品一致性

仓库：`/home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125`（独立 worktree，
分支 `zcode/goal-carto-design-series-20261125`）。基线 master 8fe3d7663 + 本任务 WP1 已提交
（a5868c089：`app/services/design_specs/` 权威模型已存在）。

## 你独占的文件（其他 agent 不碰）

- `app/services/map_series/`（新包：`__init__.py`, `contract.py`, `errors.py`, `store.py`,
  `service.py`, `propagation.py`, `consistency.py`, `freeze.py`）
- `app/models/map_series.py`（新）
- `migrations/versions/g51_map_series.py`（新；down_revision=`g50_carto_design_series`）
- `app/tools/map_series_tools.py`（新）
- `app/api/routes/map_series.py`（新）
- `tests/unit/map_series/`（新）
- 公共接线（各加 1-2 行 additive）：`app/tools/__init__.py`（`_TOOL_MODULES` 尾部）、
  `app/main.py`（router include 区、design_specs_routes 之后）

**禁触**：`app/services/design_specs/*`、`app/services/mapspec/*`、`app/services/atlas_batch/*`、
`app/lib/cartography/*`（全部只读消费）、`frontend/`（WP4 另有人做）、
`app/lib/cartography/multiscale/*`、`app/api/routes/mapspec_mutations.py`（开放 PR #2274/#2275 领域）。

## 权威与纪律（必须遵守）

1. **不建第二地图状态**。每成员的真实地图状态 = 各自 session 的权威 MapSpec
   （`app/services/mapspec/store.py` + lifecycle engine CAS）。系列只存**引用与计划**。
2. **设计规范 pin**：成员引用 `design_spec_id + version`（WP1 权威，见
   `app/services/design_specs/versioning.py`）。规范退役后既有 pin 继续可解析（软废弃）。
3. **传播 = 逐图 CAS**：每成员经既有 mutation 通道落图（读 `apply_gis_mutation` /
   lifecycle engine 的调用方式，参考 `app/services/print_layout_authoring/service.py:292`
   apply 的 CAS 用法与 `expected_revision`）。**绝不宣称原子批量**——逐图 receipt
   `{member_id, status: committed|superseded|failed|skipped_locked, revision, error?}`。
   失败不掩盖；被用户锁定（user-wins）的成员/字段 skip 并披露。
4. **不把某图 ref 传播到其他图**：传播内容只能是规范投影出的表达意图（palette/
   classification 方法/组件/版式参数），数据引用（ref:）逐图绑定各自会话。
5. **成员身份**：`session_id + mapspec_fingerprint`（读 store 取 fingerprint 的方式见
   `app/services/mapspec/store.py`；`.fp` sidecar）。freeze 时物化文档+指纹（见 6）。
6. **freeze（本 WP 只做 plan 结构与 preview；真实生产执行在 WP5，不要实现渲染）**：
   freeze plan 数据结构含每成员 `{session_id, mapspec_fingerprint, print_layout_digest?,
   data_fingerprints（来源层 data_digest 或 ref 描述指纹）, title, design_spec_id+version,
   output_profile}`。冻结语义 = 计划层面 pin（可序列化 JSON），不复制渲染字节。

## 数据模型（migration g51，照 g50 先例）

- `cartography_map_series` 主表：id(String36)/org_id/creator_id/is_builtin/name/description/
  status(draft|published|retired)/design_spec_id(nullable FK 无约束——跨库）/spec_version pin/
  members(JSON 有界 ≤50)/comparability(JSON)/dimension_tags(JSON)/output(JSON)/
  structural_digest/created_at/updated_at/retired_at/retirement_note。
- `cartography_map_series_versions` 不可变快照表（同 g50 版本语义：max+1 savepoint 重试、
  parent 继承 ≤16、软废弃）。scope/授权谓词照 `app/services/design_specs/versioning.py`
  与 `app/services/templates/scope.py`（builtin ∪ creator ∪ org；SEC-02；写=creator 或
  同 org admin）。

## contract.py（typed pydantic，extra=forbid，有界）

`map_series.v1`：
- `name/description`
- `design_spec_id`（必填）+ `design_spec_version`（可选 pin；缺省=latest 解析时点记录）
- `members: list[SeriesMember]` ≤50：
  `{member_id(lower_snake,唯一), title≤160, subtitle≤160?, session_id,
   role in (primary|comparison|context|detail), dimension_tags: list[str]≤8,
   extent_override?: [w,s,e,n], data_bindings: {role_name: field_name}≤8,
   locked_fields: list[str]≤16（user-wins 锁）, enabled: bool=True}`
- `comparability`：`{shared_classification: bool, shared_palette: bool,
   shared_extent_by_role: bool, unit_contract: {role_name: unit}≤8}`
- `dimension`：`{axis: region|time|indicator|theme, labels: list[str]≤50}`
- `output`：`{targets≤4, paper_profile?, dpi 72-600}`
- 防泄漏巡检：**复用** `app/services/design_specs/contract.py` 的 walk 纪律
  （import 其 `_walk_leak_findings` 或复制最小实现并注明来源）——成员 payload 禁止
  ref: 游标/凭证/内联 GeoJSON/数据断点（data_bindings 只放字段名）。

## 一致性检查（consistency.py，结构化 findings）

`run_series_consistency(db, series_id) -> {findings: [...], ok}`。finding 形状：
`{code, severity in (info|warning|error), member_id?, component?, message, fix_hint?}`。
封闭词表 codes（每条真实现，不许占位）：
- `SPEC_PIN_MISSING`（error，成员无规范 pin 或规范不可解析）
- `UNIT_MISMATCH`（error，同 comparable 角色/同指标跨成员单位不一致——从成员会话
  MapSpec 的专题层字段与 spec data_roles 对照；读不到会话数据如实 `not_evaluated`
  finding `SESSION_UNAVAILABLE`）
- `CLASSIFICATION_SCOPE_CONFLICT`（error：comparability.shared_classification=true 但
  成员间 legend/classification 方法不一致——从 MapSpec 图层 paint method 读）
- `PALETTE_MISMATCH_SHARED`（error：shared_palette=true 且成员 palette 不同）
- `TITLE_MISSING`（warning）/ `SOURCE_MISSING`（warning：图面 attribution/source 组件缺席）
- `LEGEND_MISSING`（warning：专题层无 legend 组件且无 legend_spec）
- `ASSET_VERSION_DRIFT`（warning：成员 symbol_asset 绑定 revision 不一致）
- `EXTENT_DIVERGENCE`（info：非 extent_override 成员间 bbox 显著不一致，仅披露）
- `LOCKED_FIELD_CONFLICT`（warning：传播意图与成员 locked_fields 相交）
上限 ≤100 findings；每条可定位到 member/component。
**负例**：不同指标不强制同色域——检查只对 `dimension.axis==indicator` 且 comparability
声明的可比组生效，否则不产生 PALETTE_MISMATCH（写测试锁定）。

## 传播（propagation.py）

- `build_propagation_preview(db, series_id, *, spec_version=None, intents: dict) -> preview`：
  列出受影响成员、每成员将变更的字段、锁定字段、失效资产、不可解析成员、逐成员
  eligibility。**只读**。
- `apply_propagation(db, series_id, *, spec_version=None, intents: dict, actor) -> receipts`：
  逐成员调既有 mutation 通道（one by one；每成员独立 try/except；单成员失败不影响
  后续；返回逐图 receipts；绝不抛出后中断整批——分批语义如实）。
  intents 形状由你定义但必须 typed（建议：`{palette?, classification_method?, k?,
  components?: [{component_type, op: ensure|remove|update, options?}], typography?}`），
  并在 preview/apply 两侧同构。

## REST（app/api/routes/map_series.py）与工具（app/tools/map_series_tools.py）

- REST：`/api/v1/map-series`：GET 列表 / POST 创建 / GET {id} / POST {id}/versions /
  POST {id}/members(增删改单个成员) / GET {id}/consistency / POST {id}/propagation/preview /
  POST {id}/propagation/apply / POST {id}/freeze-plan（本期返回冻结计划 JSON，不执行生产）。
  授权与错误映射照 `app/api/routes/design_specs.py`（我已写好，直接抄形状）。
- 工具（Pi 经 webgis_execute 直达）：`webgis_map_series_list/create/inspect/add_member/
  consistency/propagation_preview/propagation_apply`。@tool 纪律照
  `app/tools/design_spec_tools.py`（`_actor_context`/`_guard` 可 import 复用
  `app.tools.design_spec_tools._actor_context`，或复制并注明；**_guard 的 functools.wraps
  必须保留**——@tool 依赖签名内省）。`data_mutations=("project_memory_write",)`。
  side_effect：读=pure；apply/成员写=state_mutation。

## 测试（tests/unit/map_series/，pytest --no-cov 全绿）

- contract 负例：成员超预算/重复 member_id/未知 role/泄漏字段/schema extra。
- 版本化：单调/继承深合并/跨系列 parent 拒绝/软废弃兼容读/命名冲突 409。
- 一致性：构造 2-3 成员（合成 session MapSpec 用 `app/services/mapspec` 真实引擎建；
  参考 `tests/cartography/atlas_batch/fixtures.py` 的 district_document 合成法）→
  各 codes 正例+负例（特别是「不同指标不报 PALETTE_MISMATCH」）。
- 传播 preview：锁定成员 skip 披露；不可解析成员诚实；apply：逐图 receipts、
  单图失败不中断、superseded（expected_revision 过期）如实。
- REST 全路径 + 授权（TestClient 约定照 tests/unit/design_specs/test_design_spec_api.py，
  记得造 User 行——auth 校验用户存在）。
- migration g51 upgrade/downgrade 往返（DATABASE_URL 指向 /tmp 临时 sqlite 跑 alembic）。

## 验收自检（完成后逐条确认）

1. `pytest tests/unit/map_series --no-cov -q` 全绿。
2. `ruff check` 你新增的全部文件 clean。
3. `python -c "import app.main"` 不炸。
4. 真实 MapSpec 引擎建 2 成员会话 → consistency → preview → apply receipts 端到端
   （单测内，无 mock 引擎）。
5. 不碰禁触清单任何一个文件（`git status` 证明改动面 = 你独占清单+两处接线行）。
6. 更新 `planning/goal-carto-design-series-20261125/progress.md`（追加一行 WP3 记录）
   并原子 commit（message: `feat(cartography): map series authoring plan and consistency (WP3)`）。

遇到接口对不上（例如 lifecycle engine apply 的真实签名）以**读源码为准**，不要猜。
完成后报告：改动文件清单、测试输出尾部、关键决策（含与简报的偏离及理由）。
