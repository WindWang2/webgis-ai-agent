# ADR-0154: 自动标注引擎 —— 标注字段自动挑选、碰撞感知放置、缩放分级密度与可寻址 label_layer（Adaptive Cartography 05 线）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/05-auto-labeling（ac-05）
- 关联: ADR-0118（label 文本适配契约，导出域）、ADR-0120（Typed MapSpec V6）、ADR-0126（Label Engine V6，导出域确定性碰撞）、06 线符号律（字号绝对基准）、07 线版面编排（label_layer 的组合消费方）

## 1. 背景

专题图没有标注就不像专业图，但本仓标注能力几乎全靠人工（P0 勘察 `docs/dev/ac-05-label-recon.md`）：

1. `component_taxonomy` 定义了 `content.label_layer`（分类），但 `component_registry` 无对应
   descriptor —— 标注不可寻址、不可局部突变、不可被版面系统摆放；
2. 活运行时只在 spec **显式**给 `layer.label{field}` 或 `layout.labelField` 时生成 symbol 子层，
   而后端**没有任何 producer** —— 不给就静默无标注（失败模式 F1/F2）；
3. `chat/context/formatters.py` 的 `label_keys` 只服务 LLM 上下文，从不进制图决策；
4. 无自研避让/抽稀/优先级：唯一手段是 MapLibre 内置 `text-allow-overlap:false`；
   `carto.label.collision_est` 告警后只能人工缩字号（基线：密度网格 30 格扫描告警率 **83%**）；
5. 无缩放分级：低 zoom 与高 zoom 显示同样密度的文字。

既有 `label_engine.py` / `label_collision.py` / `label-solver.ts`（ADR-0118/0126）全部是
**导出/出版物域**的确定性求解器 —— 交互运行时的标注布局决策此前是空白，本 ADR 补的正是这一块，
不动导出孪生（byte-stable 契约维持）。

## 2. 决策一：标注字段自动挑选（P1，`app/lib/cartography/label_plan.py`）

- `choose_label_field(profile, intent) -> LabelFieldChoice{field, confidence, reasons[],
  rejected[], advisory}`：纯函数、无 IO、无随机，同输入两次求解 `model_dump()` 相等（golden 锁定）。
- 评分合成：name-like 词表（exact 1.0；子串按子词长加权，多语言含 `名称/name/title/label/类型/类别`）
  → 语义排除（主键/编码/时间戳/几何字段名 + 样本级编码/时间戳/纯数值检测）→ 字段基数比
  （唯一值/总数，三段梯度惩罚）→ 长度分布 → 空值率。
- 同分 tie-break 按 §0.4 固定次序：exact 词表命中 > title/label 族 > 语义类型 > 长度分布，
  再按字段名字典序全序化（可复现）。
- **无合格字段 → `field=None` + advisory（100% 不标注，禁止用 ID 当标注）**——`sensors`
  数据集（纯 hex id/时间戳）在 10 数据集 ground truth 中锁定该契约。
- `rejected` 是一等对账面：**0 分字段也带语义理由**（`id_like_field_name` /
  `timestamp_like_values` / `no_vocab_hit`……），决策可解释可审计。
- 宽度口径与 `semantic_checks`/`label_engine` 同源（CJK 1.0em / 其他 0.6em）。

## 3. 决策二：标注策略自动编排（P2，同模块）

- `plan_label_strategy(profile, view) -> LabelStrategy{mode, top_n, priority_field,
  priority_source, zoom_bands[], size_ratio, reasons[]}`。
- 密度分档（确定性三档）：`n ≤ 2000 → all`（zoom 档饱和为全量）；`2000 < n ≤ 20000 → top_n`
  （确定性档位：≤8000 → 400，其余 → 250）；`n > 20000 → hover_only`（无常驻标注）。
- `priority_field`：重要度词表（population/gdp/面积/stars/scalerank/…）按序扫描数值字段；
  无命中时取名字典序首个数值字段；面要素无数值字段 → `area_proxy`（运行时几何面积代理）。
- `zoom_bands`：默认 4 档（`[0,8)→top 10%`、`[8,11)→25%`、`[11,14)→60%`、`[14,24)→100%`），
  档内 `sizeRatio` 为字号比例系数。**绝对字号基准归 06 线符号律**（现状 `label.size ?? 12`），
  本线只产出乘数。
- `build_label_spec(profile, …)` 把 P1+P2 决策组装成 MapSpec `layer.label` dict
  （camelCase，`mapspec_schema.MapSpecLayerLabel` 的 additive 扩展字段），是后端唯一的
  spec 写入面；前端 `label-layout.ts` 是该形状的唯一交互侧消费者。
  **诚实披露（pre-merge review 修正）**："`label_layer` 组件 → `layer.label`"
  的写入桥**本线未实现**——组件面（descriptor / 工厂冻结 `options.label` /
  rebind）与 spec 产物面各自就位，但尚无消费方读组件 `options.label` 去写
  绑定图层的 `layer.label`；该接线随 layout/compose 线落地。当前 spec 侧
  `layer.label` 仍经既有显式写入面（`layer.label{field}` / `layout.labelField`）
  到达前端运行时。

## 4. 决策三：交互运行时的避让/抽稀/分级/降级（P3–P5，`frontend/lib/mapspec-runtime/label-layout.ts`）

新模块是纯函数层（确定性契约同上），runtime 的标注子层路径是唯一接线点：

1. **全局优先级**：`symbol-sort-key` 表达式（`* -1 (to-number (get priorityField))`）——
   MapLibre 内置碰撞贪心按 sort-key 升序放置，重要要素先占位；
2. **确定性抽稀**：`selectTopLabels` 按优先级降序（stable tie = 数据序）取 Top-N，生成
   label 子层 filter —— 数据带可用 id 键（`__fid/id/fid/OBJECTID/osm_id`，≥90% 非空）→
   `["in", ["get", idKey], literal(ids)]` 精确集合；无 id 键但优先级数值离散度足够 →
   数值 cutoff（`[">=", …]`，同值并列超选如实入 evidence）；否则 skipped（宁可让碰撞器吸收）。
   无法安全抽稀时不猜 —— reason 落 evidence；
3. **zoom 分级**：`zoomBands` 声明档位 → 字号 `step(["zoom"], …)` 表达式（P4）+ 跨档
   `zoomend` 时按档内 `topRatio` 重算抽稀 filter（有界工作集，同档幂等）；
4. **降级阶梯（§0.4 四级，`resolveDegrade`）**：注记墨量比（与后端 collision_est 同口径）
   超阈值逐级 —— L1 缩字号(×0.85) → L2 去晕圈 → L3 加密抽稀(topN×0.5) → L4 关闭该层标注；
   每级写 runtime evidence（`getLabelEvents()`，环形 cap 200）；
5. **样式自适应（P5）**：`haloMode:"auto"` 时按底图背景亮度反转配色（深底浅字深晕、浅底维持
   #1007 黑字白晕契约；显式 `color/haloColor` 恒胜）；CJK 样本收窄 `text-max-width`、晕宽
   +0.25 —— **不动字号**（绝对基准归 06 线）；
6. **label-only 快路径**：`labelOnlyLayerChange(prev, next)` 检测 recompile 只动了标注声明
   → 仅幂等替换 `${id}-label` 子层，**主层零 remove/add**（换标注字段 = 局部突变，运行时
   事件计数契约有 vitest 断言）；主层 filter 变化与抽稀 filter 取 AND 合成；
7. **编译器 parity**：headless 编译器同样排除 raster/heatmap 的 label 子层（消除 F3 分歧 ——
   此前编译器生成、运行时排除，同 spec 屏幕与导出漂移）。

## 5. 决策四：`label_layer` 组件可寻址（P6）

- `component_registry._SEED_DESCRIPTORS` 正式注册 `label_layer`（category
  `content.label_layer`，`placement_domain="layer"`，`cardinality="multiple"`，
  `requires_layer_binding=True`）；渲染/导出支持矩阵**如实留空**（`component_renderers.py`
  反向覆盖检查要求每个 ComponentType 有条目）——标注经 MapSpec layer.label 子层渲染，
  组件本体是**绑定与决策面**，不进 chrome 注册表；
- `components.py`：`ComponentType` 增 `label_layer`；`label_layer_component()` 工厂
  （profile 在场 → `build_label_spec` 自动冻结字段+策略进 options；无候选 → `auto:false`
  诚实空绑定 + advisory）；`_REBIND_FIELDS["label_layer"] = ("field", "layerId")` ——
  **换标注字段 = rebind 局部突变**，不重建图层；`_FACTORY_BY_TYPE` 登记（upsert 可达）；
- 07 线的 `component_composer` 可直接按 descriptor 消费（版面编排接口即标准组件面）。

## 6. 决策五：语义检查消费策略（P7 的量化面）

`_check_label_collision`（仅标注检查段）扩展：

- spec 声明 `label` 策略时按策略修正估计：`top_n` 把有效可见注记数钳到
  `min(featureCount×coverage, topN×档内topRatio×coverage)`；`hover_only` 记 0；
- `label{field}` 声明本身让非 symbol 主层进入检查域（策略声明即标注存在）；
- evidence 增加 `label_strategy` 块（declared/mode/top_n/band_top_ratio/effective_labels）。

效果（`scripts/label_quality_report.py` 取证，同 30 格基线网格）：告警率 **83% → 50%**
（相对降 40%）。剩余告警是诚实告警 —— 极端格子（8000 要素×8 字 CJK）即便 400 条常驻
标注仍超视口承载，由运行时降级阶梯继续收敛，检查不粉饰。

## 7. 后备与代价

- MapSpec 契约为 **additive optional** 扩展（mode/topN/priorityField/zoomBands/sizeRatio/
  haloMode 全可缺省）——旧 spec 行为逐位不变（`text-allow-overlap:false` + #1007 缺省样式的
  层完全不走新路径）；`ts_projection` 再生成 `types.generated.ts`；
- 抽稀依赖源数据形状（id 键/数值优先级）：不可安全抽稀时 skipped + evidence，不伪造精确性；
- zoom 跨档再抽稀挂 `zoomend`（非逐帧），跨档瞬间的密度由 MapLibre 碰撞器兜底；
- 与 06 线契约：字号只给比例系数（`sizeRatio`×band `sizeRatio`×降级因子），绝对基准与
  zoom 插值基准由 06 线定义后替换缺省 12；密度信号（`DENSE_FEATURE_COUNT` 等阈值）
  单份实现于 `label_plan.py`，06 线若落地 `density_signal()` 则改为 import；
- 与 03 线契约：`label_plan` 不触碰 `symbology/visualization_plan/thematic_spec` ——
  03 线的符号化决策产物可直接叠加 `layer.label`（正交通道）。

## 8. Consequences

- 正面：无标注→自动标注（含诚实拒绝）；密集层标注重叠率墨量降 **91%**（hotels_dense
  3200 点，`label_quality_report.py` 表 2）；字段挑选 10/10（ground truth ≥90% 门禁）；
  collision_est 告警率 83%→50% 且可继续收敛；标注成为可寻址组件（换字段不重建图层）；
- 代价/限制：策略字段进入 schema 面（additive，需双语 schema 同步维护）；抽稀 filter 与
  主层 filter 的合成在 label 子层域发生（审计需读 runtime evidence）；导出域孪生不改
  （导出确定性由 ADR-0126 链承担，本 ADR 不重复）；
- 测试面：`tests/cartography/test_label_plan.py`（22）、`test_label_strategy_semantic_check.py`（6）、
  `frontend label-layout.test.ts`（30）+ `runtime.label.test.ts`（10）。
