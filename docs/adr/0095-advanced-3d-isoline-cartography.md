# ADR-0095: Advanced 2.5D / 3D & Isoline Cartographic Runtime V1

- 状态: Accepted
- 日期: 2026-09-03
- 关联: ADR-0015 (Legend Spec), ADR-0036 (MapSpec Layer Model), ADR-0078 (Unified Thematic Style), ADR-0088 (Cartographic Component Library V2), ADR-0092 (Reproducible Professional GIS Runtime)

## Context

WebGIS AI Agent 当前在地图模型库（`model_library.py`）中登记了主流地理可视化模型，但其中两个核心模型长期处于 `runtime_status = "planned"` 状态：
1. `extrusion_3d`（3D 挤出柱状图 / 2.5D 多边形立体渲染）
2. `isoline_contour`（等值线 / 连续表面等值面矢量切片）

同时，前端与后端的现有原型存在以下方法论与工程缺陷：
- **无显式高度量化通道**：前端依赖全局 HUD 的 `is3D` 布尔值，粗暴地把全部多边形加上 `fill-extrusion-height = coalesce(height, 20)`，把 UI 视效混淆为 GIS 制图模型，缺乏指标字段选择、无量纲高度缩放、极值保护与图例表达。
- **等值线制图割裂**：虽然底层存在 `kde_contours`（基于 matplotlib 的等值面多边形），但缺乏完整的 MapModel 规范、等值线/面双形态支持、显式等值级值保留与文本标注（Contour Labels）。
- **导出与地图快照断层**：3D 透视视图在矢量 SVG 导出时无法保证视角一致性，缺乏明确的降级说明；等值线缺乏统一的图例与色带映射。

## Decision

本 ADR 正式将 `extrusion_3d` 与 `isoline_contour` 提升为 **Native GIS Cartographic Models**，并建立生产级端到端运行时链路。

### 1. 架构不变式（Invariants）

- **MapSpec 是地图期望态的唯一真相**：不引入独立的 `3DSceneSpec` 或 `ContourSpec`。3D 图层与等值线图层均通过 MapSpec Layer、Paint Expressions 与 Components 表达。
- **解耦全局视角与图层模型**：HUD 的 `is3D` 仅代表地图视角的全局倾斜/相机交互模式，而 `extrusion_3d` 是图层级的定量几何柱状模型。图层即使在 2D 平视视角下也是确定性的 MapSpec 挤出图层。

### 2. 3D 挤出模型契约（`extrusion_3d`）

#### 2.1 高度视觉通道（Height Visual Channel）
```python
class ExtrusionHeightChannel:
    height_field: str                  # 数据集中的定量数值字段
    height_unit: str = "m"             # 物理单位或业务单位 (人, 亿元, 米等)
    transform: str = "linear"          # "linear", "sqrt", "log1p"
    scale_factor: float = 1.0          # 缩放系数
    min_visual_height_m: float = 10.0  # 最小可视挤出高度（米）
    max_visual_height_m: float = 5000.0# 最大可视挤出高度（米），防止摩天大楼失真
    clamp_negative: bool = True        # 负值是否钳制为 0（严禁产生倒悬几何）
    base_field: Optional[str] = None   # 底部标高字段
    base_value: float = 0.0            # 默认基底标高
```

#### 2.2 高度归一化与极值保护
- 针对原始数据（如人口 10,000,000 或 GDP 5,000 亿），严禁直接将未经缩放的数值当作米数传给 MapLibre。
- 系统根据数据分布（分位数、最大最小值）将指标映射到 `[min_visual_height, max_visual_height]` 范围。
- 支持对强右偏数据启用 `log1p` 或 `sqrt` 变换，避免单个离群极值将其他要素高度压缩为零。

#### 2.3 双通道独立编码与图例
- **高度通道**（物理高度）与 **颜色通道**（专题设色）可绑定相同字段，亦可绑定不同字段（例如：高度表达 GDP 总量，颜色表达人均 GDP 增长率）。
- 当两者绑定不同字段时，系统生成双图例组件（高度标尺组件 + 专题颜色色带），杜绝单图例歧义。

#### 2.4 相机视角推荐（Camera Profile）
- `extrusion_3d` 建立时推荐最佳观察视角：`pitch = 45.0`，`bearing = -15.0`。
- 相机状态遵循 MapSpec View 规范，尊重用户手势交互，不强制复位造成视角拉锯。

### 3. 等值线/等值面模型契约（`isoline_contour`）

#### 3.1 几何形态二元支持
1. `contour_line`：折线要素（LineString / MultiLineString），包含 `level`（数值）、`unit`（单位）、`is_index_contour`（计曲线/首曲线，布尔值，每 5 级加粗）。
2. `filled_contour_band`：面状等值带（Polygon / MultiPolygon），包含 `min_level`、`max_level`、`level`。

#### 3.2 等值级策略（Contour Level Strategy）
- **显式等级（Explicit Levels）**：用户指定 `[100, 200, 300]` 时，系统 100% 精确保留，绝不自动替换为其他间隔。
- **等间距/分位数（Interval/Quantiles）**：根据数据极差与步长自动划分友好整数级。
- 要素属性强制携带 `level` 与 `unit` 机器可读字段。

#### 3.3 拓扑质量与 CRS 保证
- 输入数据在投影坐标系（UTM/高斯克吕格）下执行密度估计与网格插值，网格大小设安全上限防止 OOM。
- 输出矢量要素重投影回 `EPSG:4326`，自动检测并剔除空几何、面积为零的退化碎片。

### 4. 导出与降级策略（Export Parity & Fallback）

- **Canvas PNG / PDF 导出**：导出高分辨率 MapLibre 真实 3D 渲染画面与图例组件。
- **矢量 SVG 导出降级**：当无法在无头服务器上精确进行 3D 透视光栅化时，系统将 `extrusion_3d` 安全降级为 2D 专题多边形（Choropleth），在元数据中显式声明：
  `export_degraded = True, reason = "3d_perspective_not_vectorized"`。
- **等值线导出**：无论 PNG、PDF 还是 SVG，均以矢量 Line/Polygon 完全对等导出。

### 5. 制图质量语义检查（Semantic QA）

- `EXTRUSION_HEIGHT_FIELD_VALID`：检查高度字段是否存在、是否全为空或全为零。
- `EXTRUSION_PITCH_ADVISORY`：检测高度挤出激活但相机俯仰角接近平视（`pitch < 10°`）时的视角建议。
- `CONTOUR_LEVELS_VALID`：检查等值级是否单调递增、有效级数是否 $\ge 2$。
