# G41 BASELINE — 双变量与可靠性表达作者工作台

记录时间：2026-11-14（北京时间） / 执行 UTC 戳 `20261114T151807Z`

## 身份与基线

| 项 | 实测值 | 证据 |
|---|---|---|
| remote `nameWithOwner` | `WindWang2/webgis-ai-agent` | `gh repo view --json nameWithOwner` |
| defaultBranchRef | `master` | `gh repo view --json defaultBranchRef` |
| 最新 base SHA | `1276692737a8cf58d0604fb40ae0e70fc61779a2` | `git fetch origin master` 后 `git rev-parse origin/master`；= 研究快照 |
| base HEAD 标题 | `Merge pull request #2116: close restore, cluster, quota, and background-step gaps` | `git log origin/master -1` |
| open PR | **0** | `gh pr list --state open --limit 100`（空输出，非查询失败） |
| open issues | **0** | `gh issue list --state open --limit 200 --json number \| jq length` = 0 |
| 最近 60 个 closed PR | 已分页读取 #2116 → #2021 | `gh pr list --state closed --limit 60` |
| #2094–#2116 GitHub reviews / inline / conversation | **均为空**（与快照一致）；G29–G35 独立评审在 `planning/G29-*/REVIEW.md` 等 | 逐 PR `gh api .../reviews`, `.../comments`, `.../pulls/N/comments` |
| Pi pin | `vendor/pi` gitlink `b4afdddf44e059c67ccf5097e5317d913521b470` = v1.5.2；`PI_MIN_VENDOR_TAG=v1.5.1` | `.gitmodules`, `git ls-tree HEAD vendor/pi`, `docs/PI_RUNTIME_CONTRACT.md:4-5`, `app/services/chat/pi_compat.py:45` |
| 主工作区 HEAD | `816705218a5aa...`（落后；有 `M vendor/pi` 与大量未跟踪 `.claude/skills/*`） | `git status --short`、`git log -1`；**未清空、未修改** |
| 本任务 worktree | `/home/kevin/projects/webgis/webgis-worktrees/g41-bivariate-reliability-authoring-20261114T151807Z` | `git worktree add -b ... origin/master` |
| 本任务 branch | `zcode/g41-bivariate-reliability-authoring-20261114T151807Z` | 同上 |
| 统一重阶段锁 | `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock` | `git rev-parse --path-format=absolute --git-common-dir` |

## 文档与硬规则（本次实读）

- 根 / `app/` / `app/services/` / `app/lib/cartography/` **均无** `AGENTS.md`；只有 `frontend/AGENTS.md`（Next.js 生成，与后端无关）。`docs/adr/` 无 index，只有 `RENUMBERING_MAP.md`。
- `CLAUDE.md:41`：**Never wait for CI results**（与本任务「不等线上 CI」一致）。
- `docs/architecture.md:202-219`：分层 `contracts→models→core→lib→schemas→services→api→main`；`lib/cartography` 不得 import services/api；工具入参最严格 pydantic；Zero Big Data in Context；**MapSpec 是唯一 desired state，新 mutation 必须经 `apply_gis_mutation`**；失败诚实披露；架构级变更先写 ADR；门禁 `scripts/check_import_boundaries.py`。
- `docs/PI_RUNTIME_CONTRACT.md:13,18`：`dispatch_tool` 唯一管线；禁止平行工具注册中心 / 平行 MapSpec / 第二渲染器 / 第二编排器。ChatEngine fallback 是**合法但必须三态披露**的降级态（:108-113），历史 architecture 文档描述的下线不等于全面禁止，本次不恢复任何 fallback。
- `CONTEXT.md:265-274`：`legend_spec` 是专题样式单一事实源，`thematic_spec.spec_to_paint` 是 paint 投影单一构造点。
- `thematic_authoring/__init__.py:1-7`：不新建第二套撤销栈 / MapSpec 存储 / 分类算法。
- `pytest.ini`：`testpaths=tests`、`pythonpath=.`、`asyncio_mode=auto`、`timeout=180`。
- QUALITY_MANIFEST：新注册工具必须有静态测试引用。

## 环境实测（决定验证层级）

| 能力 | 实测 | 影响 |
|---|---|---|
| Python 环境 | 主工作区 `.venv` 为空壳（0 site-packages）；**可复用** `webgis-wt-g25-.../.venv`（pytest 9.2.0 / pydantic 2.14.2 / weasyprint 70.0 / PIL 13 / lxml / httpx / numpy / shapely） | 用 `PYTHONPATH=<G41 tree>` + `cd <G41 tree>` 解析本任务 `app`（已实测） |
| `vendor/pi` 本工作树 | **未初始化、空目录** | 直接跑 Pi journey 必然 skip |
| 同仓已构建 Pi | `webgis-worktrees/g33-.../vendor/pi` 已完整构建，`dist/bundle/rpc-entry.js` 存在，submodule HEAD = `b4afdddf...`（= 固定 pin） | 可在本 worktree 内按资源纪律初始化**同一固定 pin** 后真跑旅程；详见 LOOP_LEDGER |
| frontend `node_modules` | 主工作区 1.3G；本工作树按同仓既有惯例符号链接 | 已建 `frontend/node_modules -> 主工作区/frontend/node_modules`（gitignore，无 diff） |
| node / pnpm | node v22.22.2 / pnpm 10.34.9 | 满足 `build-vendor-pi.sh` 的 ≥22.19 |
| 导出工具 | `rsvg-convert` 系统可用；weasyprint 可用；`reportlab`/`cairosvg` 缺失 | SVG→PNG 用 rsvg-convert，SVG→PDF 用 weasyprint HTML(SVG) 链 |
| 磁盘 | 433G 可用 | 足够 |

## 已确认的旧合同（影响新设计）

1. `bivariate.py:82-89` `_bin_index` 用严格 `>`：**值恰等断点归下类**。
2. `thematic_authoring/stats.py:45-59` `assign_class` 用 `>=`：**恰等断点归上类**（MapLibre `step`）。两者语义相反 → 新模式必须显式 `boundary_rule` 字段，旧图不得无声变色。
3. `bivariate.py:24-42` 仅登记 3×3 两族色阵（`BiPurpleOrange` / `BiTealRose`）；`SUPPORTED_BIVARIATE_N=(2,3)`；2×2 是四角派生；**无 16 色阵**，n=4 必须拒绝。
4. `compute_bivariate_classes` 就地写 `__biv_class`（会覆盖用户字段）；本任务必须生成**冻结展示副本**与独立派生字段名。
5. `thematic_authoring/service.py:224-286` `_hydrate_features` 分开读 descriptor 与内容，缺 revision 回退 0 —— **不是强版本快照**；G41 不得复用该水合函数作为版本保证。
6. `session_data.py:436,505-507`：`store` 把 `_ref_revisions[ref]=1`，`overwrite` 单调 `+1`；`get_ref_descriptor():934-944` 把 live revision stamp 进 descriptor；`get():621-651` 返回 deepcopy（在 `self._lock` 内取对象引用，锁外 to_thread deepcopy，覆写是替换对象而非原地改）→ 可用「权威单调 generation 前后核对 + 锁 + 内容指纹」建立同代冻结，并检测 ABA。
7. `mapspec/pipeline.py:73-133` + `mapspec_source.py:78-91`：`UpsertLayerIntent.source_data={"ref_id": ...}` 一次原子替换 source carrier + layer。
8. `gis_world_state/mutation.py::apply_gis_mutation` → `lifecycle_engine.apply_mutation`（CAS + checkpoint + undo + provenance）是唯一写路径。
9. `thematic_spec.py:558-613`：`spec_to_paint` 是唯一 paint 投影点；`_bivariate_to_match`（match/class_field）、`_uncertainty_to_interpolate`（0.85→0.25 反向插值）。
10. `frontend/lib/map-kit/vector-svg-export.ts:104,260`：前端 SVG 路径对 bivariate **只发 `legend_entries_truncated` 诊断、不画矩阵**；PNG 画布（export-chrome.ts:1560）与 live DOM（legends.tsx:89）画矩阵；服务端 G34 `render_legend_box_authored` 只画扁平条目。
11. `frontend/lib/mapspec-compiler/compiler.ts` 与 `app/services/mapspec_to_svg.py::_resolve_paint_value` **都支持 `match` / `interpolate` StyleMethod** → 用 `match` 表达有界可靠性桶（含显式 unknown），可保证 live 与服务端 SVG 同源同语义。
12. `uncertainty.py:40-96`：`UncertaintyMeasure.measure ∈ {standard_error,variance,confidence_interval,prediction_interval,quantile,value}`，`level ∈ (0,1)`，`unit` 空 = 与主输出同单位；是**算法输出证据契约**，与 converter 的 `metadata.uncertainty_field/min/max` 样式侧互不相连。

## 冲突记录

- 研究快照 #2094–#2116 的 GitHub reviews/inline/conversation 为空，但 `planning/G29..G35` 有独立 REVIEW.md；本任务两处都认，且**不把 PR 作者测试自述当作本次复跑证据**。
- 历史 architecture 文档描述 ChatEngine fallback 已下线，而 `PI_RUNTIME_CONTRACT.md` 仍定义合法 fallback 三态披露。按「实际生产代码 + 更新后的 ADR/契约优先」，本次不恢复、不新增任何 ChatEngine 路径。
- 已合并分支仍存在（G36/G37/G38 worktrees 在 `127669273`）不代表开发者占有；本任务只读它们，不改、不删、不合并。
