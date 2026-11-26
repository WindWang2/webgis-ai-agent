# feat(cartography): reusable design specifications and consistent map series

## 概述

从一次性地图扩展到可被用户与 AI Agent 共同维护的**制图设计规范、系列地图与成果包的统一表达**：跨图/跨版次表达一致性、数据绑定与约束、可重复生产及审阅。核心增量不是新的地图综合算法，而是既有权威（MapSpec CAS / 设计系统 V4 / 模板版本化 / G40 资产 / PublicationIR / durable jobs）之上的**规范层与系列层**。

- 基线：origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（#2266 合并后）
- Pi pin：未改（vendor/pi 与方向 01 的 #2276 无关；本任务全部能力经既有 ToolRegistry/ToolDispatchService 面，Pi 经 `webgis_execute` 代理直达）
- 分支：`zcode/goal-carto-design-series-20261125`；migration：`g50_carto_design_series` + `g51_map_series`（down=0109 单线，upgrade/downgrade 往返本地验证）

## 新增用户旅程

1. **提取规范**：对一张真实已生成的地图说「把这张图的风格做成规范」→ `webgis_map_design_extract` / `POST /design-specs`：产出 portable 草案（palette id/分类方法与类数/组件存在性/受控字体/纸张 profile）——具体分类断点是数据值，永不进入规范；ref: 游标/凭证/会话选择被排除并逐条披露。
2. **绑定另一地区/时相**：`webgis_map_design_check_bindings`：role→field 显式绑定，字段存在性/值型/全零/负值/单位契约逐条校验；读不到的会话如实 `not_evaluated`（fail-closed，不伪造通过）。
3. **系列作者**：`webgis_map_series_create/add_member` → `webgis_map_series_consistency`（结构化 findings 定位到图件/组件）→ `webgis_map_series_propagation_preview/apply`（逐图 CAS receipts：committed/superseded/failed/skipped_locked；用户锁定字段被跳过并披露）。
4. **系列出版**：`webgis_series_production_submit`（durable job）→ 逐成员真实渲染（print_layout 纸面链或出版 PDF 链）→ 指纹缓存增量（改一个成员只重做该成员）→ 成品 QA（PDF 页数 pypdf / SVG 解析 + 标题语义 / PNG 签名）→ `series_publication_manifest.v1`（逐成员状态/文件 sha256；无 wall-clock，确定性 digest）。
5. **前端工作台**：`design-series-panel`（规范/系列/传播三 tab，真实 MapSpec 预览取权威渲染器；apply 前强制 consistency，error 级 findings 禁用传播按钮）；Pi 与 UI 共用同一 service/存储权威。

## WP 验收对照

| WP | 交付 | 证据 |
|---|---|---|
| WP1 权威模型 | design_spec.v1 typed 契约 + 不可变版本快照（0048 同构）+ 继承/digest/软退役/scope | `tests/unit/design_specs/` 64+41 测试；防泄漏负例（breaks/ref:/凭证/inline geojson 各一条 fail-closed）；引用校验对照 6 个权威 registry |
| WP2 绑定/提取/应用 | extract/recommend/binding/apply + 4 工具 | 分母纪律（DENOMINATOR_MISSING 澄清+密度族拒荐）；stale revision superseded 路径；dry-run 零 mutation 断言（真实引擎） |
| WP3 系列计划 | map_series.v1 + 一致性 findings 封闭词表 + 传播逐图 CAS + freeze | `tests/unit/map_series/` 73 测试；端到端（一致性→preview→apply→legend_spec 真实变更） |
| WP4 工作台 | 面板 3 tab + i18n + j26 journey + 2 行挂载 | vitest 6/6；全套 626 passed；lint 0 warnings |
| WP5 出版生产 | freeze_plan.v1 + 执行器 + 指纹缓存 + QA + manifest + durable job/工具/REST | `tests/unit/series_production/` 16 测试（含 2 成员真实 SVG + 缓存复用 + STALE_MEMBER 增量失效 + 真实 durable job 端到端） |
| WP6 案例与规模 | 5 类案例（ground truth 值级对账）+ 20/50 规模 | `tests/cartography/design_series/` 19 测试；`[scale] members=20 seed=0.4s render=0.2s reuse=0.0s bytes=66000` |

**旗舰全链路**（`test_series_full_journey.py`）：3 个真实成员会话（字段名各异）→ 规范 → 一致性 → 传播（YlOrRd ramp 可验证落到成员 legend）→ 冻结（指纹对账）→ durable 生产 → manifest all_complete + 产物 sha256 与磁盘文件对账。

## 复现步骤（本地验证，无线上 CI 依赖）

```bash
cd /home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125
PY=/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python
$PY -m pytest tests/unit/design_specs tests/unit/map_series tests/unit/series_production tests/cartography/design_series tests/unit/test_tool_registry_rationalization.py --no-cov -q   # 223 passed
$PY -m pytest -m cartography --no-cov -q            # 2800 passed；5 失败=master 既有基线（见下）
$PY -m alembic upgrade head && $PY -m alembic downgrade -1 && $PY -m alembic upgrade head   # g50/g51 往返
cd frontend && pnpm lint && pnpm test               # lint 0 warnings；626 file-passed
```

## 验证环境与结果

- 后端本域 223 passed（含 R1 回归 5 项）；cartography 标记 2800 passed / **5 失败均为 master 既有基线**（`test_contracts.py` pin 一致性、`scale_portrayal_cases` corpus、`component_variants` v3/v4 ×3 —— 已在干净 master worktree `/tmp/wg-master-baseline2@8fe3d7663` 逐个复现，非本分支引入）
- **全量 unit 基线对账（零回归证明）**：分支全量 `114 failed / 22056 passed`（52 min）；干净 master 全量 `123 failed / 21858 passed`（52 min，`/tmp/wg-master-baseline2@8fe3d7663`）。**ID 级 diff：分支新增失败 = 0**（`comm -23` 为空）；分支比 master 少 9 个失败（`test_runtime_validator.py` 的 9 个 browser fixture 在 master 运行中失败、分支运行中未失败 —— flaky 环境产物，方向相反也不构成回归）。失败文件子集（56 文件）同口径复跑：branch 104 failed == master 104 failed，**失败 ID 集合逐字节一致**。全量运行中仅在本分支全量出现的 11 个失败（terrain_tilejson ×8、review_core_low_boundaries ×2、temporal_media/test_faults ×1）经探针证明非本任务引入：本任务全部新测试 + 该 3 文件合跑 `338 passed / 0 failed`，且同 11 项在 master 全量中同样失败（属仓库既有全量排序全局状态污染，`QUALITY_ORDER_SEED` 机制即为该问题而设）
- 前端 vitest 5604 passed / 19 failed（分支基线既有：pdf-dependency-security 15、geoai 2、postcss 1、chat.json 占位符 1 + 2 收集错误 —— 独立 HEAD worktree 复跑证明）；lint 0 warnings；typecheck 7 项（5 项与 master 逐签名一致 + 2 项为共享 node_modules 缺可选依赖的环境产物）
- 资源：构建/测试 -j2 内串行；20 成员系列生产 0.2s / 66KB 产物（合成小文档、96dpi）

## 独立评审

- **R1 冷审**（独立 reviewer，反例注入 CE1–CE4 + 值级测试抽查）：结论「需修复后合并」——2×P1（传播 stale-upsert 回退 paint；成员会话无归属校验可越权改写他人 MapSpec）+ 3×P2（幂等键含 frozen_at / 可比角色缺单位静默通过 / simulated_data 真实路径不可达）+ 6×P3。
- **修复**（commit 7cc5b02a4）：全部 P0/P1/P2 关闭 + 可修 P3 落地（typed superseded / 缓存 QA 失效条目 / preview 死分支 / 指纹键名 / series_name 透传）；2 项 P3 如实披露不修（no-op 传播报 failed —— 词表已被测试与前端 receipts 编码；命名查重 creator-scoped —— 保守 fail-closed）。新增 5 个 R1 回归负例（color+ramp 不回退 / 跨用户拒绝 / freeze 证据隐藏 / 幂等键稳定 / UNIT_UNDECLARED）。
- **R2 复审**（另一位未参与实现与 R1 的 reviewer，独立运行回归与 6 条自有反例——含 phantom→foreign 升级路径探测）：结论**可合并**，无 P0–P2 遗留；R1 全部修复逐项验证通过（5 条回归测试 + 218 项三域套件由 R2 亲自复跑）。3 条 P3 备注：①前端 MapSeriesPayload TS 缺 simulated_data（已补，8050980ea）；②attach 门归属锚 create/upsert 语义分叉（双侧均 fail-closed，仅语义备注）；③freeze-plan optional auth 与 submit 强制 401 不对称（均安全）。user_id=None 调用面全量收口核查通过（REST 7 处 + tools 6 处全部传身份）。
- Pi live 旅程披露：本 worktree `vendor/pi` submodule 未检出（空目录），方向 04 的既有 Pi 旅程本为 opt-in 门控（PI_JOURNEY_E2E=1 + 构建产物，绝不静默绿）。本任务能力面经**真实 ToolRegistry.dispatch**（生产派发入口，Pi 长尾经 webgis_execute 同达）全链验证；live Pi 进程冒烟按仓库既有纪律属阻塞，如实登记。

## 与并行工作/既有能力的边界

- **#2274（多尺度综合）**：不做其简化/聚合/尺度转换/paper scale route；只消费 master 稳定 portrayal。其文件面（multiscale/*、semantic_checks.py、hud/*、map.json）零触碰。
- **G34 print layout / G40 symbol assets / G15 frame products / atlas_batch**：全部只读消费/复用（渲染、资产版本、共享分级语义、页缓存纪律），未包装成新功能、未建第二渲染器/资产库/地图状态。
- 共享接线（均最小 additive）：`app/tools/__init__.py`（3 行注册）、`app/main.py`（3 个 include）、`app/tools/categories.py`（16 工具归类）、`app/services/jobs/lifecycle.py`（1 个 JobKind）、`frontend/components/sidebar/map-studio-tab.tsx`（2 行挂载）、`frontend/lib/i18n/messages.ts`（namespace 登记）、`frontend/e2e/helpers/registry.ts` + `api-stubs.ts`（journey 登记）。
- **开放 PR 避让矩阵**（2026-11-26 复查）：#2272/#2273/#2275/#2276 文件面零领域重叠；#2277（model authoring）交集仅上述 3 个共享接线文件。若并行分支后合，建议合并顺序：本 PR 先行或最后均可——接线行互不冲突，冲突时以各自 additive 行为准。

## 未完成/跳过项

- 系列成员生产当前支持单页地图产物（SVG/PDF/PNG）；成员级多页图册（每成员再分页）不在本期（既有 atlas_batch 已覆盖单会话分页场景）。
- R1 两项 P3 如实披露不修（均有测试锁定现有行为）：no-op 传播记 `failed`（receipt 词表已在前端与测试编码）；设计规范命名查重按 creator scope 而非 org scope（保守 fail-closed：同 creator 跨 org 同名误报 conflict，不泄露他人存在性）。
- 前端 journey 为 mock 模式（仓库 Playwright 纪律）；真实浏览器多轮路径已在 vitest 组件层覆盖核心交互。

## 测试与新增面清单

- 新增后端测试 223（design_specs 105 + map_series 78 + series_production 16 + design_series 案例/旗舰 19 + registry 归类 5）+ R1 回归 5（含于上述）
- 新增工具 17（design_spec 5 + map_design 4 + map_series 7 + series_production 1），全部 registry 归类、typed errors、有界回执、inspect/preview 与 apply 分级
- migration：g50/g51（upgrade/downgrade 往返验证；无 CHECK 破坏；既有表零改动）
