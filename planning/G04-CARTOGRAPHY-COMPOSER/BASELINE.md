# G04-CARTOGRAPHY-COMPOSER BASELINE

- 审计时间:2026-11-07(实时查询,非快照假设)
- `origin/master` = `585812ee4e2d75612f2b124e6fe6587af3deca5c`(2026-11-07 17:54 +0800,Merge PR #2010)
- worktree:`../webgis-wt-intelligent-carto-composer-20261107`,branch `zcode/goal-intelligent-carto-composer-20261107`

## 1. 在途 PR 与分支冲突矩阵

| PR/分支 | 主题 | 与本方向关系 | 决策 |
|---|---|---|---|
| #2011 `zcode/goal-gis-execution-compiler-20261107`(open) | G02 IR→执行编译:`gis_harness/execution_compiler/*`、`requirement_ir/{build,normalize}.py`、`analysis_workflow_tools.py` | RequirementIR 是 CM-01 的上游输入,但 #2011 改的是 IR 构建/编译,不改 cartography | **不改 `requirement_ir/` 与 `execution_compiler/`**;CM-01 只消费其既有产物。若确需上游小修,以最小 diff + PR body 声明 |
| #1975(已合并) | 出版制图 vNext:约束版面/标注治理/多格式导出(ADR-0283) | 本方向的地基,**不得重做** | 只在其上增强真实组合与一致性 |
| #1993(已合并) | 地理范围与版面求解:`solve_layout_v5` 接入矢量端点(显式 page_profile) | 同上 | 沿用单求解器 |
| #1974/#1972/#1973(已合并) | 情境交互/类型化封套/Pi v1.5.0 | 上游契约 | 只读消费 |
| dependabot #1978–#1986 | 依赖升级 | 无冲突 | 忽略 |
| 其余 2026-11-07 本地 worktrees(capability-ecosystem/geodata-readiness/harness-reliability/pi-native-runtime) | 其他 G 方向并行 agent | 路径不重叠(它们未推远端分支) | 不触碰其文件域 |

## 2. Issues #1999–#2007 实时核验(2026-11-07,均 open 未认领)

逐一以代码证据核验,**全部仍未修复**:

| Issue | 断言 | 亲自核验的证据 | 归属 |
|---|---|---|---|
| #1999 A4 导出没走约束求解 | 浏览器/Agent 导出链不调 `solve_layout_v5` | `app/tools/cartography.py:1024` 固定返回 `constraint_layout:"not_evaluated"`;仅 `POST /api/v1/export/vector-pdf` 显式 `page_profile` 才求解(`app/api/routes/map.py:575`);前端无任何调用方传 page_profile | **本方向**(CM-05/06,阻断验收) |
| #2000 已求值版面标记污染默认导出 | 标记写进 MapSpec 后改变后续默认导出 | 写入:`app/services/agent_swarm/specialists/cartographer.py:483-484` 把 `constraint_layout(+report)` 写进 `layout`;消费:`app/services/mapspec_svg_chrome_layout.py:24-26` 以全局标记为门 + `:29-75` **固定像素偏移**(非求解器输出)挪整饰 | **本方向**(CM-05,阻断验收) |
| #2001 换城市旧图层/名实不符范围框 | 地理守卫只查 primary_ref、名字匹配不比 bbox | `app/services/gis_harness/tool_map_product.py`、`scope_ref_guard.py`(issue 证据,未重读全文) | 边缘:CM-08「换城市」事务验收依赖;**候选最小修复**,后决策 |
| #2002 出版约束不尊重组件族锁 | 锁 `legend` 保护不了 `legend__1` | `publication_constraint_layout.py:244` 仅 `cid in locked` 精确匹配;族语义谓词已存在:`app/contracts/workbench_locks.py:49-71`(`__` 双向族匹配),未被调用 | **本方向**(CM-05 用户锁,阻断验收) |
| #2003 导出指北针 N 不随地图旋转 | 矢量链指北针恒朝上 | PNG canvas 链已对齐(`frontend/lib/map-kit/exporter.ts:486` `bearing=-mapBearing`);**矢量/SVG 链缺失**:`app/lib/cartography/svg_marginalia.py:58` `render_north_arrow` 无 bearing 参数,`app/services/mapspec_to_svg.py:564/566` 未传;MapSpec `view.bearing` 存在(`mapspec_schema.py:192`)但未被消费 | **本方向**(CM-06 旋转一致,阻断验收) |
| #2004 框选 >8 要素静默截断 | 交互载荷 | harness 交互域,非制图组合 | **避让** |
| #2005/#2006 网络距离单位/summary 丢数值 | 工具 summary | G02 执行域(#2011 邻域) | **避让** |
| #2007 OpenAPI 快照漂移 | 文档生成 | 全仓文档流程 | **避让** |

## 3. 既有能力(不重做)

- `solve_layout_v5` 是唯一求解器(`app/lib/cartography/layout_solver.py`),print profile 走 v5、screen 走 v4(`publication_constraint_layout.py:176-199`)
- 矢量 PDF 链:`render_publication_pdf`(weasyprint,deny-all fetcher,CJK 字体探测,atlas PublicationIR 分页,组件覆盖 coverage,diagnostics sidecar,lineage)
- 组件体系:component_registry/abi/taxonomy/presets/templates(131 文件/39k 行)
- 前端 map-components(含 chart-panel/colorbar/north-arrow/legends 等 + 大量 .test.tsx)
- 177 个 cartography 测试文件(golden_corpus/publication_cases/representation_corpus 等)

## 4. 三事实面审计结论(子代理 A,2026-11-07,全文见 REVIEW.md 引用)

### 面1「选择哪种地图」
- 成熟:model_library(12 seed+packs)→ composition_selection(6 因素加权,geometry∩artifact 硬过滤)→ design_candidates(R1–R6 科学门控,typed block)。**判选链本体无 query 关键词匹配**(审定静态亲和表 vs per-query 硬编码是刻意区分)。
- 缺口:**判选输入投影无单一权威**——`TaskCartographyContext`/`DesignTaskContext` 由 planner.py 与 tool_map_product.py 各自投影,口径无统一 fixture 锁定(CM-01 目标);affinity 表只覆盖 20 类目;design_candidates 族去重逻辑高复杂度(回归高风险区)。

### 面2「地图由什么组成」
- 成熟:契约五件套(descriptor/ABI props+版本/模板变体/槽位/契约)fail-closed 互账;实例 id 惯例单一事实(`component_abi.INSTANCE_ID_BY_TYPE`);`webgis_component_update` 精确实例寻址;用户锁闭环(workbench 单一事实+apply 零触碰+单码拒绝);G04 资产面已落(前 wave)。
- 缺口:**composer 主链与 apply_contract 双路径**——`bind_scope=all_thematic` per-layer 图例展开只在 composer,apply_contract 只建 primary 单实例(composition_contract.py:412-415 披露);composer overrides 仅类型级;props schema 多数类型空表(写入面强校验弱);SEED_CONTRACTS 覆盖率低。

### 面3「结果是否一致」
- 成熟:五格式导出入口+typed 披露;证据链(diagnostics sidecar→manifest→lineage/receipt);能力矩阵机器真值(`_SUPPORT_MATRIX` 三通道+fingerprint);parity/golden 测试面广(164 test 文件)。
- 缺口:**矢量 PDF backend-only,前端 exporter 仍走栅格 PDF(map.py:503-506 自认「待接线」)**;solve_layout_v5 生产消费方只有矢量链/cartographer(浏览器链恒 not_evaluated = #1999);PDF 矢量文本长标题不折行(#1975 限制 2 评审后已清);全栈浏览器 E2E NOT_RUN。

### #1975 已实现范围(不重做)
D1 约束版面(layout_constraint_spec.v1+solve_layout_v5+wrap-not-clip+safeAreaMm)/D2 标注治理/D3 导出一致性(tile-miss 有界披露+冻结边界)/D4 semantic_checks_layout(5 码词表)+selfheal 路由/D5 36 条结构 golden。遗留限制(声明性交付待接线、障碍仅 circle 等)见其 PR_BODY_DRAFT。

## 5. 避让决策汇总

1. 不重做 #1975 出版制图引擎;不新建第二求解器/第二 MapSpec/第二 renderer。
2. `requirement_ir/`、`execution_compiler/`(PR #2011 域)只读消费。
3. #2004/#2005/#2006/#2007 非本方向所有权,避让并记录。
4. #1999/#2000/#2002/#2003 直接阻断本方向 Oracle(用户锁不生效、默认导出不稳定、旋转不一致、A4 不求解),**纳入最小修复范围**,每项带 characterization 失败测试先行。
5. #2001 若 CM-08「换城市增量事务」验收被其阻断,做最小修复(overlay_refs/范围框比对),否则避让。
