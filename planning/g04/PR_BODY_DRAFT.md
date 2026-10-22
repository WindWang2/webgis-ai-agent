## Why / Now

ADR-0214 交付了 versioned 组合契约 + 组件 ABI + Agent 组合工具面，并在非目标中
显式留白：「不做用户自定义 composition template 的 DB 存储面（存储面是独立
方向，需 scope/租户/退役路径完整设计）」。截至本 PR，组合模板
（`MapCompositionTemplate`）全部是进程内静态注册表（seed + packs）：用户调好
的版面无法保存、共享、复用、版本化——只能留在会话里。

本 PR 收口该留白：**组合模板资产生命周期 V1**（存储/版本/授权/退役/恢复 +
MapSpec/Harness 真实消费 + 窄前端入口），不动模板引擎、不重做 #1755。

## 与 #1758 等 PR 的 overlap 与不重复证据

- **#1758（已合并，基线内）**：本 PR 从 `ee8c14f8`（#1758 merge commit）分叉；
  未触碰其 `semantic_checks*` / `frontend/lib/mapspec-compiler/` / `planner.py`；
  对 `composition_tools.py` 的修改是 additive 参数与新工具（既有 14 个测试零改）。
- **#1755（ADR-0228 产品 manifest，已合并）**：组合身份投影 additive 扩展
  （`asset_id`/`asset_revision` 两键 + `extra_templates` 显式注入参数），未复制
  第二份一致性 checker；产品路径 user-wins 语义原样保留。
- **open PR（执行时）**：#1759（G01 dataset readiness）、#1698/#1701
  （dependabot）——零文件交叠（见 `planning/g04/PR_OVERLAP_MATRIX.md`）。

## 改动（33 files, +5448/−49）

**lib 契约层**（纯函数、无 IO）：`app/lib/cartography/template_assets.py`（资产
载荷/物化/指纹/发布门校验/内置 4 族）、`composition_templates.py`（registry
`upsert_asset` 单调 + `validate_template` 单模板口径抽出）、
`composition_contract.py`（`CompositionIdentity` additive 资产字段 +
`apply_contract(template=…)` 钉版本参数）、`product_manifest.py`（additive
投影 + `extra_templates`）。

**存储/服务**：`app/models/composition_template_asset.py`（两表）+ 迁移 0098
（additive + guard + downgrade）+ `app/services/templates/composition_assets.py`
（生命周期状态机/作用域/解析两态/save 派生）+ REST 路由 +
`app/main.py` 挂载。

**生产接线**：`app/tools/composition_tools.py`（apply 资产分支 + list/save 两
工具 + reason codes additive）、`app/services/gis_harness/tools.py`（产品身份
资产强引用）、`app/api/routes/map.py`（export 前 replay 模板注入）。

**前端窄入口**：api client + 资产面板（保存为我的模板/列表/应用分发）+ i18n
双语。

**文档**：ADR-0230、planning/g04/ 七件套、.goal-loop-ledger.md 记账。

## DoD 对照

- 资产版本/授权闭合：draft→published→retired + restore（新 revision），
  private/org/project scope，404-before-403，admin 全见；旧版本可定位
  （list_versions），用户地图不被静默替换（user-wins + 锁零触碰 + 钉版本重放）。
- ≥4 类成果真实链路：4 族内置资产各过 apply→user edit→幂等重放→export
  manifest 语义一致性（`test_template_asset_apply_v1.py`）。
- typed diff/compat/reason：fingerprint（asset-sha256）、发布门校验快照、
  reason codes 词表（工具面 additive）。
- 无第二目录/第二 MapSpec/第二 QA：写入口唯一 `layout_set`；DB 资产不进
  全局注册表（跨租户纪律）；manifest 扩展非复制。
- 独立 review 两轮无未解决 P0/P1（见下）。

## 测试（真实退出码，基线对照）

| 面 | 命令 | 结果 |
|---|---|---|
| 制图 lane 回归 | `pytest -m cartography --no-cov -q` | **2345P/19S/3xf/0F**（基线 2345P/19S/3xf/0F，逐项一致） |
| G04 新测试 | 4 个文件 | 21+22+10+14+11 = **78 passed** |
| 存量受影响面 | tools/contract/manifest/templates-api/template-versions/golden-factory | **179 passed** |
| 迁移/元数据闸 | alembic metadata + deploy wiring + model registration | **32 passed** |
| ruff | `ruff check app/ tests/` | All checks passed |
| 前端 | tsc / vitest / eslint / CJK 门 | exit 0 / 23+9 passed / exit 0 / passed |
| 顺序矩阵 | e2e + composition_packs_v3 交叉 | 23 passed（review P2-3 复现场景转绿） |

跳过/未覆盖（如实披露）：全库 suite 未跑（#1753 已知挂起）；多 pod Redis 并发
未离线复现；线上 CI 未等待（仓库约定）。

## Review-Fix-Review

- R1（独立 reviewer，未参与实现）：**FIX-FIRST** —— P0×1（迁移未入库）、
  P1×2（env import 缺失致元数据闸红；save 派生按实例 id 丢 5/8 编辑）、
  P2×3（钉版本绕过发布门、外国项目挂靠、注册表顺序污染）+ P3×20。
- 修复：5 个 commit，每项配红测试回归（P1-2/P2-1/P2-2/P2-3 均有钉住测试）。
- R2（同 reviewer 复审）：结论见 `planning/g04/REVIEW.md` 追加节。

## 风险 / 回退

- **风险**：DB 资产不进进程注册表 → 每请求解析（有界 ≤50 行）；org-NULL
  JWT token 创建的资产无法 org 共享（样式模板线同款约束）；list_assets 有
  N+1（上限 50）；fingerprint 不含 base 模板漂移（base 版本链由系统侧负责）。
- **回退**：迁移 downgrade 反序删两表（0098）；代码侧全部 additive——摘除
  新文件后既有链路逐位回退（`CompositionIdentity` 旧字段零变化，工具既有
  参数路径不变）；前端入口可单独摘除。

## 后续扩展（不属本方向）

组件级细粒度权限；资产跨 org 市场；模板继承链（version parent）；资产
使用统计/推荐排序。
