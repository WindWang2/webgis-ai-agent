# G04 RECON — 专业制图模板与组件资产生命周期（2026-10-22）

基线：`origin/master = ee8c14f840755bd47df45a107cd4ff08515ecfd2`（Merge PR #1758）。
worktree：`/home/kevin/project/webgis-g04-template-assets`，分支 `feat/g04-cartography-template-assets-vnext`。
侦察方式：subagent A（Explore，只读）全仓测绘 + 主 Agent 逐文件核验载荷最重断言（composition_templates / composition_contract / composition_tools / versioning / scope / template_version 模型与 0048 迁移 / template_catalog / product identity / manifest）。

## 0. 与任务书假设的差异（以代码事实为准）

1. **#1758 已合并**（任务书写作时 OPEN）。master HEAD 即其 merge commit；本方向从该点分叉，无文件级冲突需要避让；其改动的 `frontend/lib/mapspec-compiler/`、`semantic_checks*`、`planner.py` 不是本方向的写入目标。
2. `planning/GOAL04_WORKLOG.md` 是**旧编号 Goal 04（Pi Tool ABI V2，PR #1620）** 的遗留日志，与本次 G04（制图模板资产）方向无关，不重演、不修改。
3. ADR-0214 非目标原文（docs/adr/0214:141-142）：「不做用户自定义 composition template 的 DB 存储面（只读身份与锁定；存储面是独立方向，需 scope/租户/退役路径完整设计）」——**G04 正是该留白方向的立项**，不是与 0214 冲突。

## 1. 模板家族真值归属（file:line 证据）

| 家族 | 定义 | 持久化 | 消费方 |
|---|---|---|---|
| 样式模板（basemap/symbology/layout/thematic 五 kind，CHECK 只含四种+composite 走 payload） | `app/schemas/template_schema.py`（SEED_TEMPLATES 静态 + pydantic） | **DB** `cartography_templates`（`app/models/db_model.py:255-284`）+ 快照 `template_versions`（`app/models/template_version.py:42-80`，迁移 0048） | HTTP `app/api/routes/templates.py`、`template_versions.py`；agent 工具 `app/tools/templates.py`；`TemplateCatalog.get_style_template` |
| 组合模板（MapCompositionTemplate） | `app/lib/cartography/composition_templates.py:42-55`；seeds :58-228；packs `composition_packs/*` | **仅静态注册表** `get_composition_template_registry()`（:341-346，进程单例；register 撞 id ValueError :247-250） | composer/resolver、plan_composition、apply_contract、conformance、`_product_composition_identity`、manifest component_requiredness |
| 组件模板（变体） | `app/lib/cartography/component_templates.py:15-33` | 静态 seed；`template_version` 字段是死字段 | 槽位 preferred_templates、apply 物化、plan_component_replace |
| 产品模板 | `app/services/gis_harness/product_templates.py:41-79` | 静态 seed + `deprecated` 布尔 | TemplateCatalog、MapProductPlanner |
| TemplateSpecV2 | `app/lib/cartography/template_intelligence.py:79-116` | 静态 curated（12 份） | plan_composition 评分选基底 |
| ProductManifest | `app/lib/cartography/product_manifest.py:125,243` | 纯函数派生 + export sidecar `.manifest.json` | `app/api/routes/map.py` export 路径 |

## 2. 组合工具与 MapSpec 写入链（单一通道）

- 三工具在 `app/tools/composition_tools.py`（注册于 `app/tools/__init__.py:66`）：discover(:149-215，只读/无 DB)、apply(:261-376)、plan_replace(:420-552)。
- apply 链：ContractRegistry 取契约 → conformance 预检（error 级 fail-closed :289-306）→ workbench 锁预检（:314-329）→ `apply_contract` 纯函数重放（`composition_contract.py:253-457`）→ **`mapspec_store.layout_set(components/component_links/composition/expected_revision, origin="agent", actor="composition_apply")`**（:339-348）→ SetLayoutIntent（`app/services/mapspec/intents.py:211-223`）落 `layout.composition`。
- 失败面 reason codes：`COMPOSITION_TOOL_REASON_CODES`（:35-45，测试锁）。
- `CompositionIdentity`（`composition_contract.py:123-144`）：template_id/template_version/contract_id/contract_fingerprint/component_abi_version/component_versions/applied_revision；随 layout 入 `cartographic_fingerprint`。
- `contract_fingerprint` = canonical JSON sha256（:203-209）；`diff_contracts` 有界结构 diff（:212-247）。

## 3. DB/迁移/RBAC 惯例（照抄对象）

- 迁移：alembic `migrations/versions/`，最新主线编号 **0097**（`0097_gc_node_result_owner_fp_unique`，down=0096）。additive 新表 + create_all-coexistence guard + downgrade 反序 = `0048_template_versions.py` 模式；漂移闸测试 `tests/test_alembic_metadata.py`、`tests/test_deploy_migration_wiring.py`。
- 并发版本号：savepoint 内 max+1 重试 ×3 → 409 语义（`versioning.py:187-225`）。
- 跨租户：scope 子句 `builtin ∪ creator ∪ org`（`scope.py:17-29`，**严禁 org_id IS NULL 当 public**）；SEC-02 授权谓词注入（can_write/can_read_parent，:139-149）；不可见 404 不泄露存在性（routes/template_versions.py:66-74）；工具面 ContextVar 身份 `_caller_identity()`（tools/templates.py:82-96）+ DB 失败降级 `[]` 只服务 builtin（:113-142）。
- 测试建表：`tests/conftest.py` 每 worker 独立 SQLite + `init_db()`；单测惯用 `create_engine("sqlite:///:memory:") + Base.metadata.create_all`（`tests/unit/test_cartography_templates_db.py:20-37`）。

## 4. 产品路径（#1755/ADR-0228）挂点

- `app/services/gis_harness/tools.py:205` `_product_composition_identity`：产品组装写身份块；**用户显式契约身份（contract_id 非空）不被覆盖（user-wins）**；template_version 目前取 `tpl.schema_version`（=1，弱版本语义——G04 用 asset id+revision 强化）。
- `product_manifest.py:177-204`：manifest["composition"] 读身份块投影 template_id/template_version/component_abi_version；:298+ component_requiredness 查组合注册表（未注册 → not_evaluated/template_not_registered，诚实降级）。
- 产品 conformance 复跑：`app/services/gis_harness/product_runtime.py:139-208`。

## 5. 前端

- `frontend/lib/map-components/`：resolve-components.ts（live+export 共用解析）、manager.ts（CAS patch）、component-catalog.generated.json（schema 5，21 类型，registry-parity.test.ts 锁）。
- 样式模板 UI：`frontend/components/drawers/template-gallery-v2.tsx` + `frontend/lib/api/templates.ts`。**组合模板无用户挑选 UI**（组合身份只由 agent 工具/产品路径写入）。
- 前端无 `lockedComponentIds` 写入点（锁 UI 未闭环——不在本方向抢做）。

## 6. 缺口裁决（done / partial / missing）

| 缺口 | 裁决 | 证据 |
|---|---|---|
| 用户自定义组合模板 DB 存储 | **missing**（ADR-0214 显式留白） | §1 |
| 资产生命周期 draft→published→retired + restore | **partial**（样式模板有快照+软失效；无状态机/发布门/回滚端点；组合模板全无） | versioning.py 全文 |
| org/project 共享作用域（模板域） | **partial**（样式模板 builtin∪creator∪org；无 project 级先例） | scope.py |
| 资产引用 id+revision 流入 apply/产品身份 | **partial**（契约 template_id+语义版本+指纹已入身份块；applied_revision 是 MapSpec mutation revision 非资产 revision；产品路径弱版本） | §2/§4 |
| restore/rollback | MapSpec 层已有 checkpoint/rollback；模板版本层零件齐缺端点语义 | §3 |
| user-wins / 锁 / 导出一致性 | **done**（复用，不动） | §2/§4 |

## 7. 红线（实现必须遵守）

lib 不 import services（ADR-0216）；槽位语义单一真值在 MapCompositionTemplate（契约只引用）；组件突变单一入口不变；ComponentType 词表不扩（ADR-0238）；工具载荷有界+结构化 reason codes+禁自由文本 query；不建第二注册表/第二 MapSpec 写入口/第二产品一致性 checker（ADR-0214/0228）；`org_id IS NULL` ≠ public；agent 不得代用户置锁。
