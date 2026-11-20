# G34 REVIEW — 独立评审 / 修复 / 复审记录

状态：**Round 1 = BLOCK（3×P0）→ 修复 → Round 2 = SHIP-WITH-FIXES（P0 全关，2×新 P1 已修）**。

## 协议

- Reviewer = 第 3 个 subagent 槽位，**未参与核心实现**（槽 1/2 为只读调研）。
- 输入：最新 base SHA、完整 diff、用户场景、兼容边界、真实测试证据、DEDUP_MATRIX、INTERFACES、TEST_MATRIX。
- **不告知「预期通过」**；reviewer 自行复跑关键用例。
- 检查面：真实入口、权限/所有权、科学语义与单位、范围/版本/身份、CAS/取消/重试、资源上限、UI 可用性、Pi 契约、导出回读、最新 PR 重叠。
- Finding 格式：`file:line` + 触发输入 + 用户影响 + 复现/代码证据 + 修复建议 + 级别（P0/P1/P2/P3）。
- 修复本任务引入的阻塞项 → 补回归 → **原 reviewer 复审**（`resume` 同一 agent，不新增槽位）。
- 未解决 P0/P1 或影响关键验收的 P2 → 不得标 completed。与本任务隔离的遗留问题附干净 base 证据。

## Round 1 — base `ca7b3ec27`，判定 **BLOCK**

Reviewer 自行复跑得到 3 个 P0，全部成立（作者随后用真实 mutation 链逐条复现确认）：

| # | Finding | 影响 | 证据 |
|---|---|---|---|
| P0-1 | `service.apply` 复用 `propose`，后者重新 `load_mapspec` 且 `initialize` 被强制 False | **首建必然失败**，UI 与 Pi 两条入口上线即不可用 | `service.py:280`（旧） |
| P0-2 | `check_source_revision` 双向绑定 `bound`/`current`，`source` 无 rebase 出口 | 任意其它 mutation 后**永久 SUPERSEDED**，版式再也改不动 | `validate.py:43-48`（旧） |
| P0-3 | 锁三处实现不一致；`replace_plan` 只看 candidate 里存在的组件；`apply_template` 无锁检查 | **锁组件可被静默删除**（数据丢失）；`locks=["scale"]` 被 `scale__v2` 绕过 | `operations.py:184`（旧）、`template.py:121-132` |

Round 1 同时报告 13 条 P1、8 条 P2，以及「27 条 oracle 无一条触达 `service.apply`」的覆盖缺口。

## 修复（Round 1 → Round 2）

| 项 | 处置 | 落点 |
|---|---|---|
| P0-1 | 抽出纯函数 `_evaluate()`（不改 store、不改入参），`propose`/`apply` 共用；`allow_empty` 允许「只建默认计划」 | `service.py:229-274` |
| P0-2 | CAS 为唯一硬门（`expected_revision` vs 权威 current）；`bound != current` 降级为 `binding_drift` + `rebased_from` 并在回执披露；成功 apply 重绑 `source` | `validate.py:31-63`、`service.py:325-329` |
| **P0-4（新发现）** | `_spec_revision` 读 `mapspec["revision"]`（文档上**不存在**）恒得 0 → 任何非零 revision 会话都判 SUPERSEDED。改为 async 读 `session_data_manager.get_state_field("_cartographic_mutation_revision")`，与 `raster_authoring.current_revision` 同源 | `service.py:80-97` |
| P0-3 | 单一谓词 `_lock_hit()` 包 `_lock_matches`；收口处 `_assert_locks_preserved()` 对每次 `apply_operations` 校验锁组件不消失、9 个受保护字段不变；`replace_plan` 的残版预检删除 | `operations.py:303-343` |
| P1 字体 fallback | `css_font_stack()`：作者族在前 + 受控 CJK 兜底族；SVG/HTML 同源；无引号标识符避免 `html.escape` 破坏属性 | `fonts.py:283-300`、`compile.py:190`、`html.py:71` |
| P1 SVG 物理尺寸 | `_with_physical_size()`：导出 SVG 根节点 `width/height` 改 mm，viewBox 不动 | `service.py:499` |
| P1 预览/导出纸面 | `_paper_for()` 单点解析；`page_plan` 解析页级 `paper_profile` 为真实 mm | `service.py:433-448`、`pages.py:126-190` |
| P1 REST 错误 | `_call()` 包住全部 handler，`SOURCE_UNAVAILABLE` → 503 而非 500 | `routes/print_layout.py:36-47` |
| P1 UI CAS 基线 | `cursor.revision` → `state.revision`（inspect 基线） | `panel.tsx:150-153` |
| P1 截断诚实 | `wrap_text` 真报 `truncated` / `truncated_lines`，`TEXT_TRUNCATED` 可达 | `fonts.py:465-466` |
| P2 页 id 去重顺序 | 去重移到 G34 页级覆写查找**之前**，并删除重复循环 | `publication_ir.py:516-529` |
| P2 漂移不可见 | `binding_drift` / `rebased_from` 进 apply 回执 | `service.py:355-358` |
| P2 阻塞丢失错误码 | `_evaluate` 校验失败时回具体 `code` + `issues` | `service.py:254-258` |

新增回归：`tests/cartography/test_g34_apply_chain.py`（7 条，含 initialize 落盘、漂移重绑、锁删除/族后缀/图例/模板清空/页级覆写、缺字兜底度量、SVG mm）与 `tests/integration/test_pi_g34_print_layout_journey.py`（真实 Pi 子进程）。

## Round 2（复审，同一 reviewer resume）

判定 **SHIP-WITH-FIXES**：

- P0-1 `FIXED`（实测 `test_t27` / `test_t29` / `test_t30` 全绿）
- P0-2 `FIXED`（实测 `test_t28`：`set_view` 后二次 apply committed，旧 expected 仍 SUPERSEDED）
- P0-3 `FIXED`（原三条 repro 全部被拒且零写入）

复审新增 findings，本轮**已全部修掉**：

| # | Finding | 处置 |
|---|---|---|
| NEW P1-1 | 页级 override 绕过全部锁不变量（`set_pages` / `replace_plan` 写 `atlas.pages[*].overrides` 改锁组件） | `_assert_locks_preserved` 增加「每页生效值」比对（`pages.apply_page_overrides`）；`test_t32` 锁定 |
| NEW P1-2 | `css_font_stack` 让兜底真渲染（1.0em），但 `_advance_pt` 仍按 0.6em 估缺字 → 宽度低估约 40%，换行/对齐与 PDF 不一致 | `_fallback_metrics()` + `_advance_pt(fallback=)` 用兜底族真实 hmtx；`test_t33` 锁定「嵌入子集度量 == 兜底族度量」 |
| NEW P2-1 | 去重循环被复制两份（死代码） | 删除第二份 |
| NEW P2-2 | `binding_drift` 计算后被丢弃 | 进 apply 回执 |
| NEW P2-3 | 阻塞的 apply 丢具体错误码 | `_evaluate` 回 `code` + `issues` |

复审明确记录的**过度拒绝风险**（P3，未修，理由见 PR_HANDOFF）：`replace_plan` 回放「加锁之前」的历史（undo）或 `apply_template` 应用含锁组件的旧快照会被锁不变量整笔拒绝。取舍：宁可拒绝并让用户显式解锁，也不静默改锁组件；后续应加 `override_locks=true` 二次确认。

## 复审后仍开放（不计入本任务阻塞）

原始 P1 中未修的部分（均与本任务「纸面作者决定正确性」正交，写入 PR 说明的 Known limitations）：

1. 预览/导出仍用全局 `mapspec.view.bounds`，不消费 frame 级 `bounds`（`service.py:396-404`）——纸张已同源，范围未同源。
2. 图例 `continue` 续排盒几何错误、无跨页（`legend.py:293-299`、`svg_marginalia.py:260,305-310`）。
3. 比例符号未计入 `needed_pt`、画在框外且无标签（`legend.py:234-235`、`svg_marginalia.py:334-346`）。
4. 页级模板仍是全局组件替换（`template.py:121-151`）；`reset_overrides` 不恢复模板前基线。
5. `submit_export` 不跑 `validate_authoring`（文本溢出只降级不阻断）。
6. UI 的 align 仍本地计算、不发 `align` op，基准含锁组件（与 Pi 语义不同，`panel.tsx:195-218`）。

## 合流后复核

待填（PR 前 fetch 最新 master、跑受影响回归）。
