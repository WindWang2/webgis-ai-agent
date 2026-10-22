# G04 独立 Review（subagent C，第一轮）

- 方向：G04 模板资产权威与演进模型（ADR-0214 留白收口）
- 分支：`feat/g04-cartography-template-assets-vnext` @ `1e11df15`（4 commits，baseline `origin/master = ee8c14f8`，含 #1755/#1758）
- Worktree：`/home/kevin/project/webgis-g04-template-assets`
- 审查范围：`git diff origin/master...HEAD` 全量（29 files, +4924/−49）+ 磁盘上未跟踪文件核查
- 审查员未改所审代码；本文件与探针脚本（/tmp）之外无仓库写入。
- 验证环境：worktree `.venv`；pytest 全部真实退出码；前端 `node_modules/.bin/{vitest,tsc,eslint}`。

## Verdict：**FIX-FIRST**

阻断项（合并前必须清偿）：**P0-1、P1-1、P1-2、P2-1、P2-2、P2-3**。
理由：一个 P0（迁移未入提交 → 生产缺表）、一个既有基线测试被打破且提交信息谎称全绿、
一个核心功能（save-as-my-template）静默丢失用户编辑且其单测用不真实 fixture 假绿、
一个发布门绕过、一个跨租户可见性缺口、一个顺序依赖的测试污染（已复现红）。

---

## P0

### P0-1 迁移 0098 未入任何 commit（生产缺表）

- 位置：`migrations/versions/0098_composition_template_assets.py`（磁盘存在，`git status --porcelain` = `??`；`git log --all -- <path>` 无记录；`git diff origin/master...HEAD --stat` 的 29 个文件里没有它）。W2 commit `a88cd774` 信息却写「DB 表+迁移 0098」。
- 为什么重要：`app/core/database.py:191-197` —— `init_db()` 的 `create_all` 仅在 SQLite 下执行；非 sqlite「schema is owned by Alembic」。全新 clone 本分支 → `alembic upgrade head` 停在 0097 → `composition_template_assets` / `composition_template_asset_versions` 两张表在 Postgres 不存在 → 全部资产路由/工具 500。本地测试全绿只是因为 SQLite `create_all` 兜了底。
- 复现：`git diff origin/master...HEAD --name-only | grep 0098` → 空；`alembic heads`（磁盘态）= `0098_composition_template_assets`，而提交态 head = `0097_gc_node_result_owner_fp_unique`。
- 修复：`git add migrations/versions/0098_composition_template_assets.py` 并补提交（内容本身正确：additive、CHECK/UNIQUE 与模型一字不差、downgrade 反序、`_table_exists` 幂等，0048 先例合规）。

## P1

### P1-1 `migrations/env.py` 漏注册新模型模块 → 既有基线测试红

- 位置：`migrations/env.py:52-71`（显式 import 清单无 `app.models.composition_template_asset`）。
- 证据：`pytest tests/test_model_metadata_registration.py::test_alembic_env_imports_every_table_module` → **FAILED**：
  `AssertionError: migrations/env.py 缺少模型模块 import: ['composition_template_asset']`。
  对 `origin/master` 同规则静态回放：`missing = NONE`（基线绿，本分支打破）。
- 说明：`env.py:82-85` 的 `pkgutil.walk_packages` 兜底在**运行时**会 import 到该模块，真实 autogenerate 的 drop_table 风险被缓解；但仓库用静态 AST 契约钉死「每个表模块必须显式登记」，该测试即 CI 红。W4 提交信息「门全绿」不实。
- 修复：在 env.py 显式清单补 `import app.models.composition_template_asset  # noqa: F401, E402  (G04: ...)`。

### P1-2 save-as-my-template 变体派生静默丢弃大部分用户编辑（单测假绿）

- 位置：`app/services/templates/composition_assets.py:738`（`slot_id = str(comp.get("id") or ctype)` 取**组件实例 id**）与 `:765`（`if base_slot is None: continue` 静默跳过）。
- 机理：真实 apply 出的实例 id 由 `app/lib/cartography/component_abi.py:40-56` `INSTANCE_ID_BY_TYPE` 决定——`legend→legend-main`、`scale_bar→scale-bar`、`north_arrow→north-arrow`、`statistics_panel→statistics`、`chart_panel→chart-panel`、`map_border→map-border`、`continuous_colorbar→colorbar-main`、`export_layout→export-layout`、`inset_map→inset-map`，与模板槽位 id（`legend`/`scale_bar`/…）几乎全部不同；只有 `title/subtitle/attribution/graticule/annotation` 巧合相等。
- 复现（probe 2b，真 `apply_contract` 产物）：basic_thematic → `composition.standard_analysis` 后派生，`slot_overrides == {'subtitle'}`；用户对 legend（位置/variant）、scale_bar、north_arrow、statistics_panel、map_border 的全部编辑**无声蒸发**。保存后再 apply 得到的是基底模板版式，不是用户版式——工具描述「用户组件编辑成为 required 槽位——重放可复原结构」（`app/tools/composition_tools.py` webgis_save_composition_template description）对该多数组件不成立。
- 假绿单测：`tests/unit/test_composition_template_assets_db.py:287-315` 的合成 spec 用 `{"id": "legend"} / {"id": "scale_bar"} / {"id": "statistics_panel"}` 这类**永不出现在生产**的 id，遂「差异覆写」断言全过。
- 修复：按 `allowed_component_types`/`type→category` 把实例解析回基底槽位（而非实例 id）再算 diff；无法归槽的在场组件应触发 inline 回退而不是丢弃；新增「先真 apply 再 derive」的测试（或直接复用工具链 fixture）。

## P2

### P2-1 钉 revision 的 apply 走 for_replay → 绕过发布门（draft 可被应用）

- 位置：`app/tools/composition_tools.py:309,322`（`_pinned = template_asset_revision is not None` → `mode="for_replay"`）；`app/services/templates/composition_assets.py:508,535-543`（for_replay 只要版本行存在即放行，含 draft）。
- 复现（probe 1）：draft 资产 r1 的 validation 快照 = `['…preferred template ghost/x not found']`，`resolve_asset_for(..., 1, mode="for_replay")` 正常解析并物化；apply 的 conformance 预检是契约面（spec↔contract），不覆盖模板级校验 → 该未过门模板会写入 MapSpec。对 org/project 共享资产，非 creator 成员即可钉任何 draft revision 应用。
- 设计本意（退役资产钉版本重放）只应覆盖 **published→retired** 的旧版；draft 不在其列。
- 修复：钉版本 apply 要求 `version.status == "published"``（retired 已发布版放行）；或 apply 前对 draft pin 校验 validation 快照为空。补测试：pinned draft → 拒绝（reason=`asset_version_not_published`）。

### P2-2 project 可见性资产可挂到**外组织**项目（跨租户可见性）

- 位置：`app/services/templates/composition_assets.py:287-294`——`create_asset` 只校验 project **存在**，不校验调用者是项目成员/属主。
- 复现（probe 7）：u1（org 1）对属主 u2/org 2 的 `P-foreign` 建 `visibility="project"` 资产 → u2 `resolve_asset_for(project_ids=["P-foreign"])` **成功解析并 list 到**该资产（name/description/槽位结构对外披露）。方向是「攻击者把资产暴露给不相干项目」，叠加 project_id 可枚举性构成越权披露面。
- 修复：接受 `visibility=project` 前校验成员关系（`Project.owner_id == user_id or Project.org_id == org_id`，复用 `_caller_project_ids` 同口径）；补 IDOR 测试。

### P2-3 全局组合注册表被 W3/工具测试污染 → 顺序依赖红

- 位置：`tests/cartography/test_template_asset_apply_v1.py`（无 registry reset fixture；`_apply` → `resolve_asset` → `_resolve_builtin` → `upsert_asset`）与 `tests/unit/test_composition_asset_tools.py:113`（同链路）。
- 复现：`pytest tests/cartography/test_template_asset_apply_v1.py::test_locked_component_zero_touch_on_apply tests/cartography/test_composition_packs_v3.py -q` → **FAILED** `assert 30 == (8 + 21)`（`tests/cartography/test_composition_packs_v3.py:37`）。全量默认按字母序（c < t）恰好绿，但 `-k`/子集/xdist 分片/`--ff` 即红。
- 对比：`tests/cartography/test_template_assets_lib.py:36-45` 有 reset fixture 且注释明言「负例不污染其他文件的 pristine 校验测试」——W3 文件漏抄。
- 修复：两个文件补 autouse reset fixture（同 lib 文件款式）。

## P3（不阻断，建议随修或记录）

1. **org-NULL 资产**（probe 3）：JWT 无 org claim 时 `visibility="org"` 落库 `org_id=NULL` → 除 creator/admin 外永不可见，静默。路由 `app/api/routes/composition_template_assets.py:64-67`（org 来自 token claims）。建议创建时 org 缺失即 422 或明示。
2. **API 测试名不副实**：`tests/unit/test_composition_template_assets_api.py:200-221` `test_write_other_user_gets_403_when_org_shared`——docstring 承诺「同 org 可见(200) 不可写(403)」，实际断言 404（u2 token 无 org）+ admin 200，**从未断言 403**；HTTP 层 403 映射无覆盖（服务层有：db 测试 :190-191）。
3. **导出解析身份不全**：`app/api/routes/map.py:395-399` 只传 `user_id`（无 org_id/role）给 `resolve_replay_template` → org/project 共享或 admin 归属资产导出时 requiredness 静默 `not_evaluated`（诚实降级但验证面缩水）。
4. **for_replay 未钉版本取 latest（可能 draft）**：`composition_assets.py:533-534`（probe 5：latest_revision=2 draft 被返回）。导出路径从身份块钉 revision，仅身份块缺 `asset_revision` 时触达。
5. **list N+1**：`composition_assets.py:650`，30 行 → 32 条 SQL（probe 4）；被 `MAX_LIST_ASSETS=50` 兜住，可改一次 join/子查询。
6. **save 工具无会话属主守卫**：REST 双胞有 `verify_session_owner`（routes :151-154），工具面没有；与既有 agent 工具面（webgis_state_get 等）同款惯例，但本工具会把**他会话版面持久化**成调用者资产（读型 IDOR 的写侧后果）。建议工具内补 `ctx.session_id`/属主校验。
7. **内置资产损坏静默跳过**：routes :100-101 与工具 list 的 `except Exception: continue` 无日志。
8. **list 工具自由文本 reason code**：`db_unavailable: {exc}` 直接进 `reason_codes`（违反自有「结构化 reason codes」纪律，且异常文本泄入 agent 上下文）。
9. **`asset_count` 与截断不一致**：list 工具 `assets[:24]` 但 `asset_count=len(assets)`（limit≤20+4 builtin 时不可见）。
10. **docstring 与实现相反**：`AssetNewVersionRequest.session_id` 写「缺省保留原 payload」，代码要求显式 payload/session（routes :83-87 vs :262-266）。
11. **builtin 资产详情 404**：`GET /{asset_id}` 对 `asset.builtin.*` 返 404（DB 无行）而列表里有——同一资源两个面不一致。
12. **发布门不校验 `min_count <= max_count`**（`composition_templates.py:320-368` 无该项）；当前无消费方（全仓 grep 证实 min_count 在组合链路无消费者）故惰性，但属覆写可注入的不变量缺口。
13. **指纹只覆盖 payload 不覆盖物化内容**：基底模板跨部署漂移时指纹不变（`template_assets.py:183-194`）；仅用于展示/对账，非安全边界。
14. **apply 工具无 DB 失败降级**：`_caller_project_ids` 抛错时工具返回非 reason-coded 错误（list 工具有 builtin-only 降级，apply 没有）。
15. **前端 project 可见性死路**：面板提供 `project` 选项但无 project_id 输入（`composition-template-assets-panel.tsx:210-213`）→ 保存必 422。
16. **不可能失败的断言**：`test_template_asset_apply_v1.py:331-332`——`read_composition_identity` 对 `template_id` 做 `str(... or "")`（`composition_contract.py:501`），非 None 时 `isinstance(..., str)` 恒真；冒烟价值有限。
17. **弱化断言**：`test_template_assets_lib.py:358` `or report.preserved`（空 spec 下 preserved 恒空，暂不死，但属 tolerance branch）；W3 `:110` `if title is not None` 会在族缺 title 槽时静默跳过用户编辑段。
18. **误导性测试名**：`test_apply_asset_other_user_403` 实际断言 `asset_not_found`（404 优先设计正确，名字错）。
19. **身份块落盘新增空键**：`composition_contract.py:477` `identity.model_dump()` 使每次非资产 apply 也写入 `asset_id=""`/`asset_revision=0`（相对旧版逐位多两键；前向兼容，h02 golden 与 contract v1 绿，仅记录）。
20. **planning 与提交信息不符**：`planning/g04/TEST_MATRIX.md` 全表「待跑/待写」，而 commit 声称各门全绿（且 P1-1 证明并非全绿）。

---

## 复现用的探针证据（/tmp/g04_probe*.py，只读）

| Probe | 结论 |
|---|---|
| 1 | draft 资产 r1（validation 有 issue）经 for_replay 钉版本解析成功（工具链路同映射） |
| 2b | 真 apply spec 派生 `slot_overrides={'subtitle'}`，5/8 组件编辑被静默丢弃 |
| 3 | org 可见性 + 无 org claim → 行 `org_id=NULL`，仅 creator/admin 可见 |
| 4 | `list_assets` 30 行 32 条 SQL（N+1） |
| 5 | for_replay 未钉版本返回 draft latest_revision |
| 6 | 变体可靠链 builtin 资产 id（公共内容），DB 资产 id 因永不注册而 fail-closed → 无跨租户泄漏 |
| 7 | 外组织项目挂靠 → 外组织属主可解析/list 该资产 |
| 8 | savepoint 撞号重试正确重算 max+1（冲突 → revision 3，2 次尝试） |

## 测试执行记录（真实退出码）

| 范围 | 结果 |
|---|---|
| `tests/cartography/test_template_assets_lib.py` | 21 passed |
| `tests/unit/test_composition_template_assets_db.py` | 18 passed |
| `tests/unit/test_composition_asset_tools.py` | 14 passed |
| `tests/unit/test_composition_template_assets_api.py` | 10 passed |
| `tests/cartography/test_template_asset_apply_v1.py` | 11 passed |
| 回归：`test_composition_tools_v1.py` + `test_composition_contract_v1.py` + `test_h02_product_manifest.py` | 50 passed |
| 回归：`test_h02_golden_factory.py` | 56 passed |
| 回归：`test_tool_registry_rationalization.py` + `test_audit4_tools_meta.py` | 22 passed |
| `tests/test_import_boundaries.py` | 15 passed（lib↔services 红线保持） |
| `tests/test_alembic_metadata.py` + `tests/test_model_metadata_registration.py` | **1 failed, 10 passed**（失败即 P1-1；`test_alembic_metadata.py` 自身 5 项全过，含单头校验） |
| 顺序复现：W3 单测 + `test_composition_packs_v3.py` | **1 failed**（即 P2-3） |
| 前端 vitest（2 文件） | 14 passed |
| 前端 `tsc --noEmit` / `eslint`（改动文件） | 0 / 0 |
| ruff | 环境无 ruff 模块，**未验证**（W4 声称 ruff 绿无法复现） |

## 已核查「疑似问题但实际正确/设计如此」（附证据）

- **无第二注册表**：DB 资产解析零注册（`test_db_assets_never_register_globally`，spy 断言 `calls == []`）；仅 builtin for_apply 时 `upsert_asset`（`composition_assets.py:567-572`），且 `upsert_asset` 仅收 `asset.*` 前缀、revision 单调、`drop_asset` 拒系统 id（`composition_templates.py:261-290`，测试锁）。既有 `CompositionTemplateRegistry` 是唯一注册表。
- **基底不可跨租户引用**：`base_template_id` 指向 DB 资产 id 时 `base_not_found` fail-closed（永不注册）；指向 builtin 资产仅得公共内容（probe 6）。`derive_payload_from_spec` 显式排除 `asset.*` 基底（:728-729）。
- **可见性谓词**：private 不被 org 命中（强于样式模板隐式 org 子句）；匿名一律不可见 DB 资产；admin 全见；project 用服务端计算的 `project_ids`（Project 表查询，非用户输入，不可伪造）；404 优先于 403（`_require_writable` :174-183 + db 测试矩阵）。
- **版本并发**：`uq_cta_revision` + savepoint max+1 重试经 probe 8 证实重算正确；失败尝试不脏会话（`begin_nested` 回滚后重算 latest）。
- **restore 语义**：新 revision 再发布，不重写历史；`current_revision` 取 max 单调（:341-342）；retire 后钉版本只读重放、新 apply 拒（reason=`asset_retired`）。
- **CompositionIdentity additive**：旧 spec 读得 `asset_id=""`/`asset_revision=0`，原键逐位保留（`test_identity_additive_defaults_preserve_old_spec`）；`apply_contract` 钉版本模板 `template_id_mismatch` fail-closed。
- **manifest 注入**：`extra_templates` 以 asset id 为键（= 物化模板 id），无注册表污染；无注入时诚实 `not_evaluated`。
- **单写入口**：资产 apply 复用 `webgis_apply_composition` → conformance 预检 → 锁预检 → `apply_contract` → `mapspec_store.layout_set`（SetLayoutIntent）；`webgis_save_composition_template` 只读会话、不碰 MapSpec。
- **ComponentType 词表未扩**：覆写字段白名单（`_OVERRIDABLE_SLOT_FIELDS`）只能引用既有类型；发布门复用 `validate_template` 单一口径（类型词表/preferred/zone/fallback），无第二校验器。
- **与 #1755/#1758 无语义冲突**：master 已含二者；G04 对 `product_manifest.py` / `gis_harness/tools.py` 的改动均为 additive（composition 块追加 asset 投影；产品身份对 `asset.*` 模板取注册表 revision，DB 资产本就不进注册表故保持弱版本语义，注释已披露）。
- **前端注入面**：资产 id 一律 `encodeURIComponent`（测试锁）；错误走 `isApiError` 分支，不裸抛。

## 修复顺序建议

1. P0-1（git add 迁移）→ P1-1（env.py import）→ 重跑 `tests/test_model_metadata_registration.py` + `tests/test_alembic_metadata.py` + `tests/test_deploy_migration_wiring.py`。
2. P1-2（实例→槽位归位 + inline 回退 + 真实链路测试）。
3. P2-2（project 成员校验 + IDOR 测试）→ P2-1（draft pin 门 + 测试）→ P2-3（两个测试文件补 reset fixture）。
4. P3 随修或逐条记录进 TASKS/DECISIONS；TEST_MATRIX.md 回填真实退出码（含 ruff 实跑证据）。

---
---

# 第二轮 Review（subagent C，修复批次 `e53fd29e` 之后）

复审范围：`git diff 1e11df15..HEAD`（8 files, +358/−14）+ 关键调用链重读 + 探针复验
（/tmp/g04_probe4.py；旧行为对照直接加载 `git show 1e11df15:app/services/templates/composition_assets.py` 原模块执行）。

## 结论：**SHIP**（6 个阻断项全部关闭且有证据；余项为 P3，建议进 PR 风险段披露）

## 阻断项逐条核销

### P0-1 迁移入库 —— 关闭 ✅
- `git ls-files migrations/versions/0098_composition_template_assets.py` 命中；提交于 `e53fd29e`；`git diff HEAD -- <file>` 空（与一审内容逐字一致）。
- 真跑 `DATABASE_URL=sqlite:////tmp/... alembic upgrade head`：0097→0098 执行，两表建成；
  `uq_cta_revision(asset_id,revision)` 唯一约束 + 4 个 CHECK 全部在 alembic 产物中；
  `alembic downgrade 0097` 两表干净移除。`tests/test_deploy_migration_wiring.py` 21 passed。

### P1-1 env.py 注册 —— 关闭 ✅
- `migrations/env.py:71` 新增 `import app.models.composition_template_asset`。
- `pytest tests/test_model_metadata_registration.py` → **3 passed**（一审为 1 failed）。

### P1-2 save 派生保真 —— 关闭 ✅（行为经探针实证；但回归测试 potency 不足，见 R2-新-1）
- 新实现：`_resolve_slot_id`（`composition_assets.py:814-825`）按**类型**匹配 base 声明序首个允许该类型的槽位
  ——与 `apply_contract` 的槽位认领口径一致（apply 按契约/模板声明序逐槽找在场实例，实例按类型匹配；
  单实例同型场景两者等价；多实例同型时 apply 本就只建 primary 单实例，派生去重后覆写同一槽位，语义对齐）。
- unresolved（在场组件类型不在任何 base 槽位）→ `_inline_payload_from_slots` 诚实降级（`unresolved` 计数 :789-806）。
- 探针实证（R2-1/R2-2）：
  - legend 移到 top-right → 覆写 `{"position_zone": "top-right", ...}`；物化后再 apply，legend 实例 position=top-right（端到端保真）；
  - scale_bar 禁用 → 覆写 `cardinality=optional, required=False`；
  - inline 降级保真：uncertainty-panel → `cardinality=required, required=True, position_zone=bottom-left, allowed_component_types=[uncertainty_panel]`；禁用的 north_arrow → optional/False；legend variant=academic → `preferred_templates=["legend/academic"]`。
- `test_derive_payload_falls_back_inline_for_extra_components` 是**真红测试**（旧代码下 `inline_template is None` → 断言失败，已验证）。

### P2-1 钉版本发布门 —— 关闭 ✅（for_replay 分支；但该分支无测试覆盖，见 R2-新-2）
- `resolve_asset_for` 新增 `elif version.status == "draft": raise asset_version_not_published`（:562-566）：
  for_apply/for_replay **双态拒 draft**；published/retired 重放放行。
- 探针实证（R2-3）：draft pin × for_apply/for_replay 均拒；retired published pin 重放 OK；
  latest=draft 的未钉重放 → `resolve_replay_template` 返回 None → 导出 requiredness `not_evaluated`（诚实降级，不渲染未过门载荷）。
- 工具层 pinned→for_replay 映射在新语义下安全（钉版本不再能带入 draft）；W3 既有退役重放/钉版本测试不回归。

### P2-2 外国项目挂靠 —— 关闭 ✅
- `_project_accessible`（:174-181）：属主本人或同 org；`create_asset` :302-309 校验。
- 探针实证（R2-5）：外国项目（org 99/owner other）拒（Forbidden）；同 org 项目、本人属主项目放行；
  不存在项目仍 `asset_invalid_visibility`；admin 挂外国项目同样拒（保守，fail-closed，记录为设计选择）。
- REST 与工具双入口都走同一 `create_asset`（routes :184），覆盖无遗漏。
- `test_project_asset_foreign_project_rejected` 为真红测试（旧代码下外国项目可建成 → 不断抛错 → 失败）。

### P2-3 注册表顺序污染 —— 关闭 ✅
- 两个 e2e 文件均加 autouse `_registry_pristine`（apply_v1 :45-56；asset_tools :27-38）。
- 一审复现命令复跑：`test_locked_component_zero_touch_on_apply + test_composition_packs_v3.py` → **10 passed**（原 1 failed）。
- 反方向 + 合并进程：`asset_tools + packs` → 23 passed；6 个 G04 文件 + packs 同进程 → **87 passed**。

## 新调用链核查（coordinator 点名项）

7. **save 工具 session-owner guard**（composition_tools.py:800-819）：`HTTPException → missing_session` 映射正确
   （`verify_session_owner` 不存在/越权统一 404）；`finally: await db_gen.aclose()` 保证会话关闭
   （`get_async_db` 的 async/sync 回退两条路径都能被 aclose 收尾）；非 HTTPException（DB 故障）按工具面惯例
   上抛为 dispatch 异常（与 apply 工具 `_caller_project_ids` 同款），不进 reason code——可接受。
   `_authorize` 语义为真：用户不匹配/匿名无 token/legacy NULL/NULL 全 fail-closed。缺口：无**负向**测试
   （foreign session → missing_session 未测，只有 clean_session 补 Conversation 主行后的正路）。
8. **export replay 身份传递**（map.py:387-404）：`_uid/_org/_role` 在闭包定义前同作用域计算，捕获正确；
   `_user` 来自 `get_current_user_with_version`（恒 dict），`actor_ids` 吃 dict/None 均可；
   org 共享/admin 资产现在能在导出解析到模板（R2 顺带关闭一审 P3-3 的身份不全问题）。
9. 另注：`app/tools/composition_tools.py:30` 新增 `from fastapi import HTTPException`——app/tools 下唯一 fastapi import；
   边界规则只禁 tools→app.api，`tests/test_import_boundaries.py` 15 passed，非违规；记名备查。

## R2-新发现（均 P3，不阻断）

1. **`test_derive_payload_preserves_all_edits_after_real_apply` 是弱测试**：用 git 旧模块实证——旧代码下
   `overrides={'subtitle'}`（legend/scale_bar 等编辑已丢），该测试两条断言（keys⊆base_slots、present 类型被 base 覆盖）
   **全过**。它不断言任何真实编辑被保留。建议补：apply 后把 legend position 改掉 → 断言 overrides["legend"]["position_zone"]。
   （真正兜住 P1-2 的是 inline 回退那条测试。）
2. **P2-1 修复分支无测试**：`test_pinned_draft_revision_apply_rejected` 用默认 `mode="for_apply"`（旧代码本就拒 draft pin）；
   实际修的 for_replay 拒 draft 分支零覆盖。建议该测试改 `mode="for_replay"`（=工具 pinned 映射）或两态都测。
3. **save 属主守卫无负向测试**（见上 7）。
4. **无 Conversation 行的 mapspec 会话不能再 save**：守卫先于创建执行，mapspec-only 会话（无 chat 会话行）→ missing_session。fail-closed，行为变化记录备查。
5. **角例**：组件类型不匹配任何 base 槽位、但实例 id 恰等于某 base 槽位 id 时，覆写会落到该槽位并带入异型 `allowed_component_types`（发布门可通过、语义古怪）。需类型被改错的组件 id，低概率。
6. **一审 P3 未修项确认仍开放**（coordinator 将在 PR 风险段披露）：org-NULL unshareable、list N+1、前端 project 无 project_id 输入、
   `db_unavailable: {exc}` 自由文本 reason code、asset_count vs `assets[:24]`、builtin detail 404、fingerprint 忽略 base drift；
   另两条「已收紧」实际未动：`test_corrupt_identity_block_tolerated` 仍恒真（`read_composition_identity` 对 template_id 做 `str()` 强转，
   非 None 时必为 str——本轮只把表达式赋值给变量，逻辑等价）；`test_template_assets_lib.py:358` 的 `or report.preserved` tolerance branch 仍在。
7. **`AssetNewVersionRequest.session_id` docstring 与实现相反**（routes :83-87 vs :262-266）仍未修。

## 第二轮测试执行记录（真实退出码）

| 范围 | 结果 |
|---|---|
| `tests/test_model_metadata_registration.py` | 3 passed（P1-1 关闭） |
| `tests/test_deploy_migration_wiring.py` | 21 passed（P0-1 关闭） |
| 6 个 G04 文件 + `test_composition_packs_v3.py` 同进程 | 87 passed（P2-3 关闭） |
| 一审污染复现命令（W3 单测 + packs） | 10 passed（原 1 failed） |
| 反方向（asset_tools + packs） | 23 passed |
| 回归：composition_tools_v1 + contract_v1 + h02_manifest + h02_golden_factory + import_boundaries + alembic_metadata | 129 passed |
| alembic upgrade head / downgrade（真库） | 两表建成/移除，uq+CHECK 齐全 |
| 前端 | 修复批次无前端改动；一审 vitest 14 / tsc 0 / eslint 0 仍有效 |
| ruff | 仍不可用（环境无模块），PR 前须补跑 |

## 最终 verdict

**SHIP。** 6 个阻断项全部关闭且均有可复现证据（含真 alembic  upgrade/downgrade 与旧模块对照实验）。
建议（非阻断）：合并前把 R2-新-1/R2-新-2 两条弱测试补强（各一行断言），save 守卫补一条负向测试；
P3 清单（含 R2-新-3~7）进 PR 风险段披露；TEST_MATRIX.md 回填真实退出码与 ruff 实跑证据。
