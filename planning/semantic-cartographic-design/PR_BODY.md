# feat(carto-design): 专业专题制图语义决策与多方案可解释设计

## 概要

用户说「成都小学分布、各区数量差异、密度热点，做一张专题图」时，系统现在基于**数据种类与表达目标**产出 2–4 个可比较的表达模型层设计方案（点图/聚合/密度面/区级统计/双期对比），每个候选带「为什么合适/为什么不合适/科学局限/降级路径」的 typed 证据；选定方案经 user-wins pin 固化（后续成图与自动调整不再改写）；决策链（intent→数据资格→候选→选择→分类→组合）作为一等工件随产品下发，Agent 可解释、前端可见、QA 可反查。

- master 起始：`4b3629c90`（origin/master，2026-10-29）
- 分支：`zcode/cartography-design-choices-vnext`（14 个原子提交：M0 规划 → M1 引擎 → M2 接线 → M3 选择 → M4 检查码 → M5 五族 → M6 G05 → M7 前端 → R1 评审清偿 → G4 收口 → 前端 tsc 对齐 → R2 评审清偿）

## 差异化（不重复建设）

master 已有：组合模板层候选（composition_selection）、recipe 级多候选（plan_candidates）、分布→分类裁决（choose_classification）、data_qualification 五态、methodology_warnings、user-wins 绑定机制（#1614）、45 语义检查码。本 PR 全部**调用**这些权威；新增的是表达模型层的候选与门控、决策 trace 工件、3 个语义检查码、统一选择 action 与前端披露面板。去重矩阵见 `planning/semantic-cartographic-design/PR_OVERLAP_MATRIX.md`（开放 PR #1868 文件面零重叠）。

## 用户场景（全部经真实 dispatch 链验证）

1. **成都小学分布**：产品结果携带 2–4 候选（点族 + 聚合/密度族 + 率语义门控对），`design_decision_trace` 数据事实行说明分母/点数证据。
2. **缺分母谈密度**：无 population 字段时 normalized_choropleth **typed blocked**（DENOMINATOR_MISSING + 降级路径），原始计数候选带「不得标注为每万人」披露；分母在场（含 overlay 跨源投影）则 REQUIRED_SATISFITED 可用。
3. **稀疏点要热力**：3 点 < HEATMAP_MIN_POINTS=10（复用 cost_model 单源）→ 热力候选 typed blocked 可见（不静默换方案、不假成功）。
4. **固定表达方案**：`webgis_map_design_select` 经 RequirementDocument 显式 patch（actor=user）固化 pin；下一轮产品候选面 honored，自动调整不覆盖（复用 #1614 user-wins 机制 + quality_loop 既有守卫）。
5. **双期对比**：change 意图 + 时相字段（TIME_FIELD_HINTS 单源）→ change 族候选。

## 科学/安全边界

- 热力恒带 KDE 披露（KDE ≠ 人口密度）；DENSITY_SEMANTICS_HONESTY 检查码在图面词法层拦截「KDE 冒充人口密度」（fail）与「原始计数误标率口径」（warning）。
- 三个新检查码：CHART_MAP_FIELD_COHERENCE（图-图口径）、VIEWPORT_COVERS_RESULTS（视口含结果，补 coverage=0 反 pass 漏洞）、DENSITY_SEMANTICS_HONESTY——契约 v3（48 码）经显式协议更新。
- 载荷有界：design_digest（LLM 面 ~0.5KB，2500 预算内）、design_candidates/trace（details/raw 面，全字段截断）；G4 大数据契约阈值校准 24K→30K（实测 ~28.5KB，意图不变：无要素内联，仍比要素内联低两个数量级）。
- 不新增网络面；pin 路径硬编码（无任意 IR path 注入）；降级 best_effort 不阻断主链（有测试）。
- 修复顺带清偿：requirement_ir 幂等重放误报栅栏（applied==0 无变更不写）。

## 测试命令与结果（全部本地实测）

| 面 | 命令 | 结果 |
|---|---|---|
| 引擎/检查码/五族 | `pytest tests/cartography/ -m 素材相关 --no-cov -q` | 2961 passed |
| 设计面单测 | `pytest tests/cartography/test_design_candidates_v1.py tests/cartography/test_semantic_checks_design_v1.py tests/cartography/test_design_families_e2e_v1.py` | 19+11+6 passed |
| 生产接线/选择 | `pytest tests/unit/test_map_product_design_trace_v1.py tests/unit/test_map_design_select_v1.py`（真实 registry.dispatch） | 4+6 passed |
| G05 验收 | `pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 -q` | 45 passed（含 FD 族 3 新场景；pin 64 场景经 CLI） |
| requirement_ir/绑定回归 | `pytest tests/unit/gis_harness/ tests/unit/test_clarification_loop.py` | 2733+ passed |
| 后端全量相关面 | `pytest tests/cartography/ tests/unit/gis_harness/ + design 单测` | **5701 passed, 0 failed**（R2 清偿后终值） |
| 前端 | `pnpm exec vitest run components/map/map-components`；`pnpm exec tsc --noEmit`；eslint（改动文件） | 134 passed / 零错 / 零告警 |
| lint | `ruff check app/ tests/` | 全绿 |
| mypy | 白名单棘轮模式，白名单文件（app/core 等 4 处）本分支未触碰 | 门禁不受影响 |

**未跑项**：远端 CI（按任务纪律不等）；Playwright journey（E2E_MODE=mock 需起前端服务，本地面板行为已由 vitest jsdom 覆盖）；golden 像素回归（本分支无渲染输出变化）。

## 生成物同步（全部经项目生成器）

component-catalog.generated.json / types.generated.ts / catalog docs（component-catalog.md、renderer-parity-matrix.md）/ golden corpus ×2（GOLDEN_CORPUS_UPDATE=1）/ mutation parity golden（ZCODE_PARITY_GENERATE=1）/ G05 scenarios.json pin（CLI --force）。

## 独立评审

两轮独立上下文评审（未参与实现）：
- **R1**：3 P1 + 2 P2 + 9 P3 → 全部清偿（commit c16ed762a），细节映射见 `planning/semantic-cartographic-design/INDEPENDENT_REVIEW.md`。
- **R2**（agent_56ee4180，独立上下文）：R1 清偿全部实证通过（含 80 注册模型 pin 往返枚举：71/80 可用、9 拒绝均诚实且与测试锁一致）；新发现 2 P2（planned 模型被推荐可 pin、率语义模型不可 pin）+ 6 P3 → 全部清偿（cbefe52f5，含共享总账历史恢复）。专项扫描（并发/幂等/载荷截断/前端防御/G05 details 合并/契约 W8/requirement_ir 幂等副作用）无新问题。两轮共 6 处负例翻转验证均真实翻红。

## 回滚

单一分支，全部 additive 接线（best_effort 降级面）；`git revert` 各里程碑原子提交即可分粒度回滚；语义契约 v3 回滚需同步回退 JSON+测试计数；无数据库迁移、无配置变更。

## 待后续方向

- data_binding 增补 count/rate 语义类型（契约 W8 条目已登记）→ DENSITY_SEMANTICS_HONESTY 从词法升字段级。
- 前端接受按钮直连 HTTP 面（当前统一 action 为 agent/API 通道，面板只读披露）。
- plan_candidates（recipe 层）与 design_candidates（表达层）的联合排序呈现。
