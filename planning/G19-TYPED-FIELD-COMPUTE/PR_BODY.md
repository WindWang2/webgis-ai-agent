# G19-TYPED-FIELD-COMPUTE：类型化字段计算与可复现派生属性工作台

## 问题与用户获得的行为

当前 master 已有属性编辑、数据准备、联邦连接、过滤与统计，但用户想把「人口 / 面积平方千米」「两年变化率」「按条件形成类别」「统一行政区代码」「按明确时区提取月份」变成**可复用、可查询、可制图**的新列时，只能下载到外部表格软件再传回，或把整表交给模型。

本 PR 提供一条完整闭环：

| 步骤 | 用户/模型可做的事 |
|---|---|
| 发现 | 数据准备 tab 内的「字段计算」面板与 4 个 `field_compute_*` Pi 工具；`catalog` 列出函数集/范围/预算 |
| 构造 | 选来源 ref → 字段搜索 → 写 `{中文 字段名}` 安全公式（或提交 IR）→ 声明单位/时区/作用范围/错误策略 |
| 预览 | 采样 before/after、依赖字段、输入/输出类型、单位与量纲、派生量分子分母、null/error 计数与有限示例、预算预估；明确标注 sample/estimate |
| 确认 | generation 失效 + 迟到响应丢弃；确认绑定 `confirmation.params_fingerprint` |
| 生成 | 全量派生 → 新 `ref:fc-*` → 收据/谱系/artifact 登记 → 结果表 → 加载到地图/后续分级图 |
| 找回 | 默认 `session_ref`（如实披露生命周期）；显式 `project_id` 时写真实磁盘项目清单，可重开找回公式与来源 |

**结果**：原件属性/几何不变；输入版本、schema、scope、AST/函数集版本、单位/时区/错误策略共同锚定计划指纹；预览与全量共用同一解释器；刷新后 `output_ref` 可读、字段可用于既有制图工具。

## 实现边界（明确不做）

- **不是**任意 Python/JS/SQL 执行器：只有 6 类闭合 IR 节点、28 个白名单函数；无 `eval`/`exec`/import/文件/网络/属性反射/系统时钟/随机数。
- **不改** `data_preparation` 既有 6 个步模型、executor、收据、7 个 API 端点；不改 MapSpec schema、Pi runtime/bridge、ToolRegistry、G13 资格政策、全局 UI 主题/布局/面板宽度。
- **不接管** G14 原件编辑/undo journal、G17 导入协议、G18 下载器、G15/G16/G20/G21 的 schema 子模型、G09 批量分析、#2022 自动刷新、#2032 UI 重设计。
- **不占用 ADR 数字编号**（仓库已有 0286/0287 重号，且与并行 PR 冲突）：契约登记在 `planning/G19-TYPED-FIELD-COMPUTE/INTERFACES.md` + 域模块 docstring。

## 关键设计

| 面 | 做法 |
|---|---|
| 类型与缺失 | bool 不是整数、空串不是 0、null 不隐式变值；除零/溢出/非有限为行级 error；`abort`/`null` 策略显式 |
| 科学语义 | 字段名只是提示；单位必须 user-declared；加减同量纲同标尺，乘除保留分子/分母证据；`percent`↔`fraction` 数值标尺与物理倍率显式区分 |
| 几何 | 复用 `crs_safety.classify_crs` + `pyproj.Geod` / shapely；3857 与 assumed/unknown CRS 阻断，但**不影响同一计划里不依赖几何的列**；洞/多部件/跨经线披露 |
| 作用范围 | `all_rows` / 受限 `predicate` / 可靠 `selection`（完整+revision+稳定唯一 ID）；截断/过期一律拒；范围计算保留全体行，`subset_export` 是独立显式操作 |
| 版本锚 | 一次性冻结一致快照，所有批次只读它；发布前二次核对内容+schema，变化即 `SNAPSHOT_CHANGED` 中止且不发布；`STALE_PLAN` 拒绝旧计划 |
| 原子登记 | compute→write→register→publish；收据落盘是 ready 必要条件；取消/写入失败不发布半成品；幂等键含内容锚 |
| 精度 | 大整数字面量编译期拒；数据中的大整数以文本写出并披露 `bigint_as_string`，不静默舍入 |
| 生成物 | 用户帮助文档由 `function_help()` + `contracts` + `CANONICAL_UNITS` 经域内 `gen_help.py` 生成，不是手写清单 |

## 共享文件接线（全部纯新增行，无格式化/重命名）

`app/main.py`（+2）、`app/tools/__init__.py`（+1）、`frontend/lib/i18n/messages.ts`（+5）、`frontend/components/sidebar/data-sources-tab.tsx`（+2）、`scripts/generate_endpoint_scope_matrix.py`（+1 条 `DOMAIN_RULES`）。生成物 `tests/quality/snapshots/openapi.json`、`docs/api-docs.md`、`docs/dev/endpoint-scope-matrix.csv` 按本分支当前版本独立完成并随 PR 提交，不留给用户/合并者。与并行 PR 的唯一潜在冲突段是 `data-sources-tab.tsx` 的 prepare panel 内部挂载行。

## 已执行的验证（如实区分四类）

**✅ 本地通过**

| 项 | 结果 |
|---|---|
| `pytest tests/data/test_field_compute_v1.py -q --no-cov` | **45 passed** |
| `pytest tests/data/test_field_compute_review_v1.py -q --no-cov` | **12 passed**（独立评审修复针对性回归） |
| `pytest tests/data/test_field_compute_api_v1.py -q --no-cov` | **6 passed**（真实 ASGI 路由 + 鉴权） |
| `pytest tests/unit/test_field_compute_tools_v1.py -q --no-cov` | **6 passed**（真实 `ToolRegistry.dispatch`） |
| `pytest tests/quality/test_api_compatibility.py tests/test_api_docs_drift.py tests/test_endpoint_scope_matrix.py -q --no-cov` | **23 passed**（刷新生成物 + schema 消歧修复后） |
| `vitest run <组件 + 错误解析> --pool=threads --maxWorkers=1` | **9 passed** |
| `ruff check`（全部新增/改动 Python） | All checks passed |
| `git diff --check` / `git diff --cached --check` | 通过 |
| `tsc --noEmit` / `eslint <新增前端文件>` | 新增文件 0 类型错误 / 0 警告（8 个既有 `scene3d/*` 错误隔离） |
| i18n zh/en `fieldCompute.json` | 141 / 141 键一致 |
| 21 个用户与故障场景 | 见 `planning/G19-TYPED-FIELD-COMPUTE/TEST_MATRIX.md`（UI 单列等边界如实声明） |

**⚠️ 既有失败（干净 base `8167052` 可复现，位于未改动文件）**
- `tests/data/test_preparation_plan_v1.py`：24 passed / 9 failed —— `dataset_semantics/store.py:252` 写 `dsd-v1:<sha>.json`，Windows 冒号非法（WinError 123）。
- `tsc --noEmit`：8 个错误全部在 `lib/map-kit/scene3d/*`（缺 `three` 依赖 / implicit any）。
- 本 PR 的 descriptor/provenance/artifact 登记一律 additive，失败以 `descriptor_minted=false` + `degraded` 如实披露；**成功路径不以该写入成功为必要条件**。不删测试、不放宽容差、不改 golden。

**🚧 环境阻塞 / 未运行**
- 真实 Pi 进程 + stub LLM 全链路 → MapSpec mutation → 浏览器渲染回执：本环境无 Node extension/runtime 与渲染服务；已用真实 `ToolRegistry.dispatch` 覆盖「工具→服务→成果→既有分类引擎」的数据面闭环，MapSpec/渲染保持 `not_evaluated`，不伪造。
- 前端全量构建 / 全量 Vitest / Playwright 浏览器旅程：资源纪律与同机 4 个 worktree 并行下未运行；只跑定向组件测试与静态检查。
- 有界基准已真实执行：Windows AMD64 / Python 3.13.9，5,000 行 × 3 列、batch 2,000，耗时 **1.008s**、`tracemalloc` 峰值 **1.81 MiB**（仅解释器执行期新增分配，不含输入构造与冻结深拷贝）；**万行级基准未跑**，不据此承诺 10 万/千万行能力。
- **线上 CI：按授权不等待、不轮询；未关闭或降低任何门禁。**

## 独立评审（review → fix → rereview）

独立 reviewer（未参与核心实现、非前端实现者）冷读完整 diff 后判定「当前不可进入 PR」，列出 **4 个 P1 + 9 个影响验收的 P2**。全部按最小集合修复并补回归，详见 `planning/G19-TYPED-FIELD-COMPUTE/REVIEW.md`：`tests/data/test_field_compute_review_v1.py` 12 项针对性回归全部通过。

关键修复：① 契约模型加 `FieldCompute` 前缀，重生成三份生成物并验证既有 OpenAPI 短名/`$ref` 零变化；② 项目保存失败降级 `persistence` 并如实披露；③ 发布顺序改为「收据草稿闸门 → store → 登记 → 已发布收据」，收据落盘失败时零可见半成品；④ `datetime_part` 用表达式自带时区；⑤ 幂等命中补做项目保存；⑥ `computed_rows` 真实统计；⑦ abort 下几何阻断只降级该列；⑧ 新增服务端 cancel 端点并联动前端、入图失败移除占位图层；⑨ 稳定键收据/manifest 不截断、弱引用锁、UI 单列声明；另修 owner 语义、错误信封解析、组件夹具与 lint。

> 复审结论：`REVIEW.md` 第二轮待回填（由同一独立 reviewer 冷读新 diff 与回归证据，不预设通过）。

## 与其他 PR 的关系

- 开放 `#2032`（UI 重设计）：只在 prepare panel 内加一行挂载，不动主题/布局/侧栏/面板宽度；冲突段已在 `INTERFACES.md §2` 列出。
- 开放 `#2022`（G10 刷新）：不做自动刷新；提交前版本重核与 `SNAPSHOT_CHANGED` 是独立的计算一致性语义。
- 开放 `#2021`（G11 简报）与 dependabot：不碰。
- **PR 前新出现的 `#2073`（G13 能力生态生产装配）**：已用 `gh api pulls/2073/files` 分页核查其 29 个 changed files，与我的 9 个共享接线/生成物文件**零重叠**（`OVERLAP` 检查为空），无需冲突处理；其领地（`pi_availability_adapter` / `tool_surface_v3` / `tool_dispatch_service` 等）本 PR 不触碰。
- G12–G18 / G20–G21：按 `INTERFACES.md §5` 让出所有权；本 PR 基于 `origin/master @8167052`，PR 前再次 fetch 确认主干未前进（`0 behind / 5 ahead`）。相关 3 个开放 PR（#2021/#2022/#2032）无正式 review submission / inline comments。

## 迁移与回退

纯新增域 + additive 接线，无数据迁移、无 schema 变更、无破坏性 API 变更（OpenAPI 为 additive 新端点）。回退 = revert 本 PR 的 4 个 commit；已生成的 `ref:fc-*` 为会话内数据，不依赖数据库迁移。

## 交付清单

`PR_HANDOFF.md`（函数集版本、支持形态与规模、共享接线、G12–G21 兼容、验收/评审记录、PR URL）、`TEST_MATRIX.md`、`REVIEW.md`、`LOOP_LEDGER.md`、`BASELINE.md`、`DEDUP_MATRIX.md`、`PLAN.md`、`INTERFACES.md`、`docs/field-compute-contract.md`（契约驱动用户帮助）。
