# G41 独立复审记录（REVIEW）

流程：实现与自测完成后，交**未参与实现**的独立 reviewer 只读复审（第 3 个 subagent 槽位），
按 P0（阻塞）/P1（须修）/P2（建议）分级。P0/P1 **全部修复后**才进入提交。
本文件记录 reviewer 结论、修复映射与复验证据；不记录美化结论。

## 第 1 轮：reviewer 结论

结论：**不可合并**，6 项 P0/P1。reviewer 逐条给出可复现路径与错误期望，均落在
「用户看到的图与图例声明不一致」或「合同字段声明了但没生效」两类真实缺陷上。

| # | 级别 | 缺陷 | 为什么是阻塞 |
|---|---|---|---|
| 1 | P0 | `flip_rows` 只反转图例 `cells` 的屏幕行序，主循环里 `joint`/`colors`/`class_index` 没跟着换，同时 `row.direction`/`arrow` 照旧输出 `high_to_low`/`up` | 开关打开后**图面不变、箭头却改了**：读者被告知「低值在顶部」而实际渲染仍是高值在顶。这是对读者的错误陈述，不是排版瑕疵 |
| 2 | P0 | `revert` 提交后把 `data_binding` 恢复成**基准源**（提交时的源），不是本域作者决定**之前**的源 | 撤销一步反而把用户先前选择的第二数据源丢了；"可撤销"语义不成立 |
| 3 | P1 | 可靠性分桶的区间判定仍用固定规则，`boundary_rule` 只影响双变量轴 | 同一层两个通道对「恰好等于断点」的值给出不同归属；图例与 paint 对不上 |
| 4 | P1 | `allow_noncentered` 出现在合同里但无人读取（死字段） | 声明了来源模型合同约束却不执行，等于给用户假保证 |
| 5 | P1 | 服务端 SVG 图例把长字段名直接裁到固定宽度，无任何披露 | 静默截断：读者看不到被截掉的量测含义；与「不伪造、不隐藏」冲突 |
| 6 | P1 | `clip=clamp` 时越界误差被并入相邻桶，`out_of_domain` 桶永远为空 | 图例宣称有「越界」类目但恒为 0，等于把越界样本伪装成正常可靠 |

## 修复映射（逐条闭环）

| # | 修复 | 代码位置 | 复验 |
|---|---|---|---|
| 1 | `flip_rows` 改为**纯呈现方向**：主循环产出规范行序（低 B → 高 B），`cells` 按 `screen_row` 反向映射到同一 `class_index`，`direction`/`arrow` 由实际屏幕序推导（flip → `low_to_high`/`down`），`flip_rows` 与 `class_index`/paint 语义解耦 | `classify.py:211-273`、`row_col_meta` `:315-324`；`legend.py:82` | oracle `test_flip_rows_reverses_cells_colors_and_arrow`（非对称 3×3：屏幕首行=低 B、箭头 `down`、每格 `class_index` 与规范阵一致）+ 前端 `g41-canonical-legend.test.ts` 屏幕序断言 |
| 2 | 证据块新增 `previous_source_ref` / `previous_paint` / `previous_data_binding` 整层快照；`revert` 从该快照原子恢复 `source` carrier + `paint` + `legend_spec` + `data_binding`，并显式中性 `opacity=1.0` 阻断 durable-presentation 继承 | `service.py:431-460`、`:622-628`；`expression.py` 快照写入 | `test_b30_revert_cleans_stale_bivariate_and_second_channel`（提交 → 改源 → 提交 → revert，回到**上一来源**而非 base） |
| 3 | 可靠性分桶改用与双变量同一 `assign_axis(value, breaks, boundary_rule)`，区间表由 `axis_intervals(..., spec.boundary_rule)` 生成 | `reliability.py:192`、`:26` | `test_reliability_bucket_mapping_hand_computed`（手算，断点值归属显式）+ `test_boundary_rule_hand_computed` 双通道一致 |
| 4 | `allow_noncentered=False` 真正生效：非对称/不以估计值为中心的区间判 `invalid` (`interval_not_centered`)，并进入 `invalid` 桶与披露；`True` 时保留 `noncentered` 披露 | `stats.py:251-282`；回执 `reliability.py:341` | `test_b20_ci_pi_distinct_and_level_in_legend`、`test_noncentered_interval_policy_is_enforced` |
| 5 | 服务端 SVG 保留完整文本于 `data-g41-*-full` 与 `<title>`，视觉宽度不足时**显式披露**（`short()` 只作用于可见字形，不丢数据），并由 publication 测试断言全文可回读 | `frontend/lib/map-kit/vector-svg-export.ts:142-200` | `test_g41_publication_readback.py` 长字段名用例（服务端渲染 → 回读全文） |
| 6 | `clip=clamp` 时越界样本进入独立 `out_of_domain` 桶并计数披露；`clip=reject` 时 typed 拒绝并给出可行域 | `reliability.py:280-312` | `test_clamped_out_of_domain_is_its_own_bucket`、`test_clip_reject_is_typed` |

## 修复后复验（本轮实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端单测 | `pytest tests/unit/bivariate_reliability -q` | **72 passed**（较修复前 70，新增 2 条针对 #1/#6 的回归） |
| 出版回读 | `pytest tests/integration/test_g41_publication_readback.py -q` | **5 passed**（较修复前 4，新增长名全文披露用例） |
| 真实 Pi 旅程 | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g41_bivariate_reliability_journey.py -q` | 见 TEST_MATRIX T10（18/18 gates） |
| 前端专项 | `vitest bivariate-reliability-panel.test.tsx g41-canonical-legend.test.ts` | **11 passed** |
| 前端回归 | `vitest --maxWorkers=2`（14 文件） | **139 passed**（见 TEST_MATRIX T9） |
| 类型 | `tsc --noEmit` | 新代码 0 错误；仅 2 处既有 `cartography-result-card.tsx` |

### 复验中自查发现的额外回归（第 1 轮 reviewer 未覆盖）

`components/hud/layer-style-panel.audit840.test.tsx` 用**不完整** `vi.mock` 替换
`@/lib/mapspec/session-cursor`（只给 `getCommittedMapSpec`/`subscribeMapSpecLive`）。
新面板挂到 fill/circle/line 后读取 `getMapSpecSessionCursor`，该用例直接抛
`No "getMapSpecSessionCursor" export is defined on the mock`，2 个 #840 门控用例失败。
修复：在该测试的 mock 里补 `getMapSpecSessionCursor`（返回 `sessionId: undefined` → 子面板
idle，不向不存在的会话发请求），**断言与门控逻辑未改动**；7 passed。
这类「mock 完整性」回归说明：新增子组件的读取面必须与既有测试替身同步，已记入 PR 说明。

## 未采纳 / 保留意见

- reviewer 建议把 `__g41_pair_class` 改成可直接被旧渲染器读的 `__biv_class`：**不采纳**。旧图零改写是硬约束（B04），复用同名会污染既有层语义；改为独立命名 + 血缘记录，图例显式声明派生字段名。
- reviewer 指出「第三通道（两主变量 + 不确定度）未支持」：**如实保留为披露**，不做四维视觉编码；写入图例 `disclosures`，不让读者误以为三角编码已表达不确定性。
- reviewer 指出真实浏览器像素对比缺失：**保留为未测**，不以外观推断代替（见 TEST_MATRIX「未测」）。

## 第 2 轮：合流后复审

提交后请**同一 reviewer** 对 `git diff base..HEAD` 的关键变化（尤其 `classify.py` 行映射、
`service.py` 快照/恢复、`reliability.py` 分桶）做第二轮只读复审，结论追加在本节。
第 2 轮不得由实现者自评代替。
