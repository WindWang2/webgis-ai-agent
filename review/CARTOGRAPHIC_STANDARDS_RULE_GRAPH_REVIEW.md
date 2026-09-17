# Review — Cartographic Standards Rule Graph & Deterministic QA Packs

- 分支：`cartography/standards-rulegraph-v1`（worktree `../webgis-wt-carto-rules-v1`）
- 基线：`origin/master @ faa453a8935101378c23eb6694a42c3616d9c670`
- 评审对象 HEAD：评审启动于 `84dc0ee8` 之后的 `a47d00e4`，修复落至本文撰写时的最新 commit
- 评审方式：独立 adversarial review（独立 subagent，全仓只读 + 仓外临时目录可写），交叉验证由主 agent 复核
- 关联文档：`docs/adr/0200-cartographic-standards-rule-graph.md`、`.agent-work/standards-rulegraph-v1/**`

## 验证基线（修复后最终状态）

| 项 | 结果 |
|---|---|
| standards targeted suite（9 文件） | **330 passed**，连续两遍一致 |
| `pytest tests/cartography`（全量，-n 2） | 1807 passed / 2 skipped / **1 failed（master 既有，见下）** |
| ruff（新文件全量） | All checks passed |
| catalog drift（`--check`） | exit 0 |
| `git diff --check origin/master...HEAD` | 通过 |
| footprint 检查 | 716+ 文件全部位于本分支 ownership 面（standards 相关路径 + CHANGELOG/UL/ADR） |
| 跨进程确定性 | 同一 mapspec 在 `PYTHONHASHSEED=0/1/42` 下报告 sha256 一致 |

## 基线同败披露（非本分支引入）

`tests/cartography/test_cartographic_quality_review.py::test_chat_token_events_do_not_trigger_cartographic_review`
在**纯净 origin/master（faa453a8）worktree 上同口径复现失败**（`assert 2 == 1`，
`evaluate_with_evidence` 的评审触发计数为 2）——#1329 hot-path convergence 落地后的
既有问题，与本分支零交集（本分支不触碰 chat/token/harness 任何文件）。

## 评审发现与处置（round 1）

| ID | 严重度 | 发现 | 处置 |
|---|---|---|---|
| P0-1 | P0 | 不可测色带（palette 名不存在→`evaluate_cell` verdict=unavailable×3；raw colors 不可解析→metric None）被判 **satisfied**——伪 CVD/print pass，违反 D4 fail-closed | ✅ 修复（`ef90c797`）：unavailable 计入 unevaluated；violated > not_evaluated > not_applicable > satisfied 结果阶梯；失败层不被不可测层遮蔽。回归：`test_standards_review_fixes_v1.py::TestP0*`（3 条） |
| P1-1 | P1 | `RULE_CONFLICT` 固定 error 级，绕过 profile cap——任何声明冲突边的 pack 可让 inferred（legacy）profile 被 gate 阻断 | ✅ 修复：冲突披露走同一 cap（strict→error / 否则 warning）。回归：`TestP1ConflictSeverityCap`（inferred allow + strict 升级，2 条） |
| P1-2 | P1 | `label_density_declared`：circle/symbol 层 profile 缺 featureCount → not_applicable（静默逃逸） | ✅ 修复：unevaluated 计数 + 同一结果阶梯。回归：`TestP1LabelDensityUnknownCount` |
| P2-1 | P2 | count_vs_rate：typeless `{}` 字段元数据被当 vacuous | ✅ 修复：dict 无 `type` 键 → unevaluated。回归：`TestP2TypelessFieldMeta` |
| P2-2 | P2 | 服务包装下单轴显式（如 `medium="print"`）会把整个 profile 翻成 strict → legacy 图可能被阻断 | ✅ 修复：strictness 只由显式 **purpose** 解锁（profile.py 语义收窄 + ADR 披露）。回归：`TestP2StrictKeysOnPurpose`（2 条） |
| P2-3 | P2 | QUALITY_LOOP_OPERATIONS 镜像与行为探针表无双向锚定（镜像加幽灵操作不会红） | ✅ 修复：探针表提为 `_auto_safe_probes()`，`set(probes)==set(QUALITY_LOOP_OPERATIONS)` 双向断言。回归：`TestP2MirrorProbeAnchor` + contract 测试 |
| P2-4 | P2 | `time: {"enabled": false}` 被视为已披露；时间 token 启发子串误报/漏报未披露 | ✅ 修复：enabled=false 视为未披露（回归 `TestP2TimeEnabledFalse`）；token 边界写入 ADR-0200「后果与边界」 |
| P3-1 | P3 | 图不对称声明（A.requires=B + B.conflicts=A）构建通过 | 记录：运行期双披露语义正确；对称性校验留给 pack 工具链 |
| P3-2 | P3 | `enabled=False` 仍会 resolve pack（未知 pack_id 抛错） | 记录：disabled 报告需 pack fingerprint 标识；保持现状 |
| P3-3 | P3 | 全局 registry 初始化后可继续 register（活单例可变） | 记录：init 竞态已 Lock 防护；冻结接口留给后续版本化决策 |
| P3-4 | P3 | `thematic_profile_declared`（info）与 `_review_profile` 的 raster+legend_spec 推断存在口径差 | 记录：info 级，语义各有所本（结构性 vs 规则 profile 选择） |
| P3-5 | P3 | 部分 not_evaluated 时报告级 status 仍可为 pass（all-or-nothing） | 记录：counters/pi_card 保留诚实明细；状态粒度留给 v1.1 |
| P3-6 | P3 | ProfileSpec strict 由 source 归一；projection 字段未直接被 checker 消费；fingerprint 重复计算 | 记录：无行为影响 |

评审员原判：**NOT-READY（P0×1 P1×2 P2×4 P3×6）**；P0+P1 全部修复并回归后达到
**READY-WITH-P2**（P2 亦已全部修复），P3 全部记录在案。

## 声称核验（评审员独立验证，非自述）

1. 零数学复制：evaluate.py 只 import 既有引擎函数/常量（`required_components_for`、`context_matrix.evaluate_cell`、palettes 原语、`SymbologyConstraints`/`_context_min_delta_e`、`thematic_spec.thematic_field`、`semantic_checks._thematic_color_spec`），raw 回退与 `symbology._context_separable` 阈值逐项一致。
2. 无第二裁决：无任何既有模块 import standards（仅新服务包装单向依赖）；报告为有界投影。
3. fix 路由闭合：构造期校验 + 行为探针 + autofill 词表 ⊆ component registry。
4. 确定性/并发/租约：单例 Lock 防护；证据只含有界 refs，无源 URL/凭据泄漏；投影 O(1) w.r.t. features。
5. footprint：未触碰 #1356 竞争面（mapspec_schema/semantic_checks/render_diagnostics/契约 JSON/lifecycle_engine/coordinator）与 #1335/#1336 热区。

## 与最新 master / open PR 的交叉

- Phase 0 时点 master 即为 faa453a8（无新合入）；8 个 open PR 中仅 #1356 触及
  `app/lib/cartography/**`，其文件面（scene_*、mapspec_schema v1.4、semantic-checks 契约 v2）
  与本分支零交集；rebase 时仅需关注 CHANGELOG.md / UBIQUITOUS_LANGUAGE.md 的追加点。
- 语义相邻：#1352（cartography 评测语料）与 #1351（data_quality semantic checks）——
  命名已避让（Standards* 前缀），PR body 互相引用。
- 是否需要 integration PR：**不需要**；本分支纯 additive，无对既有行为的任何修改
  （除 CHANGELOG/UL 文档追加）。
