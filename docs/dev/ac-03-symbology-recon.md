# AC-03 自适应符号化引擎 · P0 勘察报告

日期：2026-09-13　分支：`adaptive-cartography/03-adaptive-symbology`　基线：`09d839d3`
配套原始数据：`docs/dev/ac-03-hardcode-ledger.csv`

## 1. 硬编码销项表（概览；全表见 CSV）

任务书 §1 所列 5 处接线点全部确认存在，另有勘察新发现的次级站点。分级：

### A 级（本线主销项，5 个入口）

| 入口 | 位置 | 现状 |
|---|---|---|
| `CartographyService.build_thematic_style` | `cartography_service.py:40-42` | 签名默认 quantiles/5/YlOrRd；被 `templates.py:574` 以 payload 硬编码喂入 |
| `create_thematic_map` | `cartography.py:176` | method 已接 `choose_classification`（ADR-0073），但 palette/k 裁决、离群值、上下文缺位 |
| `h3_binning` | `advanced_spatial.py:2636` | **完全硬编码** quantiles/5/YlOrRd，无任何裁决 |
| `heatmap_data` | `spatial.py:121,286` | palette 默认 classic（热力族独立色带表）；radius 契约已有单点默认 |
| `apply_template` | `templates.py:574-576,616-623` | payload.get 兜底 quantiles/5/YlOrRd；62+ SEED 模板 payload 全部写死 |

### B 级（次级站点，同线收口）

- `app/lib/cartography/thematic_spec.py:145-147`：canonical builder 的签名默认（quantiles/5/YlOrRd）——升为「method 缺省 → 裁决」语义。
- `app/services/mapspec/composite_builder.py:138-143,332,360-363`：组合模板 ThematicSlot 兜底默认 + 无数据合成 spec。
- `app/schemas/map_component_slots.py:38-45`：slot schema 默认值。
- `app/tools/cartography.py:296-333`：`create_3d_extrusion_map` 的 Oranges/5/natural_breaks。
- `app/schemas/template_schema.py:62-64`：`ThematicChoroplethPayload` schema 默认。

### C 级（允许例外，附理由逐条登记 CSV）

- `classify.py:144`：分类引擎自身的默认值常量（引擎语义，不是入口裁决）。
- `heatmap_contract.py:34`：`DEFAULT_RADIUS_PX` 命名常量（独立契约模块）。
- LISA 语义五色（`cartography_service.py:96`、`composite_builder.py:413`）：LISA 是语义分类（HH/LL/HL/LH/NS 制图学固定用色），不是数值色带裁决对象。
- raster 侧（`raster_cartography_converter.py` 的 Viridis/Gray/Oranges、`spatial_tasks.py:487`、`rs/spectral_engine.py:93`）：栅格 PNG 渲染族/山体阴影必须灰度/光谱指数连续带，均有明确制图学语义；登记为命名常量文档化，不强行接裁决（接了反而引入错误抽象）。
- **禁改文件** `app/services/gis_harness/tools.py:63,590,802`（classic）：01/02 线领地，本线只登记不迁移。
- 热力图 4 色带族（classic/magma/viridis/thermal，透明首停靠点）：独立渲染范式（MapLibre heatmap），不是分级色带；palette 作为显式偏好透传。

## 2. 分类法与色带使用分布

5 类分类法（classify.py）：quantiles / equal_interval / natural_breaks / std_dev / head_tail。

| 分类法 | 生产入口实际到达路径 | 结论 |
|---|---|---|
| quantiles | 滥用：模板 payload 6 处 + h3_binning + 全部签名默认 | 被滥用（历史默认） |
| natural_breaks | create_thematic_map 裁决默认 + 模板 12 处 | 健康 |
| equal_interval | 仅模板 2 处显式 | 低用 |
| std_dev | **生产零到达**（仅元数据推荐） | 死方法 |
| head_tail | 仅 create_thematic_map 裁决可达（重尾） | 低知（本线扩大到达面） |

18 条通用色带（COLOR_PALETTES）字面使用统计（业务代码，不含 palettes.py/model_library/模板 payload）：

- **被滥用**：YlOrRd ×12 站点；classic ×5（热力族）。
- **低用**：Viridis ×3（栅格族）、Gray ×4（山体阴影）、Oranges ×3。
- **代码零使用**（仅模板 payload 与元数据可达）：Greens、Reds、Dark2、Magma、Inferno、PuOr。
- RdYlGn（红绿色盲不友好）出现在模板 payload 中但无 CVD 防护 —— 本线 CVD 裁决的直接动机。

## 3. SEED 模板 payload 盘点

实测 **62** 个 SEED 模板（与任务书一致；`SEED_TEMPLATES` 列表长度为准），kind=thematic 28 个：
27 个 choropleth variant 全部写死 method/k/palette，1 个 heatmap variant（intensity/radius/heatPalette hex）。

- method 分布：natural_breaks 12 · quantiles 6 · categorical 6 · equal_interval 2 · lisa 1
- k 分布：5×14 · 6×6 · 7×3 · 4×2 · 8×1 · 10×1（k=10 超出裁决边界 [3,7]）
- palette 分布：YlOrRd 7 · RdBu 6 · Blues 3 · Greens 2 · Set1 2 · Set2 2 · Viridis/Plasma/Purples/RdYlGn/Pastel1 各 1

结论：模板 = 「表述意图的艺术偏好」，但被 apply_template 当成**命令**直接执行——RdYlGn 在 CVD 下不可辨、k=10 越界、quantiles 用在重尾字段照错不误。P5 改造方向：payload 键位保持（兼容），语义降为 `recommended`（尊重显式偏好、仍受边界与可分辨性校正）。

## 4. 同数据多入口一致性基线（改造前）

用 6 个标准数据集跑「理论上应产出同一符号化决策」的入口对，改造前实测（method/k/palette 三元组）：

| 数据集形态 | create_thematic_map（裁决） | h3_binning | apply_template(choro 模板) | build_thematic_style 直调 |
|---|---|---|---|---|
| 重尾计数 | head_tail/k=5/YlOrRd | quantiles/5/YlOrRd | 模板写死（如 quantiles/6/RdBu） | quantiles/5/YlOrRd |
| 近均匀 | equal_interval 或 quantiles | quantiles/5/YlOrRd | 模板写死 | quantiles/5/YlOrRd |
| 中等偏态 | natural_breaks/k=5/YlOrRd | quantiles/5/YlOrRd | 模板写死 | quantiles/5/YlOrRd |
| 常量字段 | natural_breaks + 单类归一 | quantiles 单断点 | 模板写死 | quantiles 单断点 |
| 离群重尾(p99=100×中位) | head_tail | quantiles（色带被拉爆） | 模板写死 | quantiles（色带被拉爆） |
| n<8 小样本 | natural_breaks（无 low_confidence 标记） | quantiles/5 | 模板写死 | quantiles/5 |

同一数据四个入口四种答案——本线以 `resolve_symbology` 唯一裁决收口（P7 以 6×5 一致性回归锁定）。

## 5. 引擎现状确认（§0.2-6）

- `visualization_plan.py:91 choose_classification` ✅ 可用：显式尊重 → 重尾 head_tail → 近均匀 → natural_breaks；k 夹 [3,7]；rejected/authority 完整。**复用不重写**。
- `classify.py` ✅ 可用：5 算法纯函数，Jenks 前缀和 O(n²k) + 1000 采样上限（seed=42 确定性）。
- `palettes.py` ✅ CIEDE2000 / min_adjacent_delta_e / perceptual_ramp / WCAG 就绪；**缺**：CVD 模拟、print 变换、上下文裁决。
- `thematic_spec.py` ✅ resolve_thematic_colors 单点解析、builders、normalize_legend_spec（v1 兼容路径）；**缺**：v2 字段（unit 已有/其余待加）。
- `themes.py`：3 主题已有 print/暗底 palette 推荐知识，可作裁决知识源。
- `model_library.py`：PALETTE_KINDS 有 colorblind_safe 标记（18 条全部登记）；default_k 元数据无生产消费者。

## 6. P0 勘察结论 → 设计输入

1. 裁决引擎入口签名定为 `resolve_symbology(profile, intent, constraints)`，`SymbologyDecision` 为一等 pydantic 工件（P1）。
2. k 裁决新增「色带可分辨上限」维度：由 min_adjacent_delta_e 反推（P2）。
3. 色带裁决按 PALETTE_KINDS 语义族 × PaletteContext 六上下文（screen/projector/print/cvd_deuteranopia/cvd_protanopia/cvd_tritanopia）× 底图亮度（P3），CVD 模拟用简化 Brettel/Viénot 变换落在 palettes.py。
4. 离群值 clip_policy 四态 + legend 强制披露（P4/P6）。
5. 接线顺序：thematic_spec → cartography_service → cartography(2 工具) → advanced_spatial → spatial → templates → composite_builder，每接一个跑对应测试（P5）。
6. legend_spec v2 = 纯加字段（ADR-0078 先例）：`unit/nodata_label/out_of_range_label/method/k/palette_id/clip_policy/why`（P6）。
