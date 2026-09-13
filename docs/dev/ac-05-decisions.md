# ac-05 决策日志（adaptive-cartography/05-auto-labeling）

> 配套 ADR-0154；本文记实现中的取舍与岔路默认（§0.4 的落地证据）。

## D1 — 数据集来源：全合成的拟真 schema（不是真实数据拷贝）

仓库内没有可用的真实数据集（`data/` 仅 exports；`tests/fixtures/runtime/mvt-basic/points.geojson`
是 1 个无属性点集）。按仓内既有约定（`tests/fixtures/gis_samples.py`：license clean、
确定性、可生成、不提交二进制）落地 `tests/fixtures/labeling_datasets.py`：10 个数据集
**schema 拟真**（行政区划/POI/水系/轨交/传感网/地块/世界政区/公交/监测站/密集商贸），
字段值全合成，覆盖中英混排、ID/编码/时间戳/长文本/低基数诱饵谱系。ground truth 判定
（人工）落 `docs/dev/ac-05-label-groundtruth.csv`，判定理由含诱饵谱系说明。

## D2 — `sensors` 数据集：无 name-like 字段是特性不是缺陷

任务 §5 要求「无 name-like 字段时 100% 不标注 + advisory（禁止用 ID 当标注）」。
生成器刻意构造纯 hex id + 时间戳 + 度量字的传感网 schema；`choose_label_field` 在该数据集
上 `field=None + advisory`，且有专项断言（含 `device_id` 的 `code_like_values` 语义排除理由）。
**岔路默认（§0.4）落地**：不用 `device_id`/`station_code` 凑数。

## D3 — countries_mixed 的 tie-break：exact 命中 > 子串命中

`name` 与 `中文名称` 同时在场时，人工判定取 `name`（世界政区的通用惯例）。算法侧对应的
确定性规则：exact 词表命中（1.0）> 子串命中（≤0.9，按子词长加权）。这也是 §0.4
「同分按 name > title > label > 语义类型 > 长度分布」的实现化 —— 前两级由评分吸收，
余下同分按 `_TIEBREAK_ORDER` 词族次序 + 字段名字典序全序化。

## D4 — `rejected` 含 0 分字段

初版 `rejected` 只收正分候选，主键/编码字段（词表未命中直接 0 分）不出现在对账面 ——
任务要求 rejected 是一等工件。改为**全字段评分入对账**：0 分字段带 `id_like_field_name` /
`no_vocab_hit` 等语义理由。这让「为什么没用 OBJECTID 标注」可审计。

## D5 — 抽稀 filter 的双策略（id_list 优先，cutoff 兜底，skipped 诚实）

MapLibre filter 无法做全局排序，Top-N 必须落成 filter：

1. 数据带稳定 id 键（`__fid/id/fid/OBJECTID/osm_id/station_id`，扫描前 50 要素 ≥90% 非空）
   → `["in", ["get", idKey], literal(ids)]`（精确集合）；
2. 无 id 键但优先级字段数值且去重值数 > limit → 数值 cutoff（同值并列会超选，如实记 kept）；
3. 否则 skipped —— 不伪造抽稀，MapLibre 碰撞器兜底，reason 进 evidence。

不选的方案：运行时改写 source 数据注入 `__fid`（触碰 renderer 领地 + 大 FC 拷贝成本）；
symbol-sort-key 单独使用（只定次序不减量，>2000 点时避让仍不足）。

## D6 — 降级阶梯的选级信号：墨量比（与后端 collision_est 同口径）

不读 MapLibre 逐帧碰撞结果（不可复现、依赖渲染时序），而是在挂层时用与
`semantic_checks._check_label_collision` 完全同口径的墨量估计（CJK 1.0em/0.6em、
1024×768 视口）确定性选级。四级：L1 缩字号 ×0.85 → L2 去晕圈 → L3 抽稀加密（topN×0.5）
→ L4 关闭该层标注。每级写 `getLabelEvents()` evidence（环形 200 条）。

## D7 — 换字段不重建图层：runtime label-only 快路径

reconciler 的 diff（06 线领地，禁改）会把 label-only 变更报为 recompile。快路径做在
runtime 应用侧（本线领地）：`labelOnlyLayerChange(prev, next)` 规范化比较（剥掉 label 键后
逐位相等 + label 声明确有变化）→ 仅幂等替换 `${id}-label` 子层，主层零 remove/add。
vitest 以 removeLayer/addLayer 调用计数断言（门禁「运行时事件计数」）。同步与 debounced
两条应用路径、z-order 门（label-only 不算结构变化）三处对齐。

## D8 — 字号只给比例（06 线接口）

`text-size = (label.size ?? layout.labelSize ?? 12) × strategy.sizeRatio × 降级因子 ×
band.sizeRatio(step)`。缺省 12 是**现状缺省**（#1007 契约），06 线符号律落地后由其给出
绝对基准；本线在任何岔路都不引入新的绝对字号决定。

## D9 — collision_est 的策略消费只动标注检查段

`semantic_checks.py` 是共享面：本线只扩 `_check_label_collision` 及其两个私有助手
（`_label_band_top_ratio`），行为兼容性由既有 phase4 规则测试（13 条）锁定。检查域扩展
（`label{field}` 声明让非 symbol 主层进入检查）是策略生效的必要面 —— 否则新 producer
路径的标注对 QA 不可见。

## D10 — label_layer 的渲染支持矩阵如实留空

`component_renderers._SUPPORT_MATRIX` 反向覆盖检查要求每个 ComponentType 有条目。
label_layer 的条目 `renderers=[]/exporters=[]`：标注由 MapSpec layer.label 子层渲染
（runtime/编译器/SVG 导出三条既有路径），组件本体是绑定与决策面 —— 不假装有 chrome
渲染器（与该文件「支持矩阵只描述已实现」的纪律一致）。

## D11 — compiler 的唯一改动：raster/heatmap parity

无头编译器对 raster/heatmap 也生成 label 子层、活运行时排除（勘察 F3 分歧）。本线把
编译器对齐到运行时语义（同排除）。编译器的策略编排不复刻：headless 侧没有要素数据，
抽稀/降级是数据依赖决策，导出域已有 ADR-0126 确定性求解器承担。

## D12 — pytest-xdist

门禁命令带 `-n 2`，`requirements-dev.txt` 未含 pytest-xdist —— 本地 venv 补装
（不进 requirements 提交；无 CI，门禁即本机）。
