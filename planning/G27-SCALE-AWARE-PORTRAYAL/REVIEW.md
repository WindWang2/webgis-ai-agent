# G27 REVIEW — 独立评审 / 修复 / 复验记录

## R1 — 第一轮独立 review（未参与实现的 `g27-reviewer`）

- 审查对象：`8a9a79f76875eb95d31bd448baad971edb2e61d3` → `46b93d7d2`（4 commit）
- 复跑（reviewer 自行执行，非采信自述）：后端 `68 passed`、前端等价 `23 checks passed`、`py_compile` 15 文件 Exit 0、`git diff --stat` 31 文件 / +4178 / -2、tsc 基线 `toolSlice.ts:32 TS1109`（#2085 在途，G27 相关 0）
- 结论：**阻断未归零**。1 BLOCKER + 4 P1 + 4 P2 + 2 P3。
- 关键实证发现：reviewer 自行实跑坐实「converter 与 service 对同一 `priority_field` policy 判定相反」（converter `applied=False / GHOST_PRIORITY_FIELD`，service `applied=True`）。
- 肯定项（已排除）：偷删/抽样分析数据、重写用户锁/pin、DPI 误作语义 zoom、第二套 MapSpec 文档、倒退 v1.5、触碰 G22 vendor、带入 #2084–2086 修复、改 master/CI 门禁、改动起始工作区未提交文件。
- 原始 findings 全文见本文件 §R1 表（下表为逐项处置，不重述证据路径）。

## R1 findings → 处置（主 Agent 修复，reviewer 不写产品代码）

| ID | 级别 | 处置 | 修复位置 / 理由 |
|---|---|---|---|
| F1 | BLOCKER | **修复** | 本 `REVIEW.md` 落地；`LOOP_LEDGER.md` 更新至 L1/L2 真实状态；下一条 `fix(g27)` commit 记录 R1→修复；R2 复验结论见 §R2。修复前自述「已完成 review→fix→复验」确属不实，现以真实 commit 历史为准。 |
| F2 | P1 | **降级 + 去误导命名** | 真实 `apply_gis_mutation` CAS/undo/provenance 引擎链在本方向最小 venv 中因 `pydantic_settings`/服务栈依赖缺失**无法实跑**（属外部阻断，见 TEST_EVIDENCE §5）。A19/A20 由 `verified` 降为 `partial`；测试名去掉 `undo_chain` 字样；不再声称「CAS verified」。隔离 fixture 只证明 service 构造的局部 layer / 参数 / 回执映射。 |
| F3 | P1 | **代码修复** | converter 改用共享 `SCALE_POLICY_NAMESPACE` 常量，并新增与 service 同源的有界 `_g27_available_fields`（前 50 要素 / ≤32 键）传入 `resolve_effective_scale_portrayal`。新增 `test_f3_converter_and_service_agree_on_priority_field` 契约用例。 |
| F4 | P1 | **重写断言** | `plan_scale_contexts` 的 `visibility_semantics` 改为由真实 `export_adjudicate_layer` 裁决结果填充（不再硬编码常量）；A15 测试改为「三种 DPI 的物理分母互不相同，但语义 zoom / frame extent / 逻辑 viewport / 真实裁决摘要逐位相同」——可证伪。ACCEPTANCE 指向修正后的测试。 |
| F5 | P1 | **代码修复 + 表述降级** | ① `project_scale_portrayal_spec` 默认不再投影文档级 `thresholds.maxFeatures`（原实现投影后被 converter 与 service 双双丢弃，且自动改写会影响存量导出语义）；`decimate_budget` 保留为解释面 display-budget 披露，仅 `include_budget=True` 显式请求时才返回。② `component_portrayal_state` 在 `inspect_scale_portrayal` 真实回执中成为生产消费者（组件状态随 UI/Agent 回执输出）。③ `terrain_maxzoom` 明确只进解释面（任务规定 terrain 不扩展）。INTERFACES/ADR/PR_BODY 相应降级；A12/A17/A18 降为 `partial`。 |
| F6 | P1 | **真实规模重测** | `perf_probe.py` 改为真实构造 5000/20000/8000 个合成要素，测量完整解析链；`feature_count_unchanged` 逐轮断言 `len(features)==n`；`mutation_module_loaded` 由 `sys.modules` 实测；`persistent_mutation_calls` 为纯函数路径的推导定值（探针不引用任何持久化 mutation API）。新数据见 TEST_EVIDENCE §4。A25 仍标注「真实浏览器帧时间 blocked」。 |
| F7 | P2 | **文档明确 + 状态同步** | 明确 `applied` 唯一权威在后端 `EffectiveScalePortrayal`；前端 `isScalePortrayalApplied` 只是**更保守的本地显示投影**（有 explanation 时完全以它为准），不是第二套完成判定。`LOOP_LEDGER` 不再停在 L0。 |
| F8 | P2 | **代码修复** | `resolve_effective_scale_portrayal` 的解释面 `label_zoom_bands` 统一取 `spec_label_zoom_bands()`（[0,24)），与 spec 投影/runtime 消费值逐位一致；`INTERFACES.md` 与前端常量注释同步；新增 parity 断言。 |
| F9 | P2 | **代码修复** | service 对 `origin="user"` 且缺 `expected_revision` 的 commit 返回 typed `MISSING_EXPECTED_REVISION` 回执，不静默落库；新增用例。 |
| F10 | P2 | **测试接真实 registry + 验收降级** | 组件测试白名单改取 `get_component_registry().get(...).variants`（不再自造字典）；A12/A17/A18 降为 `partial`（纯函数 + 真实 registry 静态验证，未接真实 renderer 变体渲染）；A21 明确为「解释面加性入口」，不声称导出渲染已按各 frame viewport 重排。 |
| F11 | P3 | **代码修复** | converter 命名空间改用共享常量；authoring 参考 zoom 注释澄清；新增 `test_f11_exception_path_leaves_no_partial_projection` 断言异常路径零部分写入。 |
| F12 | P3 | **代码修复** | 前端 `hasScalePortrayalConsumer` 收紧为「`zoomBands` 与 G27 spec 投影 4 档逐位一致」，任意非空数组不再算已接线；parity 的 z=24 边界改为显式钳制语义并由 `spec_label_zoom_bands()` 全域覆盖保证，不再用 1e-9 hack 掩盖。 |

## 主线整合（review 后）

- `git fetch origin master`：主线由 `8a9a79f` 前进到 `b8f2a6ecc`（#2084/#2085/#2086 均已合并，开放 PR 清空）。
- `git merge --no-edit origin/master`：ort 策略无冲突（`5cbefc82b`）。合并带来 `toolSlice.ts` 修复 → tsc 的 TS1109 基线错误消失；剩余唯一 tsc 错误 `lib/map-kit/render-debouncer.ts` maplibre default export（npm 与 pnpm-lock 版本差异的本地基线，G27 相关 0）。
- 整合后复跑见 §R2。

## R2 — 复验（修复 + 整合后）

| 项 | 命令 / 结果 |
|---|---|
| 后端 6 测试文件 | `72 passed, 2 warnings`（reviewer R2 自行复跑 `71 passed in 0.86s`，其后补 A15 gate 用例增至 72；较 R1 的 68 增加 F3/F9/F11 等契约用例） |
| 前端等价执行（tsc 纯 JS 转译 + Node） | `23 checks passed`（含收紧后的 `hasScalePortrayalConsumer`） |
| 前端全量 tsc | `TOTAL 1 / G27_RELATED 0`（唯一为 `render-debouncer.ts` 基线） |
| 真实规模性能探针 | 5000/20000/8000 真实要素，每档 0.446–0.505 ms，峰值 14–34 KB，`feature_count_unchanged=true`，`mutation_module_loaded=false` |
| 语法 | 15 个新增/修改 `.py` `py_compile` Exit 0 |
| 起始工作区 | `M vendor/pi` + `.claude/skills/*` + `_tmp_47_*` 仍只读保留 |

**R2 结论（同一独立 reviewer 已复审确认）**：代码级阻断归零，同意以 Draft PR 提交；点名的文档/测试诚实性残留已在 `docs(g27)` 收尾 commit 中修正。：P0/P1/P2 的**代码级阻断**（F3 判定不一致、F4 空断言、F5 假接线、F6 空跑、F8 档位漂移）已修复并有可复跑实证；F1 流程门槛已补齐。剩余均为**外部阻断**：真实 Pi 子进程 RPC 全链、本地真实浏览器 E2E、真实 CAS 引擎链（依赖栈）、真实浏览器帧时间（4 GiB VA 硬限）。这些在 ACCEPTANCE 中如实标 `blocked/partial`，PR 以 Draft 提交，不给 Ready 结论。

> reviewer 本人不得给自己 approval；本文件由主 Agent 记录，R2 结论由独立 reviewer 复审后确认或驳回。
