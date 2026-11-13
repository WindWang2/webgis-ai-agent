# REVIEW · G35 独立评审 / 修复 / 复审

reviewer：独立冷读 subagent（未参与核心实现，只读）。材料：`/tmp/g35_full_diff.txt`
（tracked diff + 全部新增文件，7079 行）+ 工作树核对 + 10 项风险清单。
未告知「预期通过」。

## R1 结论（2026-11-13）

**存在阻断项，不建议按现状合并**：2×P0 + 5×P1 + 7×P2 + 9×P3。核心实现真实
（APNG/GIF 容器、独立解码、授权下载、故障注入红检均非 mock），但两个 P0 命中验收目标：

| ID | 级别 | 结论 |
|---|---|---|
| P0-1 | 确认 | 冻结的地理窗口未传给 SVG 编译器：`freeze._viewport()` 在只有 `spec.view.center/zoom` 时返回无 `extent`，`render._compile_svg` 的 `bounds=None` → 编译器按**当帧**要素重算范围 → 动画逐帧平移/缩放，而 manifest 声明 fixed viewport |
| P0-2 | 确认 | 6 个 Pi 工具在生产 dump 的 `default_active` 里是 deferred；旅程靠**手工改写 dump 的 default_active** 才拿到 5 次 dispatch，PR 声称为「生产扩展 + 生产 dispatch」不实 |
| P1-1 | 确认 | `format=gif` + `fps>0`（或相邻相同画面）时 Pillow 自动合并相同 P 帧，`verify_delivery` 按原 `encoded` 对齐 → 必然 `VERIFICATION_FAILED` |
| P1-2 | 确认 | `submit_durable_job` 抛异常时已写下的 pending 域任务永远无人认领，重试命中幂等复用永久卡住 |
| P1-3 | 确认 | `update_state` 无跨进程锁，cancel 与 finalize 并发时终态/成果可互相覆盖 |
| P1-4 | 确认 | 前端改任一设置或勾选观测子集后直接提交，请求体是新参数但 `plan_ref` 是旧计划 → 409 且无「请重新预览」提示 |
| P1-5 | 确认 | held（沿用）段把显示窗口也设成源观测窗 → 图上「2020 缺期」却印着 2019 窗口 |
| P2-1 | 确认 | `ENCODE_TIMEOUT_S` 是死常量；`MAX_TEMP_BYTES` 只有估算无运行时配额 |
| P2-2 | 确认 | 重开恢复按 job_id 字典序取「最新」，可能恢复旧任务 |
| P2-3 | 确认 | 内容 oracle 与渲染共用 `normalize_boundary_value`，不是独立解释 |
| P2-4 | 确认 | 工具契约字段不实：`plan` 有写副作用却声明 `cacheable_read`；`submit` 把后台耗时算进分级 |
| P2-5 | 确认 | `title` 参数被接受但完全不生效且不进 plan_hash |
| P2-6 | 疑似 | `.owner` 写 `session:<sid>`，登录用户只能靠签名 URL；签名泄漏即可下载（既有 export 设计） |
| P2-7 | 疑似 | durable 幂等键不含域 job_id，状态分歧时可能孤儿 |
| P3-1..9 | 确认/信息不足 | 量化余数只记录不补偿、缺期 >64 静默截断、末观测间隔借用、loop 语义差异、manifest 写侧无大小限制、job_id 一致性、DST 交叉证据不足、`current_revision` 语义、`.json` 放宽既有下载面 |

reviewer 明确「通过」的 13 项：真实入口、owner/fail-closed、时间语义、深拷冻结/plan_hash、
APNG chunk/CRC/单帧常驻、资源硬帽、独立解码与 5 类故障红检、i18n 双 locale、
pending 不升级、并行边界与 ChatEngine 已下线。

## 修复（作者，逐项）

- **P0-1** `freeze._viewport()` 重写：显式 `layout.frames[].extent` 优先；否则从冻结文档
  **全部**内联要素（含 G26 静态栅格 `source_bounds`）求一次并集，作为每帧共同的
  4 元素 `bounds` 传给 `compile_mapspec_to_svg_detailed`；无法给出确定 extent 时
  `VIEWPORT_UNFIXABLE` typed 拒绝。`render._compile_svg` 现必拿到 `viewport.extent`。
  新增 `test_T1_frozen_geographic_window_is_fixed_across_all_frames`（spy 记录 5 帧的
  `bounds` 并断言全部等于冻结 extent）与 `test_T1b_layout_frame_extent_takes_precedence`。
- **P0-2** **不修改共享 `pi_native_surface.py`**：先尝试把 6 工具加进 `default_active`，
  实测破坏 4 个既有契约测试（`default_active == NATIVE_TOOL_NAMES`、deferred exposure），
  已完整回退。改为采用**生产 deferred 发现路径**：与 G26 `webgis_map_export` 同款，
  旅程先 `tool_search` 激活再按名直调；**删除 dump 的 `default_active` 改写**，
  断言生产 dump 零改写下 6 工具在超集、`exposure=deferred`、带完整 schema。
  新增 `test_T3_g35_tools_follow_production_deferred_surface_contract`（同时钉住
  `default_active == NATIVE_TOOL_NAMES` 不变量），旅程新增 `deferred_discovery=true`。
  既有 120 个 Pi surface / 导出鉴权测试回归全绿。
- **P1-1** `build_plan` 对 `format=gif && fps>0` typed 拒绝（`GIF_FPS_UNSUPPORTED`）；
  `write_gif` 按量化后真实像素**显式折叠**相邻相同帧并合并 segment/observation/时长映射，
  `encode_media` 返回 `effective_encoded`，runner 用**实际容器帧映射**做核验与 manifest；
  UI 在 gif 时禁用/归零 fps。新增 `test_T2…` / `test_T2b…`。
- **P1-2** `submit_durable_job` 包 try/except：异常把域状态写 `failed`（不留在 pending），
  返回 `DURABLE_DISPATCH_FAILED` 503 并允许重试；复用分支检测 `durable_job_id` 为空的
  孤儿 pending 并释放；幂等 `params` 纳入 `media_job_id`（同时修 P2-7）。新增
  `test_dispatch_failure_marks_failed_and_retry_is_allowed`。
- **P1-3** `update_state` 加 `fcntl.flock(state.lock, LOCK_EX)` 跨进程临界区；终态字段
  **整体**从 `before` 恢复（status/phase/progress/artifacts/verification/error/message），
  不只回退 status。新增 30 轮双线程 `test_concurrent_cancel_and_finalize_never_loses_terminal_state`。
- **P1-4** 面板用 `previewArgsRef` 记录生成当前预览的请求体；`onSubmit` 检测到
  settings/子集变化则**自动重新预览**取新 `plan_ref` 再提交。新增前端用例断言
  `previewMediaPlan` 调用 2 次且 submit 使用新 ref 与新 dwell。
- **P1-5** `display_window` 恒取本段（缺期）窗口，只有 `content_window` 取 `held_from`
  观测窗；`verify_delivery` 新增 SVG 窗口注记断言。新增 `test_T1b_held_display_window_note…`。
- **P2-1** `write_apng`/`write_gif` 每帧检查 `time.monotonic()` 与
  `ENCODE_TIMEOUT_S` 墙钟预算，超时抛 `ENCODE_TIMEOUT`；回执 `encode_timeout_enforced=true`；
  GIF 常驻预算按 3× 计（P 图 + RGB 中间量）。
- **P2-2** `initial_state` 记 `created_at`，`list_jobs` 按时间排序并返回该字段，
  前端按 `created_at` 取最近任务。新增前端恢复用例。
- **P2-3** `_oracle_expected_ids` 改为**独立**把契约 ISO instant 与要素原始值都解析成
  aware datetime 后做 `[start,end)` 比较，不再调用 `normalize_boundary_value`；
  新增 SVG 窗口注记校验。
- **P2-4** `plan` 改 `side_effect="state_mutation"` + `produced_refs=("plan_ref",)`；
  `submit` 改 `fast/light/small`（耗时属后台 job 阶段）。
- **P2-5** 删除 `MediaPlanArgs.title` 与计划里的空操作字段（关闭词表不再暴露无效参数）。
- **P2-6** 不改既有 export 全局路由（会扩大影响面）；在 PR_HANDOFF / PR_BODY 中**如实**
  披露：签名 URL 是实际授权，`.owner` 侧车在该 principal 口径下只作存在性检查，
  不再宣称「双闸门」。列为已知限制。
- **P3** 最大余数法补偿量化误差；缺期 >64 加 `gaps_truncated` 披露；proportional 增加
  `interval_assignment` 说明；`MAX_MANIFEST_CHARS` 提到 16 MiB 并在写侧校验；
  `load_state` 校验 `job_id` 一致性；移除 `_MEDIA_TYPES` 的 `.json`（避免放宽既有下载面）；
  loop 语义差异进 disclosures。

## 修复后本地验证

| 套件 | 结果 |
|---|---|
| `pytest tests/unit/temporal_media` | **60 passed** |
| Pi 全链旅程（不改写 dump，含 `tool_search`） | **1 passed**，`verdict.fail=[]`，`deferred_discovery=true` |
| 既有 Pi surface / 导出鉴权回归（10 个文件） | **120 passed** |
| 前端 vitest（5 文件） | **34 passed** |
| `tsc --noEmit` / `ruff check` / `git diff --check` | 0 error / 全通过 / CLEAN |

## R2 复审结论（同一独立 reviewer，静态冷读 7752 行 R2 diff）

**结论：R1 的 2×P0 + 5×P1 全部真实清偿，无新 P0/P1 回归；但首轮 R2 未签
「全部清偿」**，因为仍有 1 个用户可见 P2 残留（U1）、1 个测试覆盖缺口（U2）、
2 处过度声明（U3/U4）与文档矛盾（U6）。reviewer 明列 8 项 U1–U8 与 6 条
「提交 PR 前必须动作」。作者按清单全部闭环后复跑验证。

| ID | R2 判定 | 作者闭环 |
|---|---|---|
| U1 P2 子集勾选 → 预览 → 提交丢失子集 | 确认残留 | `service.preview` 新增 `all_observations`（全部观测清单，不受子集筛选）；前端 checkbox 改用它、`loadPreview` 不再 `setSelected(null)`；新增前端用例断言 `frame_ids=['f-2019','f-2024']` 且 `plan_ref` 为子集版本、提交不二次重预览 |
| U2 P2 GIF 折叠分支无真实触发用例 | 确认残留 | 新增 `test_T2c_gif_collapse_branch_is_really_exercised`：构造 PNG 元数据不同→字节不同、像素相同的相邻帧，真实触发 `write_gif` 折叠；断言 `container_merged_frames==1`、effective 合并映射、容器解码 1 帧 2000ms；A/B/A 非相邻相同帧不误合并 |
| U3 P2 `MAX_TEMP_BYTES` 无运行时配额；×3 声明与代码不符 | 部分清偿 | `planning.py` GIF 常驻预算实装 3×（P 帧 + RGB 中间量）；`runner.py` 按真实写入字节累计帧+SVG+容器，`> MAX_TEMP_BYTES` 抛 `RESOURCE_BUDGET_EXCEEDED`（预检之外新增运行时配额） |
| U4 P2 渲染侧 `_independent_expected_ids` 命名误导 | 部分清偿 | 改名 `_render_window_expected_ids`，docstring 明示它复用 `normalize_boundary_value`、只是渲染侧自校验；权威独立 oracle 明确为 `verify.py::_oracle_expected_ids` |
| U5 P3 `title` 前端类型与死常量残留 | 部分清偿 | 删除 `frontend/lib/api/temporal-media.ts` 的 `title?: string`、`contracts.py` 的 `MAX_TITLE_CHARS` 常量与导出 |
| U6 P3 文档三处矛盾 | 确认 | `PLAN.md` 改为「3 个 media type（`.apng/.gif/.zip`），未加入 `.json`」；×3 已实装故 `REVIEW/INTERFACES` 声明与代码一致；前端计数口径在 TEST_MATRIX 统一标注（14 = i18n/命令目录，18 = 面板/API/时间轴，35 = 五文件合计） |
| U7 P3 终态恢复字段不全等 | 确认 | `update_state` 终态恢复清单补 `cache_hits/current_frame/total_frames`；`_all_feature_extent` 的 `len(xs)<2` 已由 `spec.view.bounds` fallback 覆盖；`_call` 不可达 return 为防御性兜底，保留 |
| U8 证据 三类数字不在 diff 可复核范围 | 确认 | TEST_MATRIX.md 附实际命令、文件清单与逐套件输出；R2 为静态复审，但 U1–U7 修复后作者已完整复跑并在下表记录 |

**R2 后复跑（作者，真实执行）**：

| 套件 | 结果 |
|---|---|
| `pytest tests/unit/temporal_media` | **61 passed** |
| Pi 全链旅程（生产 `tool_search`，dump 零改写） | **1 passed**，`verdict.fail=[]` |
| 既有 Pi surface / 导出鉴权回归（10 文件） | **120 passed** |
| 前端 vitest（5 文件） | **35 passed** |
| `tsc --noEmit` / `ruff check` / `git diff --check` | 0 error / 全通过 / CLEAN |

**最终评审结论：无阻断项（P0/P1 及影响关键验收的 P2 均已闭环）。**
U7 中的低危防御性问题与「未与真实浏览器同链联跑 Pi」「未等线上 CI」属
PR_BODY 已列明的外部分层限制，不是遗留 P0/P1。
