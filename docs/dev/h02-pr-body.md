# PR: feat(cartography): product conformance and export parity foundation（ADR-0228）

## 来源（issue / 文档债 / 任务书）

- 目标来源：ZCode Goal H02 任务书（智能制图产品编译与多端质量一致性基础层 VNext）。H01（上游 MapProductPlan 语义）未合并 —— 本 PR 按任务书以 master 现状接口（MapProductPlan/MapRequestIntent/ProductFacetContract）additive 对接，不依赖 H01。
- Baseline：`origin/master @ 0415ad8d3a03096ab405ece643753bc93974a760`（2026-10-20 实测 fetch；PR 创建前再 fetch 复核无漂移）。
- Dedup：与 open PR（#1695–#1701 全为 dependabot，零交叠）无重叠；#1622/#1623/#1621/#1614/#1617/#1502 均已合并为本 PR 复用的基础；他属 issue #1713/#1726/#1747/#1719/#1720 只建回归契约（xfail/it.fails，不改其实现文件）；#1702/#1707/#1753（测试基建存量债）不属本 PR，仅在 BASELINE.md 实证披露。

## 根因与证据（Problem Evidence）

1. `cartographic_fingerprint` 对 v1.6 失明：quality_loop `_LAYER_METADATA_KEYS` 不含 `representation`/`visibility` —— 实测仅改语义缩放表达的两份 spec 指纹逐位相等；RenderWork `work_id`、C13 截图 revision 门、lifecycle `superseded` 代守卫全部失明。
2. `representation_at_scale_report` 生产死路：仅 review_cartography(:685) 计算、`to_dict()`(:533) 不序列化、主 mutation 路径不计算、生产消费为零。
3. 组合身份不落物理 MapSpec：`layout_set(composition=)` 通道存在（ADR-0214 D2/D3）但 `webgis_map_product` 不传；终验 required_slots 兜底 `[["title"],["scale_bar"]]` 弱于真实模板契约（completion/inputs.py）。
4. 显式 export intents 不触发 export 槽位裁决（planner.py:1577-1596 仅 report_product 分支）。
5. 产品语义层不复跑 composition conformance（唯一消费点是 webgis_apply_composition 预检）。
6. nodata guard 仅 live 生效：thematic-paint.ts:21-26 模块自述「headless spec_to_paint does not encode it → export coerces nulls into the lowest class」。
7. 客户端 SVG 导出不水合 ref 源：exporter SVG 分支不调 `injectResolvedRefSources`（对比 map-panel.tsx:687 / publication-export.ts:136），孪生 `if (!srcData) return` 静默丢层。
8. 导出产物缺聚合语义证据：无 versioned manifest 把数据 revision/显隐/分类字段/色彩分级/legend/来源/时间/组件必需性做成机器可验证报告。

## 改动清单（Architecture & Files）

- 设计要点（详见 ADR-0228）：指纹 v1.6 可见性（D1）；组合身份经既有 `layout_set(composition=)` 通道落地 + 用户契约身份不覆盖（D2）；export 槽位裁决 + conformance 产品快照（D3）；nodata guard 四下降点同语义（StyleMethod 可选 `nodata`，schema 模板→types.generated 再生成）（D4）；SVG ref 水合 + `layer_ref_unavailable` 结构化披露（D5）；Cartographic Product Manifest v1 六维语义验证挂载导出路由 sidecar（D6，含属主校验）；版面文本确定性截断（D7）；33 场景跨语言工厂（D8）。
- 新增：`app/lib/cartography/product_manifest.py`、`docs/adr/0228-cartographic-product-manifest-v1.md`、`tests/cartography/test_h02_{fingerprint_report_gaps,product_pipeline_gaps,product_manifest,nodata_parity,golden_factory,issue_regression_contracts}.py`、`tests/cartography/golden_corpus/h02_scenarios/{generate.py,scenarios.json}`、`tests/benchmarks/test_h02_cartography_perf.py`、`frontend/lib/{mapspec-compiler/nodata-parity.test.ts, mapspec-compiler/heatmap-export-palette.regression.test.ts, map-kit/h02-scenarios.compile.test.ts, map-kit/vector-svg-ref.test.ts, export/layout-truncation.contract.test.ts}`、`planning/h02-cartographic-conformance/*`（RECON/PLAN/ORACLE/BASELINE/WORKLOG/LOOP_STATE/CHANGE_MAP/PERF/REVIEW）。
- 修改（最小接触面）：`quality_loop.py`（键表 +2、to_dict +1 行、结果构造 +1 行）；`gis_harness/tools.py`（identity helper + pin 保持）；`completion/inputs.py`（提取纯函数）；`planner.py`（重选条件扩展）；`product_runtime.py`（conformance 快照键）；`thematic_spec.py`/`mapspec_to_svg.py`（nodata）；`render_diagnostics.py`（+2 词表码）；`ts_projection.py`（+2 可选字段）+ 再生成 `types.generated.ts`/`component-catalog.generated.json`；前端 `compiler.ts`/`mapspec-to-svg.ts`/`vector-svg-export.ts`/`exporter.ts`/`export-chrome.ts`/`layout-description.ts`；`api/routes/map.py`（manifest sidecar，含 verify_session_owner）。

## 验证命令与输出摘要（Test Evidence）

- 命令：
  - `.venv/bin/python -m pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q` → **2339 passed / 19 skipped / 3 xfailed / 0 failed**（基线 pristine master 同口径 2259 passed / 19 skipped / 0 failed，172.62s）
  - `.venv/bin/python -m pytest tests/unit/gis_harness/ tests/unit/test_analysis_cartography_converter.py tests/cartography/test_ts_projection_contract.py tests/test_map_api.py tests/unit/test_mapspec_schema_v14_scene.py -q --no-cov` → **2544 passed / 3 skipped（exit 0）**
  - `.venv/bin/python -m pytest tests/benchmarks/test_h02_cartography_perf.py -m perf --no-cov -q` → **6 passed（9.26s）**；实测 100/1000 层 manifest 3.46/26.67ms、峰值 RSS 48MB（PERF.md）
  - `cd frontend && pnpm run typecheck` → **exit 0**；`pnpm run lint`（--max-warnings 0）→ **exit 0**
  - `cd frontend && pnpm exec vitest run` → **4363 passed + 1 expected fail**（唯一失败 data-plane.perf 为并发负载抖动，隔离复跑 5/5 通过；与本 PR 改动面无关）
- 回归：后端全量套件受存量 #1753 阻塞不跑（BASELINE.md 实证披露）；改用定向 lane（上述）+ 指纹影响面 148 既有测试 + render_work corpus 61 passed。
- 纪律：本地串行 / `--no-cov` / 不等线上 CI / golden 无漂移（唯一 golden 触碰是 catalog 受控再生成，ts_projection 契约测试锁定）。
- 关键 Oracle 验证连续两遍一致：cartography lane 两遍均 2339 passed / 19 skipped / 3 xfailed（103.00s / 108.13s）。

## Review 意见与清偿（Independent Review）

- Reviewer：独立 subagent（自行读码 + 实际复现 117 次工具调用，不代作者自评）；结论：**FIX-FIRST → 清偿后复验通过（ACCEPT-AFTER-FIXES）**。
- 计数：P0 ×0 / P1 ×2 / P2 ×4 / P3 ×5。
- 逐条清偿（commit c99ec307）：
  - `P1-1 manifest 分支无属主校验` → 补 `verify_session_owner`（与 _record_lineage 同款）+ manifest 计算卸载线程；map_api 测试绿。
  - `P1-2 ACK 测试缺 mapIdle 致 typecheck 红` → 补齐；typecheck exit 0。
  - `P2-1 nodata 空串语义 Python 孤例` → Python 对齐仅 null/missing（三端一致）。
  - `P2-2 time 块原文透传` → 键白名单。
  - `P2-3 Python 孪生静默丢层` → `layer_ref_unavailable` 诊断 + EMITTER_REGISTRY 双端登记。
  - `P2-4 空 spec 冒充 pass` → layer_visibility 空图层 not_evaluated。
  - `P3 ×5`（_digest set 确定性 / perf 公共 API / planner output_target / 卸载 / 发射点归属）→ 全修复。
- 未清偿项：无（全部清偿并复验；REVIEW.md 存证）。

## 风险与回滚（Compatibility & Rollback）

- 兼容性：全 additive —— StyleMethod 新键可选（无 nodata 时输出逐字节兼容，converter golden 锁定）；types.generated/catalog 受控再生成（契约测试锁定）；指纹扩展只影响携带 v1.6 字段的 spec（旧 spec 投影逐字节不变，render_work corpus 61 passed 实证）；manifest sidecar 是新文件不影响旧导出读取；DB/schema 零变更。
- 回滚开关：无 env flag —— 按单 commit revert（6 个 commit 独立可回滚：W1+W2 / W3 / W5 / W6+W7 / W4+W8 / W9 清偿）。
- 残余风险：(a) manifest 六维验证是语义面不承诺像素一致（设计如此）；(b) `_SEMANTIC_DEGRADATION_CODES` 分类集需随词表演进维护（P3 登记为后续小 PR 方向：从 RENDER_DIAGNOSTICS 派生分类属性）；(c) vlm/hybrid 视觉门、SVG 表达式求值、heatmap 导出色带仍待他属 issue 修复（xfail 契约将在其落地时 XPASS 亮灯提醒移除标记）。

## Out of Scope

- #1713/#1726/#1747/#1719/#1720 的单点修复（归属各自 issue；本 PR 仅 xfail/it.fails 回归契约）。
- 依赖升级（#1695–#1701 dependabot 归属）。
- 像素级跨浏览器一致性、矢量 PDF 前端接线（audit3 C-4/#1213 既有登记）。
- 后端全量套件解阻塞（#1753/#1702/#1707 存量债）。
- H01 上游语义扩展（MapRequestIntent 现状接口已够用）。
