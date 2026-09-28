# ADR-0216: 架构边界与共享 Contract Kernel（app/contracts 归位）

- 状态：Accepted
- 日期：2026-09-29
- 方向：H01（架构边界与共享 Contract Kernel）
- 基线：master `77d2678d`（初稿基于 `930459ef`，rebase 收敛 #1566/#1564 并行合并）
- 关联：issues #1541（lib↔services 双向耦合）、#1542（core/auth 反向依赖 services）、#1546（extensions 三棵树）

## 背景与问题

F/G 波次大量合并后，三处底层依赖边界失守：

1. **lib ↔ services 双向耦合区**（#1541）：`app/lib/cartography/` 10 个文件 import `app.services`——`render_work_projection.py:33` 模块级引 `governor.render_budget.RenderWorkInput`；`quality_loop.py:648`/`runtime_repair.py:158` **函数体内**懒导入 `mapspec.lifecycle_engine`（注释自称"防循环"——懒导入正是循环依赖压力的直接证据）。反向 65 个 services 文件 import lib，边界名存实亡。
2. **core 层倒置**（#1542）：`app/core/auth.py:698` 函数级 `from app.services.history_service_async import AsyncHistoryService`——core 是 mypy 棘轮白名单区（`files=["app/core"]`），反向依赖使类型与测试边界失真。另侦察发现同族既有边：core/errors→lib.cancellation、core/logging_config→lib.runtime.context、core/auth→tools._utils。
3. **extensions 三棵同名树**（#1546）：根 `extensions/`（4 个 extdemo 示例 pack）、`app/extensions/`（Pi 桥 .mjs 资产，非空壳）、`app/extensions_platform/`（权威平台，48 文件）——"extension"一词三个所指。

## 决策

### D1：契约 kernel = `app/contracts/`

新增最底层包 `app/contracts/`，只收**被 ≥2 层消费、自身零 app 内依赖（至多 stdlib/pydantic/app.models）、语义为形状/词表/纯计算**的对象。落位五件 + 一 Protocol：

| kernel 模块 | 内容 | 自 |
|---|---|---|
| `render_work.py` | RenderWorkInput + render_work_units + export_dpi_factor + 工作量系数 | services/governor/render_budget.py |
| `cartography_components.py` | ComponentType/Position 词表 + ComponentPlacement/CartographyComponent + normalize_placement + 纯 payload 校验器 | services/gis_harness/components.py（契约核；工厂/突变/variant 目录权威留 services） |
| `mapspec_intents.py` | SetViewIntent/SetSceneIntent/PatchLayerPresentationIntent | services/mapspec/lifecycle_engine.py |
| `workbench_locks.py` | LOCK_CONFLICT_CODE + locked_layer_ids_of/locked_component_ids_of/is_entity_locked | 同上 |
| `completion.py` | F_RENDER_APPLY_FAILED + MapCompletionFinding（bounded dataclass） | services/gis_harness/completion/contracts.py |

兼容策略：services 原模块保留 **re-export shim**（`components.py` 为全量 shim 面保留非契约部分并 re-export 契约核），36+ 引用方零改动；`tests/test_contract_kernel.py` 锁定旧 path 与 shim≡kernel 同一对象。

拒绝项：`workflow_v4/methodology.py`（992 行自包含但有状态单例）不整体下沉——**参数注入**（`plan_composition_for_method(registry=...)`，缺席显式 ValueError）代替，遵循 `methodology.py:878` 既有 registry 参数惯用法。

### D2：core 反向边清偿

**#1542 守卫归属**（与并行合并的 #1566 收敛）：`verify_session_owner` /
`require_owned_session` 的唯一 services 用途是
`AsyncHistoryService(db).get_session_meta(...)`。PR #1566（已合并于
rebase 基线 77d2678d）将守卫整体迁至 `app/services/auth_history_bridge.py`
（services → core 方向合法），~20 个路由调用方只改 import 来源，签名与
404 语义逐字保留，并以 `tests/test_core_layer_boundaries.py` AST 守卫
锁定。本 PR 采纳该方案为权威（放弃本 PR 原案的 Protocol + 组合根注入，
避免同一 seam 两套机制），并保留其语义回归（`tests/test_contract_kernel.py`
守卫 404 用例改锚 bridge 模块）。

**本 PR 补齐的同族既有边**（#1566 未覆盖）：`app/core/auth.py` WS 守卫的
`app.tools._utils.async_db_session` 懒导入 —— 实现归位
`app/core/database.py`（tools 保留 re-export，既有 7 消费方零改动）；
`app/core/errors.py` → `lib.cancellation` 的分类面 lazy import ——
`CooperativeCancellation` 标记基类入 core，lib 具体异常继承之；
`app/core/logging_config.py` → `lib.runtime.context` —— RuntimeContext
关联原语 git mv 至 `app/core/runtime_context.py`（原路径全量 shim，
29 引用方零改动）。

### D3：import 边界门禁 = 标准库 AST（零新依赖）

`scripts/check_import_boundaries.py`：AST 扫描 `app/` 全部 .py **含函数级 import**；规则 core→{services,api,lib,schemas,tools,extensions_platform}、contracts→上层、lib/cartography→{services,api,tools,extensions_platform} 零容忍。豁免仅两种：`if TYPE_CHECKING:` 块（类型面非运行时边）与 `# h01:allow` 行内注释（逃生口，测试锁定当前 0 使用）。挂载三处：ci-local.sh contract tier + 独立 step、production.yml lint job、`tests/test_import_boundaries.py`（真实树 PASS + 6 类负向 fixture）。

### D4：extensions 三棵树裁决

- `app/extensions_platform/`：唯一权威 Python 平台（不改名——纯 churn）。
- `app/extensions/webgis-tools/index.mjs`：部署面 Pi 桥扩展（main.py 生产装载），留在 `app/extensions/`（deployed extensions 语义正确）。
- 根 `extensions/` 示例 pack → `git mv examples/extensions/`（rename 保历史）；4 个平台测试路径、10 篇 docs/extension-platform 活文档、pack 内 `EXTENSIONS_DIRS` 示例值同步。
- UBIQUITOUS_LANGUAGE 增 Extension trees 词条表；CONTEXT.md 登记裁决。

## 后果

- 正面：lib/cartography 运行时反向边 0（S1 复核 10 文件 11 处全消）；core 运行时 services/tools/lib 反向边 0；`quality_loop ⇄ lifecycle_engine` 真循环随锁/intent 下沉自然消解；门禁防回归可本地一条命令复现；`examples/extensions` 命名自解释。
- 中性：估工/锁/findings 语义逐位不变（回归测试锁定数值与前缀匹配语义）；OpenAPI 无路由变化。
- 成本：`components.py`（1320 行）拆为契约核（kernel）+ 组装权威（services）两半；旧 import path 依赖 shim —— 后续新代码应直取 `app.contracts`。
- 已知留白（Out of Scope）：lib 其他子包（geo_analysis/harness/gis/quality…约 30 文件）与 app/schemas 3 文件的 services 边、`lifecycle_engine` 3445 行巨石、`data_fabric` 反向引 API 路由缓存（#1544）——gate 规则表已留扩展位，按同法逐包清偿。
