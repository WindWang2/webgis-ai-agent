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

提交后由**同一独立 reviewer** 对 `git diff base..HEAD` 做第二轮只读复审（P0 = 0）。

### 第 2 轮 P1（全部已修）

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| R2-1 | 可靠性披露**按要素重复追加**同一句文案，随 `legend_spec` 持久化进 MapSpec | 500 要素 → 503 条同文本；25 万要素单次提交约 15MB 重复文本，并拖慢 `expression_fingerprint` | 改为 `ReliabilityScan.noncentered_measure` **计数一次**；`legend.py` 新增 `_dedupe()`，两份 canonical 的 `disclosures` 统一去重 |
| R2-2 | `revert` 预览把 `previous_legend_spec is None` 当「无快照」拒绝，提交侧却支持「恢复为无图例」 | 层在作者化前没有图例时，撤销**永远无法提交**（预览拿不到 `candidate_digest`） | 预览改以 `previous_source_ref` 是否为合法 `ref:` 判定快照存在；`restores` 增加 `had_legend` |
| R2-3 | 图例组件聚合多层时只取**第一份** canonical，并把所有扁平条目置空 | 同图其它层标签一条不画，却仍报 `entries_rendered == entries_total` 且 `complete=True` —— 完整性门禁被架空（出版件静默丢数据） | `read_legend_content` 返回 `canonicals` 列表、canonical 层不再产生扁平条目；绘制器逐份绘制并**照常绘制每条扁平条目**；布局按全部 canonical 占位 |
| R2-4 | 图例框高把「不会被绘制的扁平条目」和 canonical 额外高度**双重预留** | 120×80mm 页框下框高 116.8mm，超出页框约 36mm，压到地图槽/安全区 | 占位高度改为与绘制器同口径的 `7/8 × row_units + 半行`，扁平条目只按非 canonical 层计；新增 `canonical_height_mm` 并删除 `canonical_extra_mm` |
| R2-5 | 面板 Apply 成功后 `busy` 永不复位（`await reload()` 推进 epoch，finally 的 epoch 相等判定恒假） | 提交一次后预览/应用按钮**永久禁用**，只能重挂面板 | 提交是独占操作，finally 无条件 `setBusy(false)`；新增「Apply 后仍可继续预览并再次应用」用例 |

### 第 2 轮 P2（部分修复，其余如实保留）

已修：

- **R2-P2-2** 越界与缺失共用同一透明度：合同新增可选 `visual.out_of_domain_output`；未声明时复用 `unknown_output` 并**显式披露**「图面不可分辨、仅计数/图例分列」。
- **R2-P2-3** 提交无条件写 `opacity: 1.0` 会吃掉用户持久透明度 0.4：改为只在既有值是表达式/缺失时写中性值；`revert` 同规则（不再凭空加 1.0）。
- **R2-P2-4** `flip_rows` 披露文案仍说「paint 类别同步反转」：改为与实现一致的「仅屏幕行序与箭头反转，类别索引/着色/计数按规范行序不变」。
- **R2-P2-7** 旧版（无 `cells`）bivariate 在 live 图例出现 `n=-1` 与 `data-g41-*` 属性：旧图分支不再输出任何 G41 属性，保持逐字节兼容。
- **R2-P2-8** 「三格式回读」只验容器：可靠性地图面改用**字符串桶键 + opacity match**（原实现把桶输出当颜色，产出非法 `fill="0.9"`，图面既无颜色也无透明度却仍通过）；新增 `fill-opacity` 值与 `fill="#225588"` 断言，并新增混合图层「矩阵 + 每条条目都画」用例。

未修，如实保留为已知边界：

- **R2-P2-1** `missing_color`/`nodata` 在双变量 `paint` 里没有消费者（缺失要素不绘制）。改动 `_bivariate_to_match` 的默认会波及既有 bivariate 图（B04 零改写），不在本 PR 扩面；图例已声明 `missing.policy`，记录为后续 G41b 项。
- **R2-P2-5** 标准误图例无条件断言「与估计值同量纲」，未校验 `estimate_unit` 与 `measure.unit` 一致性。属合同语义扩展（需要新的单位兼容规则），记录为后续项。
- **R2-P2-6** `empty=true` 的坍缩格子仍用真实色阵色绘制（图面看起来像完整 n×n）。视觉方案需与制图口径一起定，记录为后续项。
- **R2-P2-9** `revert` 的 `paint` 仍可能比「作者前」多一个中性键（仅在原值缺失且当前是表达式时），且派生展示 ref 无回收策略。前者是阻断 durable-presentation 继承的必要手段，已在代码注释说明；后者涉及 ref 生命周期，记录为后续项。

### 第 2 轮复验

| 项 | 结果 |
|---|---|
| 后端单测 | **73 passed**（新增 revert-无图例用例） |
| 出版回读 | **6 passed**（新增混合图层完整性用例，可靠性地图面断言真实透明度） |
| 真实 Pi 旅程 | **18/18 gates**（修复后重跑） |
| 前端 G41 + 回归 | 14 文件 **140 passed**；`tsc` 新代码 0 错误 |
