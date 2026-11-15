# G47 Dorling Cartogram — 独立评审（REVIEW）

| 项 | 值 |
|---|---|
| 角色 | 独立评审（未参与实现），只读审查 + 定向运行验证 |
| 日期 | 2026-11-15 |
| Base SHA | `655f769ca17f17c5797da13b4c299b8fa600e370` |
| 分支 | `zcode/g47-dorling-cartogram-20261115T124822Z` |
| 结论 | **CHANGES REQUIRED**（3×P0 / 3×P1 / 5×P2） |

## 发现

| # | 严重度 | 文件:行 | 问题 | 证据 | 建议修复 |
|---|---|---|---|---|---|
| 1 | P0 | `app/tools/cartogram_tools.py:319,355` | 6 个 Pi 工具只注册了 3 个：`status`/`export` 声明 `memory_class="small"`（合法值 unknown/light/medium/heavy），`register_cartogram_tools` 在第 4 个装饰器处抛 `ValueError`，模块加载被 `app/tools/__init__.py:188` 降级为 warning，应用照常启动。`apply`（第 5 个）同样永不注册。 | `ToolRegistry(); register_cartogram_tools(reg)` → `ValueError: 工具 webgis_dorling_cartogram_status 声明了非法 memory_class='small'`；`reg._tools` 只剩 `inspect/plan/run`；`scripts/check_tool_capability_conformance.py` 两次打印 `[ToolInit] Failed to load app.tools.cartogram_tools`。 | 两处改成 `memory_class="light"`；补一条「模块必须零异常加载 + 6 个工具齐全」的单测（现有 G47 单测没有 tools/routes 覆盖）。 |
| 2 | P0 | `app/services/cartogram_authoring/layout.py:167-183`（根因 176） | 「原地理参照轮廓」用的是**投影米坐标**，却套用了示意空间的 `_tx/_ty`：`raw` 圆点来自 `positions`（已归一化到 ~1000），几何来自 `prepared.units[].geometry`（EPSG:3857 米）。结果 45 个参照点 0 个落在 1000×1000 画布内。artifact 仍标 `reference.coordinate_space="diagram"`，SVG/PNG/PDF/UI/打印全部看不到灰线轮廓 —— 交付物所谓「原图参照」实际为空。同时把 12,384,905 / 3,803,063 这类投影坐标写进了声称非地理的 artifact（违背坐标隔离）。 | `values=100..900`：`ref x range 12384905.6734..12474220.1485`，`circle x range 427.28..571.31`，`ref points inside canvas: 0/45`；SVG 首个 path `M 12384905.67 3803063.72 ...`，viewBox `0 0 1000 1000`。`tests/integration/test_g47_publication_readback.py:182` 只断言 path 条数与 `M ... Z` 格式，未断言落在画布内。 | 几何也要走与 anchor 相同的归一化（把 `compute._normalize_anchors` 的 factor/offset 传到 `plan_circles`，或对 geometry 单独做一次同一 factor 的仿射），并给参照点加「必须落在 extent 内」的回读断言。 |
| 3 | P0 | `app/services/cartogram_authoring/quality.py:131-150` + `layout.py:288` + `compute.py:164` | 面积门是空转的：oracle 用 `expected=πkv` 和 `r=sqrt(kv)` 反算，恒等式使 `max_rel_error` 永远 ≈0；而落到 artifact 的 `radius` 被 `round(...,4)`，落盘后的真实误差从未被复核。可构造 `publication_ready=True` 而实际面积误差 5.4%（门限 1%）。 | `values=[1, 8.3e8]`，`max_iterations=4000` → `status=converged, publication_ready=True, metrics.area.max_rel_error=0.0, area pass=True`；按 artifact 的 `radius=0.0013` 复算得 `artifact_worst_rel_err=0.0543`。 | oracle 必须对**序列化后的 artifact**（round 之后）复核，而不是对内存中的 `norm.circles`；或按 4 位小数的量化误差给面积门加下界（`min_radius` 量级不足时拒绝/提高精度）。 |
| 4 | P1 | `app/services/cartogram_authoring/compute.py:67-70`（service 同构） | `scan_quantities` 在 scope 过滤**之前**对待全量源执行：`quantity.total/counts/max_value` 含被排除单元，`build_area_scale` 用被排除的最大值定 k，`zero_ids/missing_ids` 也含被排除项。而披露文案写「总量与覆盖范围不包含它们」。同一根因还让错误提示「用 scope.exclude_ids 排除」无法执行：排除后仍报 `FIELD_NOT_NUMERIC` / `MISSING_QUANTITY`。 | exclude `r2c2`：`quantity.total=4500`，`sum(drawn)=3600`，`area_scale.max_value=900`（正是被排除项），disclosures 却称「不包含它们」。include `[r0c0,r0c1,r0c2]`：`max_radius` 落在 22.23（因全局 max=900），而非计划的 36。把 `"n/a"` 放进 r2c2 再 exclude → 仍 `REJECTED: FIELD_NOT_NUMERIC`，其 hint 正是「用 scope.exclude_ids 逐项排除」。 | 先按 scope 过滤 `frozen.features`，再 scan/summary/build_area_scale；或在 scan 时传入 scope 并在 receipt 明确区分「全量 / 纳入」两套计数。 |
| 5 | P1 | `app/services/cartogram_authoring/quality.py:164-193` | 第二道重叠门 `max_overlapping_pair_ratio` 实际是死门：`over` 用 `max_pair_overlap_ratio` 同一阈值定义，因此 `fraction_cap_violated ⇒ pair_cap_violated`，它永远不能独立失败；而 `max_pair_overlap_ratio` 字段只统计「超阈对」，把真实最大重叠报成 0。 | 3×3、r=10、间距 19.0（相邻对真实重叠 1.33%，门限 2%）：`pairs_over_threshold=0, max_pair_overlap_ratio=0.0, fraction_cap_violated=False`，`max_overlapping_pair_ratio=0.0` 仍 `pass`；`real_max=0.01332`。 | 分别统计：`max_overlap_any`（真实最大，含阈下）与「超过 fraction 阈值的对」用独立、更低的阈值（如 `fraction_threshold`），并让 `pairs_over_threshold_ratio` 基于真实重叠对而非候选对总数。 |
| 6 | P1 | `app/services/print_layout_authoring/compile.py:117-141` | 纸面 adapter 只对圆做 `origin+scale` 变换，`reference_paths` 原样透传（139 行）；即使修好 #2，参照轮廓仍与圆错位/被 viewBox 裁掉。`html._diagram_img` 同病。 | `box=600×400, extent=1000`：`scale=0.4, origin=[140,30]`，圆 x∈[310.9,368.5]，参照路径仍是 `0..1000` 原始点；seam 测试只比较 `len(scene["reference_paths"])`。 | 在 `_compile_diagram` 里对 path 的每个坐标套同一 `scale/origin`（或改成传 `transform` 由 SVG `g` 承担），并断言参照 bbox ⊆ 组件框。 |
| 7 | P2 | `app/services/cartogram_authoring/layout.py:277-282` | `area_scale` 混用两套量纲：`coefficient`/`max_radius` 是归一后值，`min_radius`/`max_value`/`total_value` 仍是归一前值。回执里的 `radius=sqrt(k*value)` 复现不出 `min_radius`。 | `coefficient=1.6479, max_radius=36.309, min_radius=12.0`，但 `sqrt(k*100)=12.837`（artifact 实际最小圆 12.8372）。 | 归一化后重算 `min_radius`（乘 factor），或明确分 `raw_*`/`diagram_*` 两组字段。 |
| 8 | P2 | `app/services/cartogram_authoring/service.py:556-571` | apply 期的「同代」只是 best-effort：只读 descriptor 的 revision，异常被 `except Exception: logger.debug` 吞掉（源不可读时 `source_stale=False` 直接放行）；模块 docstring 宣称「apply 阶段…重新冻结」，代码并未重新冻结/比对 fingerprint。`owner_token`/`trusted_dispatch` 参数在函数体内完全未使用。 | 读代码 + `apply_layout` 全文无 `freeze_source`/`owner_token` 引用；异常分支只置 debug 日志。 | 复用 `freeze_source`（或至少在 descriptor 失败时 typed 拒绝），删掉未使用参数；若保留 best-effort，披露面要如实降级。 |
| 9 | P2 | `app/services/cartogram_authoring/service.py:616-642` | 幂等重放被报成新提交：`apply_gis_mutation` 返回 `result.duplicate` 时未区分，回执硬编码 `mutations: 1, committed: True, can_undo: True`。工具侧 `mutation_id` 是稳定键（`g47:{layout_ref}:r{rev}`），重试就会命中 duplicate。 | `mutation.py:456` 明确判 `result.duplicate` 却不进入本文件的任何分支；619-639 行无 duplicate 处理。 | 透传 `duplicate`，duplicate 时 `mutations: 0` 并如实标注「已存在，无新写入」。 |
| 10 | P2 | `frontend/components/hud/dorling-cartogram-panel.tsx:212` | UI 把 `allowDiagnostic: !layout.publication_ready` 写死：只要未达标就**自动**开启「允许诊断草稿」，无需用户显式确认（仅按钮文案变化）。诚实门的意图是显式人工授权。 | 第 212 行；对照 service 的 `QUALITY_GATE_FAILED` 只有在 `allow_diagnostic=False` 时才触发。 | 用二次确认（复选框/对话框）把该布尔交回用户，默认 false。 |
| 11 | P2 | `app/services/cartogram_authoring/render.py:130-133` | 图例参考圆被 `max(1.0, r)` 钳位：小 `max_radius` 时三个参考圆（数量 900/225/56）全部画成 r=1.00，读者无法用图例核对面积，与「逐圆 clamp 禁止」的口径相悖。 | `max_radius=0.6` → legend refs `[0.6419, 0.3209, 0.1605]`，SVG 三条 `<circle ... r="1.00">`。 | 图例按自身比例单独缩放（图例内一个统一系数），不要用像素下限钳位；或低于可读阈值时改用文字/条形说明。 |

## 已实证通过

- **共同面积系数（可见圆）**：`AreaScale.radius=sqrt(k*v)`，无逐圆 clamp；`r_i²/r_j² == v_i/v_j`（单测 `test_area_ratio_equals_value_ratio_after_normalisation` 通过；`python -m pytest tests/unit/test_g47_*.py -q --no-cov` → 147 passed）。
- **坐标隔离（圆本身）**：`circles[*]` 键集恰为 `{feature_id,label,x,y,radius,value,anchor}`，无 crs/lat/lon；`PrintDiagramSpec` 严格模式拒绝 `crs`/`bbox`（seam 测试通过）。参照轮廓的问题见 #2。
- **确定性**：同输入两次 `compute_layout` 的规范化 payload 完全相同；差异只有 `/metrics/solver/elapsed_s`；`layout_fingerprint(layout)` 可从 artifact 复算且等于回填值（无自引用）。
- **强同代冻结**：`snapshot.freeze_source` 在 `session_lock(fail_on_degraded/lost)` 内 rev0 → 单次 `get()`（`session_data.py:651` 确为锁外 `copy.deepcopy`）→ rev1，漂移抛 `SOURCE_MUTATION_DURING_READ`；job 内再次 `check_anchor`。
- **CAS / 第二次 apply**：`test_g47_service_lifecycle.py` 的 `test_cas_mismatch_writes_nothing`（`CAS_SUPERSEDED`，层无 `cartogram_authoring`，revision 不变）与 `test_second_apply_records_previous_for_undo`（`previous.layout_ref` 为首版）均通过。
- **服务层诚实门**：未达标布局在 `allow_diagnostic=False` 时抛 `QUALITY_GATE_FAILED` 且不写层；显式草稿时回执 `diagnostic=True`（同上测试通过）。
- **导出真实性**：SVG 9 个 `data-feature-id` 圆 + 图例参考值；PNG 96/300 DPI 尺寸比 3.125 且橙色覆盖面积按 DPI² 缩放；PDF 有 A4 横向页几何 + 矢量贝塞尔与 `rg` 填充（`test_g47_publication_readback.py` 14 passed，rsvg/weasyprint 均可用）。
- **分层**：`scripts/check_import_boundaries.py` 对新模块零违规（仅列出 `mixed_map_export.py`/`raster_authoring`/`temporal_media` 9 条既有违规，均未在本分支改动）。
- **i18n**：`frontend/test/i18n/key-completeness.test.ts` 通过（新增 `cartogram` namespace 已登记、zh/en 键与占位符一致）；`no-raw-cjk` 的失败项全在 `settings/skill-workbench.tsx` 等未触碰文件，G47 组件零裸 CJK。
- **资源有界**：`max_features`/`max_adjacency_edges` 预检、`max_iterations`/`max_seconds`/`max_candidate_pairs` 上界、每轮 `should_cancel` 检查点、`rsvg-convert` 120s 超时均存在。

## 未验证 / 残留风险

- **Pi 端到端旅程未运行**：`test_pi_g47_dorling_cartogram_journey.py` 默认 `PI_JOURNEY_E2E!=1` 跳过；且按 #1，其 driver 里 `register_cartogram_tools(reg)` 现在必然抛错。仅 nightly `quality-e2e.yml` 会跑到。
- **真实大样本**（接近 400 单元 / 8000 边 / 细节边界）：未跑；`reference` 的 9000 点上限与单位错配会叠加放大（#2 导致 tolerance 以「米」计的 2.5 单位简化几乎无效，容易提前 truncated）。
- **时间预算下的确定性**：`stop_reason=time_budget` 未进入 `_VOLATILE_KEYS`，慢机/快机会得到不同布局与指纹（内容确实不同，但与「同输入逐位一致」的口径需要补一句边界说明）。
- **仓库级生成物陈旧**：`scripts/check_generated_staleness.py` 报 `docs/quality/*` 4 个文件陈旧（manifest 记 485 tools，当前 506），未改动文件，判为既有漂移，未归因到本分支。
- **导出并发**：`render_png` 每次请求起一个 `rsvg-convert` 子进程，无并发闸（测试里用重阶段锁，生产没有）。未压测。

---

## 实现方修复回应（第二轮，2026-11-15）

评审 11 条全部接受，逐条修复并加回归用例（`tests/unit/test_g47_review_regressions.py`，13 条）。
复审结论见文末。

| # | 级别 | 处理 | 证据 |
|---|---|---|---|
| 1 | P0 | `memory_class="small"` → `"light"`（2 处） | `ToolRegistry` 实际注册到 **6** 个工具；`test_all_six_pi_tools_register_without_swallowing_errors` |
| 2 | P0 | 参考轮廓改走 `source_frame`（与 anchor 同一仿射）；包围盒同时纳入几何 bounds | 45/45 点落在 `[0,1000]`；`x∈[104.78,895.22] y∈[40,960]`；`test_reference_outlines_live_inside_the_diagram_canvas` |
| 3 | P0 | 半径/坐标只在 `plan_circles` 量化一次（`RADIUS_DECIMALS=6`），oracle 按同精度复核，artifact 不再二次 round | 报告值 == 用 artifact 半径复算值（差 <1e-9）；`test_area_gate_recomputes_from_serialized_radius`、`test_area_gate_can_actually_fail_on_quantisation` |
| 4 | P1 | `compute.scoped_features` 先过滤再统计；`plan_layout`/`start_layout`/`compute_layout` 统一 | exclude `r2c2` → `total=3600`、`max_value=800`、8 圆；坏值排除后不再报错 |
| 5 | P1 | 两道门各自独立统计：硬门取真实最大重叠，占比门用 `touch_tolerance_ratio` 作自己的阈值并披露 `fraction_pair_threshold`/`overlapping_pairs` | 2.9% 重叠：`pair_cap_violated=False`、`fraction_cap_violated=True`、`max_pair_overlap_ratio≈0.029`（旧实现报 0） |
| 6 | P1 | `compile._transform_path` 对 path 坐标套同一 `scale/origin` | 纸面 path bbox ⊆ 组件框；参照中心与圆心同框（abs 25 内） |
| 7 | P2 | `min_radius` 归一后重算并标 `radius_units` | `min_radius == min(artifact radius)`（abs 1e-3）且 `== sqrt(k·min_value)` |
| 8 | P2 | descriptor 读不到/代次不可判定 → `SOURCE_VERIFY_FAILED` 拒绝写入；删除未用参数；docstring 改为如实描述 | 生命周期用例通过；新增拒绝分支有 typed code |
| 9 | P2 | `duplicate` 透传：`committed=false, mutations=0, can_undo=false` | 回执字段 + 服务用例 |
| 10 | P2 | 面板显式勾选才允许草稿应用（默认 false，换布局复位），未勾选按钮禁用 | `data-g47-allow-diagnostic`；`panel.diagnosticOptIn` zh/en 双键 |
| 11 | P2 | 图例单一统一系数（`min(0.32, 16/raw_max)`）+ 自适应行高，去掉 `max(1.0,r)` 钳位 | 三个参考圆半径比与声明一致（rel 1e-6） |

评审"未验证/残留风险"中属本任务代码的两项也一并处理：

- `shapely.ops.transform` 弃用 → 改用 `shapely.transform`，566 条 warning 归零。
- 泛 cartogram 文案失真 → 三处文案改为"面域变形未支持；圆面积/Dorling 走作者链路"，并**受控再生成**
  `docs/cartography/map-model-catalog.md` 与 `frontend/lib/map-components/component-catalog.generated.json`
  （后者在 base 上已陈旧，`test_component_catalog_parity.py` 在 base 就红，现 **4 passed**）。

### 复审（自评 + 门禁）

- 后端：`tests/unit/test_g47_*.py` **162 passed**（含本轮新增 2 条 ref 游标回归）；
  `test_g47_print_diagram_seam.py` + `test_g47_service_lifecycle.py` **19 passed**；
  `test_g47_publication_readback.py` 整档 **14 passed / 20.0s**（SVG 9 + PNG/PDF 5，锁外有界执行，见 TEST_MATRIX §F）。
- 前端：`tsc --noEmit` 0 错；`eslint … --max-warnings 0` 0 告警；Vitest **29 passed**（含 i18n 键完备）。
- 仍未解决（如实记录）：真实浏览器 E2E 未执行（沙箱 Next 引导不通）；≈400 单元大样本压测未做。
  两项写进 TEST_MATRIX §K，不当作已通过。

## 第二轮：Pi 旅程实测暴露的 P0-4（评审未覆盖，由真实运行发现）

首跑 `PI_JOURNEY_E2E=1` 是**真红**，5 项 gate 失败。逐层定位后确认**不是**环境问题：

| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| 12 | **P0** | `layout_ref` 未声明 ref 游标语义：registry 的"透明解引用"把它当会话数据别名，就地替换成布局载荷（dict），`ApplyArgs.layout_ref: str` 随即在 Pydantic 校验处报 `Input should be a valid string`。生产会话里布局必然存在 ⇒ **apply 与 export 从 Pi/SSE 调用 100% 失败**（`sse_apply.log`/`sse_export.log` 双 `step_error` 实证） | `ApplyArgs.layout_ref` / `ExportArgs.layout_ref` 声明 `json_schema_extra={"ref_cursor": True}`（registry 提供的正解，不给核心加硬编码）；新增 `test_layout_ref_arguments_declare_ref_cursor_semantics` 与 `test_declared_layout_ref_survives_transparent_resolution` 钉住 |
| 13 | P1（测试） | `_job_row` 读 `getattr(r, "kind")`，而列名是 `job_kind`（ADR-0052）⇒ `job_completed` gate **恒为 False**，是永红的死门 | 改读 `job_kind`，并加注释说明死因 |
| 14 | P1（测试） | `apply/export/run` 三条 gate 只做全日志子串匹配：`"sha256"`/`"job_id"` 在同轮 `tool_search` 的工具描述里就出现 ⇒ 工具 `step_error` 也能判绿（export 实测假绿） | 三条 gate 改为要求该工具的 `step_result` 出现且无同名 `step_error` |

修复后复跑：**1 passed / 81.8s**，23 项 gate 全绿（含 `job_completed`、`apply_via_pi`、
`mapspec_block_committed`、`apply_is_one_mutation_with_undo`、`export_bytes_match_pi_receipt`）。

| # | 级别 | 发现 | 修复 |
|---|---|---|---|
| 15 | P1（测试基建） | `_heavy_stage_lock()` 用**无限期阻塞**的 `flock(LOCK_EX)`：外部 worktree 一持锁，整份 PNG/PDF 回读套件静默挂死且零诊断（本轮实际损失约 1 小时；`/proc`/`/proc/locks`/`fuser` 全不可见持有者）。等价于没有超时的网络调用 | 改为有界等待（`G47_LOCK_WAIT_SECONDS`，默认 60s）→ 失败并给出锁路径/判别依据/两种处置；首次超时后本进程内快速失败，不再逐用例等满；新增显式放行口 `G47_LOCK_BYPASS=1`（打横幅，账本须注明未持锁）。争用路径以外部持锁为夹具实测：**20.4s 出结论** |

## 第三轮：规模实测发现 P1-16（**未修**，需决策）

起因：TEST_MATRIX §K 的「接近上限的大样本（400 单元）」未执行项，本轮实跑。

**结论：`publication_ready` 在 n ≳ 64 时恒为 false，与画布大小无关。** 实测（详见 TEST_MATRIX §L）：

- n=25 → `converged`、0 残重重叠、`max_pair_overlap=0.0034` → ✅ 可发布；
- n=64 / 100 / 400 → 一律 `stalled_with_overlap` 或 `not_converged`，残重渗透 **3.18–5.12
  diagram units**，`max_pair_overlap` 0.031–0.163，超硬门 0.02 → ❌ 不可发布；
- 把 extent 从 1000 放大到 8000（密度降到 0.7%）**不解决**：仍停在 55 个残重对、0.111；
- 迭代预算**不是单调改善**：同 extent=4000 下 400/1200/3000 迭代分别得到
  `max_pair_overlap` 0.0070 / 0.0353 / 0.0898（质量门从通过变失败）。

**形态判据**：n=64 的 12 个最差对密集堆在阈值下方（0.01997 / 0.01995 / 0.01977 / 0.01955 …），
渗透量集中在 3.18–4.27 —— 是**平衡态分布**，不是个别卡死对。

**机制**（已排除两种替代解释）：
1. 不是"弹簧静止距离设错"：吸引的 rest length 已是 `r_i + r_j`，`err ≤ 0` 时跳过；
2. 不是"漏扫导致永远不受力"：`repulsion=1.0` 下 4 单位渗透会产生 ≈1.4 单位/轮位移，
   远大于停滞阈值 `1e-3·max_r ≈ 0.03` ⇒ 两体不可能停住；且质量 oracle 与求解器共用候选对
   集合（`evaluated_pairs` 2972 = `candidate_pairs_last` 2972），违规对**在受力集合内**。
   因此只可能是**多体对向力抵消（挤压卡死）**。

**为什么不在这轮修**：三条候选修法都会改变全部布局坐标 ⇒ 现有位置/指纹类断言需重新基线化，
波及 162 条单测与多份 golden，属"改行为"的重构，必须走独立评审而不是收尾时静默改。
建议作为后续任务，按代价排序：

| 选项 | 做法 | 代价 / 风险 |
|---|---|---|
| A（推荐） | 力迭代后加**位置投影（PBD 式约束）**：对超容差对直接把双方各推开 `excess/2`，不入阻尼与步长帽 | 直接对准门限；同样需要重新基线化 |
| B | 提高 `repulsion`（接触刚度）到残重渗透 < 0.02·(r_i+r_j) | 参数标定，可能引入高频振荡 |
| C | 放宽 `max_pair_overlap_ratio` 到 ~0.05 | **不推荐**：这是弱化质量声明，不是修缺陷 |

现状是**诚实失败**：求解器如实报 `stalled_with_overlap`，质量门如实 `fail`，
artifact 如实 `publication_ready=false` 并打草稿横幅 —— 没有伪成功。P1-16 的问题不是
"撒谎"，而是**能力上限远低于文档宣称的 400 单元**（实测 n=25 可发布、n=64 起不可发布，
上限落在 25–64 之间，本轮未做二分定位）。
