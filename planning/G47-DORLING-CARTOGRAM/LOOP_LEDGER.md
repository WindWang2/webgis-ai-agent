# G47-DORLING-CARTOGRAM — LOOP_LEDGER

> 每轮只记"发现 → 改动 → 证据"。数字都是本机实际跑出来的，不是估计。

## L0 起点（基线探测）

| 事实 | 证据 |
|---|---|
| `origin/master` = `655f769c…`，open PR = 0 | `gh pr list --state open` 空 |
| 泛 cartogram 仍未实现、`cartogram_map` 仍是 `planned` | `presentation_views.py:42-56` |
| 非 Dorling 的 cartogram 投影已诚实降级 | `render_diagnostics.py` + `frame-composer.ts` 的 `cartogram_unsupported` |

## L1 计算内核（WP01–WP07）

按"数量语义 → 地理资格 → 面积系数 → 有界求解 → 图面归一 → 独立 oracle → artifact"
顺序落地，纯函数无 IO。**首轮冒烟即暴露真实缺陷**：

| 发现 | 根因 | 修复 |
|---|---|---|
| 首轮 `status=not_converged`、`max_ratio≈198`、邻接全丢 | 米级 anchor 与 diagram 半径混用，吸引力把圆甩出画布 | `compute._normalize_anchors` 把 anchor 归一等比进画布；位移改为仅披露 |
| `layout_fingerprint` 恒为 `None` | `assemble_layout` 从不写入，下游却从 `metrics.layout_fingerprint` 读 | 先占位再回填，并加入 `_VOLATILE_KEYS`（避免自引用） |
| 包围盒只按圆算，anchor 被推到画布外（x=-1186） | 定标未纳入 anchor | 包围盒同时容纳圆与 anchor（单一 uniform factor，面积比不变） |

## L2 测试驱动的三处真实缺陷

写测试时发现实现错了（不是测试错）：

| 症状 | 根因 | 修复 |
|---|---|---|
| `RATE_OR_MIXED_UNIT` 分支不可达 | `parse_plan` 里 `quantity.` 前缀分支排在语义分支之前 | 语义判定优先，字段前缀用 `quantity.` 精确匹配 |
| 重叠门只看占比，1 对 100% 重合也能过 | 缺"任一圆对"硬门 | 增加 `pair_cap_violated`（后经 L4 进一步拆开两道门） |
| `Polygon` 没有 `is_valid_reason()` | shapely 2.x API | 改用 `shapely.validation.explain_validity` |

## L3 服务面 / 出版面 / 前端

| 面 | 关键点 |
|---|---|
| durable job | `JobKind.cartogram_layout` 加性新增；幂等键含 `plan_digest + content_revision + ALGORITHM_VERSION` |
| apply | 单笔 CAS + `previous` 记录 undo；未达门需 `allow_diagnostic` |
| 纸面 | `diagram` kind + `PrintDiagramSpec`（`extra="forbid"`，拒绝 crs/bbox） |
| 导出 | SVG 真实 `<circle data-feature-id>`；PNG 走 `rsvg-convert` 按 DPI；PDF 走 WeasyPrint 内联 SVG |
| 前端 | 等比图面 + 缩放/平移/命中/键盘；面板串起 inspect→…→export |

本轮门禁结果：后端 G47 全量 **180 passed**；前端 `tsc --noEmit` 0 错、`eslint --max-warnings 0` 0 告警；
Vitest 19 passed；i18n `key-completeness` 通过。

## L4 独立评审（未参与实现）→ 3×P0 / 3×P1 / 5×P2

评审给出 **CHANGES REQUIRED**，逐条都是真缺陷。修复与证据：

| # | 级别 | 缺陷 | 修复 | 回归证据 |
|---|---|---|---|---|
| 1 | P0 | 6 个 Pi 工具只注册 3 个（`memory_class="small"` 非法 → 模块加载抛错被降级为 warning，`status/apply/export` 静默消失） | 改 `memory_class="light"` | `test_all_six_pi_tools_register_without_swallowing_errors`；手动 `ToolRegistry` 注册得 6 个工具名 |
| 2 | P0 | 参考轮廓用投影米坐标套示意变换：45 个点 0 个落在 1000×1000 内（x≈1.24e7），"原地理参照"实际为空且把米值写进声明非地理的 artifact | `plan_circles` 新增 `source_frame`：几何先过与 anchor **同一个**仿射，再套 `_tx/_ty`；包围盒同时纳入几何 bounds | `test_reference_outlines_live_inside_the_diagram_canvas`（45/45 落在 `[0,1000]`，最大绝对值 < 1e5）、`test_reference_outlines_keep_the_original_grid_shape` |
| 3 | P0 | 面积门是恒等式空转：oracle 用未 round 的内存半径反算，`max_rel_error` 恒 ≈0；实测 `publication_ready=True` 而落盘误差 5.4% | 半径/坐标**只在一处**按 `RADIUS_DECIMALS=6` 量化，oracle 按同一精度复核，artifact 不再二次 round | `test_area_gate_recomputes_from_serialized_radius`（报告值 == 用 artifact 半径复算值，1e-9）、`test_area_gate_can_actually_fail_on_quantisation` |
| 4 | P1 | `scan_quantities` 在 scope 过滤之前跑：被排除单元仍计入总量/k/计数，"排除"建议无法执行 | 新增 `compute.scoped_features`，`plan_layout`/`start_layout`/`compute_layout` 统一先过滤再统计 | `test_excluded_unit_does_not_enter_total_or_coefficient`、`test_only_the_remaining_max_drives_the_common_coefficient`、`test_scope_exclusion_actually_removes_a_bad_value` |
| 5 | P1 | 第二道重叠门是死门（与硬门共用阈值 ⇒ 恒被蕴含），且 `max_pair_overlap_ratio` 把真实最大值报成 0 | 两道门各自独立统计：硬门取**真实**最大重叠；占比门用「相切容差」`touch_tolerance_ratio` 作自己的阈值，并披露 `fraction_pair_threshold`/`overlapping_pairs` | `test_fraction_gate_is_not_dead_and_reports_true_max`（2.9% 重叠：硬门 5% 不破、占比门 0 破） |
| 6 | P1 | 纸面 adapter 只缩放圆，`reference_paths` 原样透传 → 纸面灰线与圆错位/被裁 | `compile._transform_path` 对 path 坐标套同一 `scale/origin` | `test_print_scene_transforms_reference_paths_into_component_box`、`test_print_scene_keeps_reference_and_circles_in_the_same_frame` |
| 7 | P2 | `area_scale` 混量纲：`coefficient/max_radius` 已归一，`min_radius` 还是归一前 | 归一化后重算 `min_radius`，并标 `radius_units` | `test_area_scale_min_radius_is_in_diagram_units` |
| 8 | P2 | apply 期"同代"只是 best-effort（异常被吞、参数未用），docstring 却宣称重新冻结 | descriptor 读不到/代次不可判定 → `SOURCE_VERIFY_FAILED` typed 拒绝；删除未使用的 `owner_token/trusted_dispatch`；docstring 改成如实的"descriptor 复核" | `test_source_rewrite_makes_old_layout_stale` + 新增拒绝分支 |
| 9 | P2 | 幂等重放被报成新提交（`duplicate` 未透传） | 透传 `duplicate`：`committed=false, mutations=0, can_undo=false` | 服务生命周期用例 + 回执字段 |
| 10 | P2 | 面板把 `allowDiagnostic: !publication_ready` 写死 → 诚实门形同虚设 | 显式勾选框，默认 false，换布局即复位；未勾选时按钮禁用 | `data-g47-allow-diagnostic` 受控 + i18n 键 |
| 11 | P2 | 图例参考圆被 `max(1.0, r)` 逐圆钳位，三个小圆全变同一半径 | 图例自带**单一**统一系数（`min(0.32, 16/raw_max)`），行高随半径自适应，去掉钳位 | `test_legend_reference_circles_keep_their_radius_ratio` |

附带修复（评审"未验证/残留风险"里指出、且属本任务代码）：

| 项 | 处理 |
|---|---|
| `shapely.ops.transform` 弃用（新代码里的 566 条 warning） | 改用 `shapely.transform` 向量化 API，warning 归零 |
| 泛 cartogram 文案失真（"cartogram 渲染运行时未实现"） | 后端 spec、前端 detail、`presentation_views` 三处改为"面域变形未支持；圆面积/Dorling 走作者链路"，并**受控再生成** `map-model-catalog.md` 与 `component-catalog.generated.json` |
| `component-catalog.generated.json` 在 base 上已陈旧（`tests/unit/test_component_catalog_parity.py` 在 base 就红） | 用生成器（`app.lib.cartography.export_component_catalog`）受控再生成；parity **4 passed** |

## L5 复审与收口

- 前端：`tsc --noEmit` 0 错；`eslint --max-warnings 0` 0 告警（`lib/cartogram`、`components/cartogram`、
  `dorling-cartogram-panel.tsx`、`layer-style-panel.tsx`、`cartography-result-card.tsx`）；Vitest 19 passed；
  i18n `key-completeness` 通过（`no-raw-cjk` 的失败项全在未触碰文件，属 base 既有债）。
- 后端：`tests/unit/test_g47_*.py` **160 passed**（含新增 13 条回归）；`print_diagram_seam` + `service_lifecycle`
  **19 passed**；`publication_readback` 见 TEST_MATRIX（重阶段锁排队执行）。
- 已知环境坑：`vendor/pi` 子模块 clone 两次失败（`fetch-pack: unexpected disconnect`），改用同 pin
  （`b4afddd4`）的既有 worktree 检出复用；`pi_vendor_identity()` 报 `entry_built=True, kind=bundle, matches_expected_pin=True`。
- 重阶段锁被其它 worktree 长时间占用时，集成测试会在 `flock` 上排队 —— 这是设计行为，不是死锁；
  非加锁用例（seam/lifecycle）先跑，加锁用例（readback）等锁。

## L6 Pi 旅程实测 → P0-4 → 全绿 → 交付

| 步骤 | 动作 | 结果 |
|---|---|---|
| 1 | 跑 `PI_JOURNEY_E2E=1 test_pi_g47_dorling_cartogram_journey.py` | ❌ **真红**：`job_completed` / `apply_via_pi` / `mapspec_block_committed` / `apply_is_one_mutation_with_undo` / `export_bytes_match_pi_receipt` 五项失败 |
| 2 | 用 `TMPDIR` 固化 WORK 目录拿 `sse_*.log`（直接跑驱动会因缺 conftest 的 Redis 而 LockDegraded，必须在 pytest 内跑） | 拿到 `step_error: 参数 'layout_ref' 校验失败: Input should be a valid string` |
| 3 | 逐层排除：`ApplyArgs` 直验通过、`reg.dispatch` dict/JSON 串两形态均通过 ⇒ 问题不在模型也不在 dispatch 解析 | 锁定 registry「透明解引用」阶段（`skip_keys` 只有 ref_id/layer_ref/layer_id/plan_id…，没有 layout_ref） |
| 4 | 修 P0-4：两个 args model 的 `layout_ref` 声明 `ref_cursor` | `app/tools/cartogram_tools.py` |
| 5 | 修两条测试自身缺陷：`kind`→`job_kind`；三条 gate 从"全日志子串"改为"该工具 step_result 出现且无 step_error" | `test_pi_g47_dorling_cartogram_journey.py` |
| 6 | 加 2 条回归钉住 ref 游标语义 | `test_g47_review_regressions.py`（15 passed） |
| 7 | 复跑 | ✅ **1 passed / 81.8s**，23 gate 全绿 |
| 8 | 外溢检查 | `test_g47_*.py` **162 passed**；ruff 全绿；`test_descriptor_coverage_gate` / `test_capability_registry_parity` 红，但 `git stash` 后同样红 ⇒ base 既有债 |
| 9 | 交付 | 分支 push 完成；PR **#2125** 已创建（`base=master`） |

教训（写进流程而不是只写进文档）：**"只差重活没跑"不能当成"逻辑没问题"** —— 本轮最严重的缺陷
（apply/export 在真实会话里必然失败）正是只有端到端旅程才能暴露的那一类；而旅程本身的三条 gate
又恰好因为子串匹配而假绿，差点把它掩盖过去。门必须钉在**该工具自己的回包**上，而不是整段日志里。
