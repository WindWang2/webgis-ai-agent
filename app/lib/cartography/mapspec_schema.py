"""Typed MapSpec — Authoritative Schema (V6, ADR-0120 W2).

MapSpec 契约的**唯一权威 schema**（Python Pydantic v2）。此前契约只存在于
dict 约定 + 手维护的前端 TS 镜像（frontend/lib/mapspec-compiler/types.ts），
无版本验证、无迁移、无兼容策略 —— 本模块收口：

- **typed spine**：文档骨架（version/view/sources/layers/layout/thresholds/
  layout.frames/layout.labels）严格校验；类型不符 → 结构化披露而非静默 coerce
  （coerce 会翻转渲染语义 —— R1-C2：``"false"``→``False``、``"2000"``→2000）。
- **open surfaces（诚实登记）**：paint/layout/legend_spec/style/options 等
  工具面保持 dict 开放（MapLibre 表达式与工具生态是开放词表，强类型化是
  独立 Epic）。开放面不是第二事实源 —— 权威形状以本模块为准。
- **unknown fields policy**：全模型 ``extra="allow"``（round-trip 保真，绝不
  丢用户数据）；未知键路径结构化收集，消费方 = vector-pdf 响应元数据 /
  report 元数据 / export sidecar（R1-Min3）。
- **canonical serialization**：输入 dict 的**保序深拷贝**（非 model_dump ——
  dump 会重排键序且受 lax 转换影响，R1-M1）；corpus golden 锁定
  ``dumps(canonicalize(x)) == dumps(x)``。
- **version / migration**：已知版本 {1.0, 1.1, 1.2, 1.3}；1.1/1.2/1.3 纯 additive
  （1.1：frames、labels.collision、组件 options 扩展；1.2：
  layout.component_links 组件图显式边；1.3：顶层 scenario_mode 推演视图协议，
  ADR-0193）。迁移注册表显式声明升级路径；
  未注册路径（更新版本）→ forward_version 标记（publication 消费方拒绝，
  不静默）。spec 自身 ``version`` 字段永不改写 —— 迁移是语义升级而非
  存储改写（desired-state 事实源仍是 lifecycle_engine/store）。

本模块只接**冷路径**（导出/报告/ publication 编译边界）；MapSpec mutation
热路径不经过这里（Issue #1082 读放大对齐）。
"""
from __future__ import annotations

import copy as _copy
import json as _json
from dataclasses import dataclass, field as _dc_field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

#: 数字脊柱原语：接受 int|float，拒绝 str/bool（不 coerce —— 保真披露）。
Number = Union[StrictInt, StrictFloat]

#: 已知 MapSpec 契约版本。1.0 = V5 既有面；1.1 = V6 additive；
#: 1.2 = V7 additive（layout.component_links 组件图显式边）；
#: 1.3 = What-If 推演视图协议（顶层 scenario_mode，ADR-0193）；
#: 1.4 = 多尺度场景协议（顶层 scene + layer.extrusion 类型化，ADR-0199）；
#: 1.5 = CartoIR 语义层类型化（layer.visibility/bivariate/data_binding，
#: C11 —— 渲染端 ABI 版本协商与 visibility/bivariate 原生表示）；
#: 1.6 = 语义缩放表达协议（layer.representation.zoom 窗口计划，方向 5 ——
#: renderer 把窗口编译为 MapLibre 原生 minzoom/maxzoom 门，零 per-frame JS）；
#: 1.7 = 三维场景资产（G3D02 —— model3d 源 + scene.assets 引用：3D Tiles
#: tileset / glTF/GLB 资产的地理锚定挂载；scene 只持 source 指针 + 放置
#: 参数，数据面仍由 source 承载，与 scene.terrain 同「指针不是第二数据面」
#: 纪律。纯 additive：缺失 = 无 3D 资产，存量 spec 行为不变）。
KNOWN_VERSIONS: Tuple[str, ...] = ("1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7")
LATEST_VERSION = "1.7"

#: 版本缺省口径：lifecycle_engine 既有写入恒带 "1.0"；缺失视为 1.0 并披露。
DEFAULT_VERSION = "1.0"

#: spec 级帧数上限（与前端 frame-composer atlas ≤50 页同口径）。
MAX_SPEC_FRAMES = 50

#: 组件图显式边上限（V7：组合关系声明稀有，32 条远超合法构图需求）。
MAX_COMPONENT_LINKS = 32

#: ADR-0193：What-If 推演视图协议词表（顶层 ``scenario_mode`` 可选字段；
#: 缺失/None = 非推演视图）。前端映射：split_view → side-by-side，
#: swipe_compare → swipe（frontend/lib/mapspec/scenario-mode.ts）。
SCENARIO_MODES = ("split_view", "swipe_compare")

#: ADR-0199：多尺度场景协议词表（顶层 ``scene.mode`` 可选字段；缺失 = 2d
#: 既有语义，存量 spec 行为不变）。2.5d = 地形/晕渲呈现、要素无垂直挤出；
#: 3d = 要素垂直挤出（fill-extrusion 高度通道激活），terrain 可选共呈。
SCENE_MODES: Tuple[str, ...] = ("2d", "2.5d", "3d")

# ── v1.5 additive：CartoIR 语义层词表（C11）──────────────────────────────

#: 可见性提示词表（v1.5）。生产方 = scale_rules.ScaleTier.visibility_hints
#: （GRAMMAR.SCALE 尺度带建议）；消费方 = 渲染端 visibility resolver
#: （frontend/lib/carto-ir/visibility.ts）。词表跨模块锁定：本常量与
#: scale_rules 实际产出键集的 ⊆ 关系由 tests/cartography 契约测试锁定，
#: 与前端 SUPPORTED_VISIBILITY_HINT_KEYS 由 contract_manifest.json 漂移闸
#: 锁定。未知键不拒绝（extra="allow" 纪律），只结构化披露。
VISIBILITY_HINT_KEYS: Tuple[str, ...] = (
    "street_detail_minzoom",
    "admin_boundary_detail",
)

#: bivariate 类别索引的要素属性名缺省（= bivariate.py 写入的既有事实）。
#: v1.5 起该魔法字符串收口为本常量 + layer.bivariate.class_field 声明。
BIVARIATE_CLASS_FIELD_DEFAULT = "__biv_class"

#: bivariate 色阵合法规模（= bivariate.SUPPORTED_BIVARIATE_N 既有事实）。
BIVARIATE_MATRIX_SIZES: Tuple[int, ...] = (2, 3)

#: data_binding 字段型词表（v1.5）。声明字段语义类型供编译器选表达式
#: 形态（number → interpolate/step；string → match）；缺失 = 编译器按
#: legend_spec/class_field 既有启发式。
DATA_BINDING_FIELD_TYPES: Tuple[str, ...] = ("number", "string", "boolean", "date")

#: 垂直夸张硬上限（契约口径；规划/质量门/SetScene 校验共用单源）。
MAX_TERRAIN_EXAGGERATION = 10.0

#: 语义缩放表达模式词表（v1.6，方向 5）。权威 owner = 本常量；生产方 =
#: ``representation.representation_at_scale``（组合 scale_rules/label_plan/
#: scene_lod 既有权威推导）；消费方 = 渲染端窗口编译
#: （frontend/lib/carto-ir/representation.ts + compiler/runtime）。三向
#: 锁定：schema 常量 ≡ contract_manifest.json ≡ frontend
#: contract-manifest.ts（漂移闸测试）。模式语义：
#: - raw / point_cluster / visual_heatmap：渲染端可自行执行；
#: - aggregate_grid / generalized：artifact-backed（capability seam，
#:   声明即「调用方确认工件已可用」）；
#: - hidden：detail visibility 门（必带 reason_codes 与披露）。
REPRESENTATION_MODES: Tuple[str, ...] = (
    "raw",
    "point_cluster",
    "visual_heatmap",
    "aggregate_grid",
    "generalized",
    "hidden",
)

#: 每层表达窗口数上限（norm 后实际 ≤ 带数；防病理文档放大）。
MAX_REPRESENTATION_WINDOWS = 8

# ── v1.7 additive：三维场景资产（G3D02）──────────────────────────────────

#: model3d 源的资产格式词表（glTF 2.0 文本 / GLB 二进制 / OGC 3D Tiles 1.1
#: tileset）。词表外的格式经 invalid_fields 披露（typed 拒绝语义不变）。
SCENE_ASSET_FORMATS: Tuple[str, ...] = ("gltf", "glb", "3dtiles")

#: 每场景 3D 资产数上限（放置声明是稀有操作，不是数据；32 与
#: MAX_ACK_SOURCES 等预算面同量级，防病理文档放大）。
MAX_SCENE_ASSETS = 32

#: 垂直基准词表（v1.7）。``ellipsoid`` = WGS84 椭球高；``egm96`` =
#: EGM96 正高（近似大地水准面）；``local`` = 场景局部基准（无全球换算
#: 证据，消费方披露使用）。缺失 datum 不拒绝 —— 质量门披露
#: ``SCENE_ASSET_NO_EVIDENCE``（绝不静默假定全球基准）。
SCENE_ASSET_VERTICAL_DATUMS: Tuple[str, ...] = ("ellipsoid", "egm96", "local")

#: render_order 上界（下界 0；MapLibre custom layer 按挂载序入栈，
#: render_order 折算为挂载序 —— 有界防病理值）。
MAX_SCENE_ASSET_RENDER_ORDER = 1000

#: zoom 域（MapLibre 契约；窗口钳制域）。
REPRESENTATION_ZOOM_MIN = 0.0
REPRESENTATION_ZOOM_MAX = 24.0


class _SpecModel(BaseModel):
    """全 schema 基类：未知键保留（round-trip 保真），无静默丢弃。"""

    model_config = ConfigDict(extra="allow")


# ── 文档骨架 ──────────────────────────────────────────────────────────────


class MapView(_SpecModel):
    center: Optional[List[Number]] = None  # [lng, lat]
    zoom: Optional[Number] = None
    pitch: Optional[Number] = None
    bearing: Optional[Number] = None


class ClusterSourceConfig(_SpecModel):
    radius: Optional[Number] = None
    maxzoom: Optional[Number] = None


class GeoJSONMapSpecSource(_SpecModel):
    """geojson 源。inlineData 是开放 GeoJSON 载荷（不深校验，防 O(payload)）。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["geojson"]
    dataPath: Optional[StrictStr] = None
    url: Optional[StrictStr] = None
    inlineData: Optional[Any] = None
    cluster: Optional[ClusterSourceConfig] = None
    content_revision: Optional[Number] = None
    # ADR-0265 additive：dataset 语义契约指纹（引用 + 指纹，不搬运数据/
    # 全量 schema；缺席 = 旧 source 合法，消费面按 DESCRIPTOR_MISSING 披露）。
    descriptor_fingerprint: Optional[StrictStr] = None


class VectorMapSpecSource(_SpecModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["vector"]
    tiles: List[StrictStr]
    minzoom: Optional[Number] = None
    maxzoom: Optional[Number] = None


class RasterMapSpecSource(_SpecModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["raster"]
    imageRef: StrictStr
    bounds: List[Number]  # [w, s, e, n]
    imageSize: Optional[List[Number]] = None


class DataFabricMapSpecSource(_SpecModel):
    """Data Fabric lazy/materialized 协议源（ADR-0050）；键面以
    ``mapspec_source.store_data`` 实际写入为准，其余开放。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["data_fabric", "wms", "wmts", "pmtiles"]
    catalog_item_id: Optional[StrictStr] = None
    lazy: Optional[StrictBool] = None
    ref_id: Optional[StrictStr] = None
    url: Optional[StrictStr] = None
    dataPath: Optional[StrictStr] = None
    # profiler 载荷（mapspec_source.profile_data 对 fabric 源读 inlineData）
    inlineData: Optional[Any] = None
    profile: Optional[Dict[str, Any]] = None
    profile_fingerprint: Optional[StrictStr] = None
    data_fingerprint: Optional[StrictStr] = None
    # ADR-0265 additive：dataset 语义契约指纹（同 GeoJSON 源纪律）。
    descriptor_fingerprint: Optional[StrictStr] = None


class RasterDemMapSpecSource(_SpecModel):
    """raster-dem 源（AC-06 ADR-0155 additive：hillshade 层的数据面）。
    MapLibre `raster-dem` 源的最小投影；encoding 缺省由 MapLibre 取 "mapbox"。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["raster-dem"]
    url: StrictStr
    tileSize: Optional[Number] = None
    encoding: Optional[Literal["mapbox", "terrarium"]] = None


class Model3dMapSpecSource(_SpecModel):
    """model3d 源（v1.7 additive，G3D02）—— 3D Tiles / glTF 资产的数据面指针。

    纪律（与 scene.terrain 的「指针不是第二数据面」同源）：本源只承载
    **数据身份**（格式词表 + url/ref 指针 + revision/fingerprint + 可选
    地理 bbox 证据）；放置语义（锚点/朝向/缩放/垂直基准）在
    ``scene.assets[].placement/vertical``，两层绝不分叉混写。

    - ``format``：``gltf``（glTF 2.0 JSON）/ ``glb``（GLB 二进制容器）/
      ``3dtiles``（OGC 3D Tiles 1.1 tileset.json 入口）。渲染端按子集
      消费（能力边界 typed 披露，不静默降级）。
    - ``url``：https 或同源相对 URL（前端资源策略再校验；SSRF 责任在
      服务端消费链）。``ref_id``：data fabric ref（水合链供给前消费方
      按 typed unavailable 披露，不虚构载荷）。二者至少其一（写路径
      coordinator 校验，schema 层保留半成品可诊断性）。
    - ``bbox``：可选 [w, s, e, n] 地理范围证据（tileset 未必自带；缺失
      不阻塞 —— 调度器按需 fetch 后以实测范围为准）。
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["model3d"]
    format: Literal[SCENE_ASSET_FORMATS]  # type: ignore[valid-type]
    url: Optional[StrictStr] = None
    ref_id: Optional[StrictStr] = None
    content_revision: Optional[Number] = None
    data_fingerprint: Optional[StrictStr] = None
    descriptor_fingerprint: Optional[StrictStr] = None
    bbox: Optional[List[Number]] = None  # [w, s, e, n]


MapSpecSource = Union[
    GeoJSONMapSpecSource,
    VectorMapSpecSource,
    RasterMapSpecSource,
    RasterDemMapSpecSource,
    DataFabricMapSpecSource,
    Model3dMapSpecSource,
]


# ── v1.4 additive：多尺度场景协议（ADR-0199）────────────────────────────


class TerrainSceneSpec(_SpecModel):
    """场景地形参数（指针 + 参数，不是第二数据面）。

    ``source`` 必须指向 spec.sources 内的 raster-dem 源 id（dangling 由
    coordinator.validate 的 SCENE_TERRAIN_SOURCE_REF 阻塞 —— 与图层
    INVALID_SOURCE_REF 同 fail-closed 口径）。``elevation_ref`` 是可选的
    证据 ref（session ref / artifact id），用于溯源"这份地形来自哪份
    已核实 DEM 数据"；缺失不影响渲染，但质量门披露无证据。"""

    model_config = ConfigDict(extra="allow")

    source: StrictStr
    exaggeration: Optional[Number] = None  # 缺省 1.0；0 < x ≤ 10（质量门钳制披露）
    vertical_unit: Optional[Literal["m"]] = None
    elevation_ref: Optional[StrictStr] = None


class SceneCameraSpec(_SpecModel):
    """场景级相机推荐（presentation 面；与 view.pitch/bearing 独立 ——
    view 是当前相机态，scene.camera 是场景建议档，前端择优应用）。"""

    model_config = ConfigDict(extra="allow")

    pitch: Optional[Number] = None
    bearing: Optional[Number] = None
    #: 0 = reduced-motion（无过渡动画）；缺省 = 前端既有过渡节奏。
    transition_ms: Optional[Number] = None


# ── v1.7 additive：三维场景资产（G3D02）──────────────────────────────────


class SceneAssetVertical(_SpecModel):
    """3D 资产垂直基准声明（v1.7 additive）。

    数据诚信红线：datum 缺失不拒绝但由质量门披露（绝不静默假定全球基准）；
    ``unit`` 现仅 "m"（其他单位需要换算证据，暂不开放词表位）；
    ``elevation_ref`` 是锚点高程的证据 ref（session ref / artifact id）——
    与 extrusion.elevation_ref 同「有/无证据」二分口径。"""

    model_config = ConfigDict(extra="allow")

    datum: Optional[Literal[SCENE_ASSET_VERTICAL_DATUMS]] = None  # type: ignore[valid-type]
    unit: Optional[Literal["m"]] = None
    elevation_ref: Optional[StrictStr] = None


class SceneAssetPlacement(_SpecModel):
    """3D 资产放置参数（v1.7 additive）—— 地理锚定 + ENU 姿态 + 缩放。

    朝向约定（显式、不猜）：``heading/pitch/roll`` 为局部 ENU（东-北-天）
    框角度（度，右手系）；模型内部轴系由格式规范固定（glTF y-up、
    3D Tiles z-up），渲染端按规范校正 —— 本块不设 up_axis 配置位，
    防止调用方发明约定。``scale`` 为米制缩放因子（>0；写路径校验）。
    ``altitude_m`` 缺省 0（放置约定，非编造数据）；``ground_clamp``
    声明贴地意图（terrain 在场时用 queryTerrainElevation，缺席时披露
    不阻塞）。范围校验（lng/lat 域、角度域、scale>0、render_order 域）
    在写路径 handler 与质量门 —— schema 层保留坏文档可诊断性。"""

    model_config = ConfigDict(extra="allow")

    anchor: List[Number]  # [lng, lat]
    altitude_m: Optional[Number] = None
    heading_deg: Optional[Number] = None
    pitch_deg: Optional[Number] = None
    roll_deg: Optional[Number] = None
    scale: Optional[Number] = None
    ground_clamp: Optional[StrictBool] = None


class SceneAssetLodPolicy(_SpecModel):
    """3D 资产 LOD 策略（v1.7 additive）—— 渲染预算的**收紧**声明。

    ``max_sse``：屏幕空间误差预算（像素；缺省渲染端档位 16）。``max_visible_tiles``：
    同时可见 tile 上限（只能收紧机器档位上限，不能放宽 —— 防病理 spec
    把弱机拖死）。策略是提示非命令：渲染端以机器档位为硬顶、策略值取
    min（诚实降级披露）。"""

    model_config = ConfigDict(extra="allow")

    max_sse: Optional[Number] = None
    max_visible_tiles: Optional[StrictInt] = None


class SceneAssetSpec(_SpecModel):
    """单个 3D 场景资产（v1.7 additive）—— source 指针 + 放置/证据声明。

    ``source`` 必须指向 spec.sources 内 model3d 源 id（dangling/错型由
    coordinator.validate 的 SCENE_ASSET_SOURCE_REF/_TYPE 阻塞 —— 与
    scene.terrain 同 fail-closed 口径）。``render_order`` 折算挂载序
    （大者后挂=更靠近相机侧绘制）。``provenance_ref``：资产来源/许可
    证据 ref（缺失由质量门披露 SCENE_ASSET_NO_EVIDENCE）。"""

    model_config = ConfigDict(extra="allow")

    id: StrictStr
    source: StrictStr
    visible: Optional[StrictBool] = None
    placement: Optional[SceneAssetPlacement] = None
    vertical: Optional[SceneAssetVertical] = None
    render_order: Optional[StrictInt] = None
    lod: Optional[SceneAssetLodPolicy] = None
    provenance_ref: Optional[StrictStr] = None


class MapSceneConfig(_SpecModel):
    """顶层场景配置（v1.4 additive；缺失 = 既有 2d 语义）。

    - ``mode``：2d / 2.5d / 3d（词表 SCENE_MODES；由 scene_planning 决策
      投影写入，SetSceneIntent 事务挂载）。
    - ``terrain``：地形参数（source 指针 + exaggeration + 证据 ref）。
    - ``camera``：场景建议相机档。
    - ``reason_code``：决策溯源（scene_planning.REASON_CODES 词表）。
    - ``degrade_to``：声明的回退档（退化链 3d→2.5d→2d 的既有意图记录）。
    - ``assets``（v1.7，G3D02）：3D 场景资产表（id 键控；source 指针 +
      放置/垂直基准/LOD 声明）。缺失 = 无 3D 资产，存量行为不变。
    """

    model_config = ConfigDict(extra="allow")

    mode: Literal[SCENE_MODES]  # type: ignore[valid-type]
    terrain: Optional[TerrainSceneSpec] = None
    camera: Optional[SceneCameraSpec] = None
    reason_code: Optional[StrictStr] = None
    degrade_to: Optional[Literal["2.5d", "2d"]] = None
    reduced_motion: Optional[StrictBool] = None
    #: v1.7 additive（G3D02）：三维场景资产表（有界 MAX_SCENE_ASSETS）。
    assets: Optional[List[SceneAssetSpec]] = Field(
        default=None, max_length=MAX_SCENE_ASSETS)


class MapSpecLabelZoomBand(_SpecModel):
    """缩放分级档（ac-05，ADR-0154）：[minZoom, maxZoom) 内显示 top
    ``topRatio`` 比例的要素标注；``sizeRatio`` 为该档字号比例系数
    （绝对基准归符号律 / label.size，本档只给乘数）。additive optional ——
    不声明时运行时按全量显示。"""

    minZoom: Number
    maxZoom: Number
    topRatio: Number = 1.0
    sizeRatio: Optional[Number] = None


class MapSpecLayerLabel(_SpecModel):
    field: StrictStr
    size: Optional[Any] = None
    color: Optional[Any] = None
    haloColor: Optional[StrictStr] = None
    haloWidth: Optional[Number] = None
    # ── 标注策略（ac-05，ADR-0154；全 additive optional，向后兼容）──────
    #: all | top_n | hover_only（缺省 all）。
    mode: Optional[Literal["all", "top_n", "hover_only"]] = None
    #: top_n 模式的常驻标注上限（确定性档位由 label_plan 给出）。
    topN: Optional[StrictInt] = None
    #: 重要度代理字段（数值型）；缺省按原序。
    priorityField: Optional[StrictStr] = None
    #: 缩放分级档（≤4 档；无声明 = 全 zoom 同策略）。
    zoomBands: Optional[List[MapSpecLabelZoomBand]] = None
    #: 全局字号比例系数（乘在 size 基准上；绝对基准归 06 线符号律）。
    sizeRatio: Optional[Number] = None
    #: "auto" = 随底图亮度自适应文字/晕圈配色；缺省 static（#1007 契约不变）。
    haloMode: Optional[Literal["auto", "static"]] = None


class MapSpecLayerExtrusion(_SpecModel):
    """挤出通道类型化（v1.4 additive；吸收 converter 既有开放 dict 键面）。

    既有写入面（analysis_cartography_converter）携带
    height_field/height_unit/transform/scale_factor/min_visual_height_m/
    max_visual_height_m/stats —— 本模型与该形状逐键兼容（除 height_field
    必填外全部 Optional）。``elevation_ref`` 是 v1.4 新增的垂直证据 ref
    （该高度字段的来源溯源）；质量门用它区分"有证据挤出"与"无证据挤出"。
    """

    model_config = ConfigDict(extra="allow")

    height_field: StrictStr
    height_unit: Optional[StrictStr] = None
    transform: Optional[StrictStr] = None
    scale_factor: Optional[Number] = None
    min_visual_height_m: Optional[Number] = None
    max_visual_height_m: Optional[Number] = None
    clamp_negative: Optional[StrictBool] = None
    base_field: Optional[StrictStr] = None
    base_value: Optional[Number] = None
    stats: Optional[Dict[str, Any]] = None
    elevation_ref: Optional[StrictStr] = None


# ── v1.5 additive：CartoIR 语义层类型化（C11）────────────────────────────


class MapSpecLayerVisibility(_SpecModel):
    """图层可见性结构面（v1.5 additive）。

    可见性裁决的**单一 precedence**（渲染端唯一 resolver 实现，本 schema
    只承载声明，不裁决）：

    1. ``layout.visibility``：用户 durable 决策通道（MapLibre 原生键，
       visibility-transaction/user-mutation 写入）—— 恒最优先；
    2. 层面 ``visible``（StrictBool）：authoring 缺省（后端产出的意图），
       仅当 (1) 缺失时作为 layout.visibility 的初值；
    3. ``min_zoom``/``max_zoom``：结构 zoom 门（编译到 MapLibre 层级
       minzoom/maxzoom，与 (1)(2) 正交 —— zoom 维度与显隐维度独立）；
    4. ``hints``：grammar 尺度带建议的**冻结声明**（键 = VISIBILITY_HINT_KEYS
       词表，值 = zoom 阈值）。渲染端对未声明结构门但 hints 引用词表键的
       层按 advisory 推导；永不覆盖 (1)(2)。

    值域：MapLibre zoom [0, 24]；min_zoom < max_zoom 由消费方校验披露
    （schema 层不拒绝 —— 保留坏文档的可诊断性）。
    """

    model_config = ConfigDict(extra="allow")

    min_zoom: Optional[Number] = None
    max_zoom: Optional[Number] = None
    hints: Optional[Dict[StrictStr, Number]] = None


class MapSpecLayerBivariate(_SpecModel):
    """双变量编码声明（v1.5 additive）—— bivariate 原生 MapModel。

    此前 bivariate 语义只能靠 ``legend_spec.class_field`` 开放 dict 字符串
    约定（extra 通道），schema 不可见、漂移不可测。本块只承载**语义身份**
    （哪两个字段、什么色阵规模、类别索引写在哪个要素属性），不复制色值
    （色值唯一权威仍是 backend bivariate.py 产出的 match paint —— #679
    单一色源原则）；``class_field`` 与 legend_spec.class_field 的一致性由
    契约测试锁定（converter 双写面）。

    降级路径（渲染端）：矩阵规模不支持 / class_field 属性缺失 → 回退
    x_field 单场 choropleth 语义 + 结构化披露，绝不白图。
    """

    model_config = ConfigDict(extra="allow")

    x_field: StrictStr
    y_field: StrictStr
    matrix: Literal[BIVARIATE_MATRIX_SIZES] = 3  # type: ignore[valid-type]
    class_field: StrictStr = BIVARIATE_CLASS_FIELD_DEFAULT
    palette_id: Optional[StrictStr] = None


class MapSpecLayerDataBinding(_SpecModel):
    """数据绑定声明（v1.5 additive）—— 字段引用的显式契约。

    此前字段名散落在 paint 表达式 / legend_spec.class_field / filter 里靠
    字符串约定；本块把「这张专题图绑定了哪个字段、什么型」提升为一等声明，
    供编译器选表达式形态（number → interpolate/step；string → match）、
    供 feature-properties typed accessor 做字段存在性检查（消灭 OBJECTID
    式魔法字段 as any）。声明与实际 paint 字段的一致性属质量门披露面，
    不在本 schema 阻塞。"""

    model_config = ConfigDict(extra="allow")

    field: StrictStr
    field_type: Optional[Literal[DATA_BINDING_FIELD_TYPES]] = None  # type: ignore[valid-type]
    class_field: Optional[StrictStr] = None


class MapSpecRepresentationWindow(_SpecModel):
    """表达窗口（v1.6 additive）—— zoom 半开区间 [min_zoom, max_zoom) × 模式。

    窗口计划是 **desired state 声明**（怎么表达），不是运行时状态：zoom
    手势不回写本块。渲染端把窗口编译为 MapLibre 原生 minzoom/maxzoom 门
    的层序列（live runtime 与 headless 编译同一裁决面 —— 双路径不漂移）。

    纪律：
    - 窗口必须连续覆盖 [0,24)（消费方 normalize；缺口计划诚实拒绝回退
      层缺省 + 披露，schema 不拒绝以保留坏文档可诊断性）；
    - ``mode`` ∈ :data:`REPRESENTATION_MODES`（未知值 invalid 披露）；
    - ``reason_codes`` 有界（≤8），窗口级证据（QA/报告消费，不渲染）；
    - ``hidden`` 窗口必带 reason_codes（空理由的隐藏 = 不可审计）。
    """

    model_config = ConfigDict(extra="allow")

    min_zoom: Number
    max_zoom: Number
    mode: Literal[REPRESENTATION_MODES]  # type: ignore[valid-type]
    reason_codes: List[StrictStr] = Field(default_factory=list)


class MapSpecLayerRepresentation(_SpecModel):
    """图层语义缩放表达计划（v1.6 additive）。

    生产方 = 后端 ``representation.project_mapspec_representation``
    （RepresentationAtScale 的声明投影；平凡全 raw 计划**不落笔** ——
    旧 spec/简单层零行为变化）。消费方 = 前端 ``carto-ir/representation.ts``
    归一化 + compiler/runtime 窗口编译。显式声明优先于渲染端计数推断
    （resolveDensityPresentation 仅在无声明时介入）。

    user-wins：用户 pin 的表达在 authoring 期由后端落为单一窗口
    （user-wins 快照）；渲染端不再二次猜测。运行期用户显隐
    （layout.visibility 通道）与本块正交且恒优先。
    """

    model_config = ConfigDict(extra="allow")

    windows: List[MapSpecRepresentationWindow] = Field(default_factory=list)


class MapSpecLayer(_SpecModel):
    """图层。paint/layout/filter 是 MapLibre 开放面（Dict[Any]）；
    ``legend_spec`` 是工具层写入的专题图例标记（开放 dict，推导见
    render_scene.derive_legend_items —— 单源，不在本层建模）。
    ``visible`` 严格 bool：字符串 ``"false"`` 必须保留原值并披露
    （孪生渲染语义是 ``is False`` 检查 —— coerce 会静默隐藏图层）。"""

    model_config = ConfigDict(extra="allow")

    id: StrictStr
    source: StrictStr
    # AC-06（ADR-0155）additive：background/hillshade 表达力补齐。
    # background 无数据面 —— 约定 source 携带 "" 哨兵（编译器/运行时省略
    # source 键）；hillshade 消费 raster-dem 源。
    type: Literal[
        "circle",
        "line",
        "fill",
        "symbol",
        "heatmap",
        "raster",
        "fill-extrusion",
        "background",
        "hillshade",
    ]
    paint: Optional[Dict[str, Any]] = None
    layout: Optional[Dict[str, Any]] = None
    label: Optional[MapSpecLayerLabel] = None
    filter: Optional[List[Any]] = None
    sourceLayer: Optional[StrictStr] = None
    cluster: Optional[ClusterSourceConfig] = None
    legend_spec: Optional[Dict[str, Any]] = None
    visible: Optional[StrictBool] = None
    #: v1.4 additive（ADR-0199）：挤出通道类型化（既有开放 dict 的收口；
    #: 旧 spec 无该键或键形状兼容 —— round-trip 不变）。
    extrusion: Optional[MapSpecLayerExtrusion] = None
    #: v1.5 additive（C11）：可见性结构面（zoom 门 + grammar 提示冻结）。
    visibility: Optional[MapSpecLayerVisibility] = None
    #: v1.5 additive（C11）：bivariate 原生语义声明（不再藏 legend_spec
    #: extra；色值权威仍在 paint）。
    bivariate: Optional[MapSpecLayerBivariate] = None
    #: v1.5 additive（C11）：数据绑定一等声明（字段 + 语义型）。
    data_binding: Optional[MapSpecLayerDataBinding] = None
    #: v1.6 additive（方向 5）：语义缩放表达窗口计划（缺失 = 层缺省
    #: 表达 + 渲染端既有计数推断；存量 spec 行为不变）。
    representation: Optional[MapSpecLayerRepresentation] = None


class MapThresholds(_SpecModel):
    """导出预算。strict 数值：字符串 ``"2000"`` 在孪生 ``_valid()`` 下被
    忽略回退默认 —— coerce 会无声激活截断（语义翻转，R1-C2 实证）。
    int|float 均合法（与孪生 ``isinstance(val, (int, float))`` 同口径）。"""

    model_config = ConfigDict(extra="allow")

    maxFeatures: Optional[Number] = None
    timeoutMs: Optional[Number] = None


class MapSpecLegendConfig(_SpecModel):
    title: Optional[StrictStr] = None
    position: Optional[Literal["top-right", "top-left", "bottom-right", "bottom-left"]] = None
    visible: Optional[StrictBool] = None


class MapSpecControlConfig(_SpecModel):
    type: Literal["navigation", "scale", "fullscreen"]
    position: Optional[Literal["top-right", "top-left", "bottom-right", "bottom-left"]] = None


class ComponentPlacement(_SpecModel):
    mode: Literal["anchor", "floating"]
    anchor: Optional[StrictStr] = None
    x: Optional[Number] = None
    y: Optional[Number] = None
    width: Optional[Number] = None
    height: Optional[Number] = None
    zIndex: Optional[Number] = None
    collapsed: Optional[StrictBool] = None


COMPONENT_TYPES = (
    "basemap",
    "legend",
    "continuous_colorbar",
    "categorical_legend",
    "north_arrow",
    "scale_bar",
    "title",
    "subtitle",
    "annotation",
    "graticule",
    "map_border",
    "attribution",
    "statistics_panel",
    "chart_panel",
    "table_panel",
    "export_layout",
    "inset_map",
    "methodology_note",
    "uncertainty_panel",
    "decision_panel",
    # ac-05（ADR-0154）：标注图层 —— 绑定/决策面组件（labels 经
    # layer.label 子层渲染）；CartographyComponent.to_mapspec() 落
    # MapSpec 的组件通道，必须能过本 schema 校验。
    "label_layer",
    # G3D05（ADR-0275）：三维制图产品组件 —— 场景披露（内容绑定
    # spec.scene，options 仅样式/附注）与线路高程剖面（points/ref 工件）。
    "scene_disclosure",
    "elevation_profile",
)


class MapSpecComponent(_SpecModel):
    id: StrictStr
    type: Literal[COMPONENT_TYPES]  # type: ignore[valid-type]
    enabled: Optional[StrictBool] = None
    position: Optional[
        Literal[
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right", "none",
        ]
    ] = None
    priority: Optional[Number] = None
    style: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    compatibility: Optional[Dict[str, Any]] = None
    variant: Optional[StrictStr] = None
    placement: Optional[ComponentPlacement] = None
    # 注（ADR-0214 D4）：组件级用户锁的单一事实是 workbench doc 的
    # ``lockedComponentIds``（W15 既有机制，lifecycle_engine 守卫全量
    # 执行）—— 本 schema 不设第二锁位。


# ── V6 1.1 additive：spec 级 frames 与 label 配置 ────────────────────────


class LayerOverride(_SpecModel):
    """帧级图层覆写。**逐键 deep-merge** 语义（R1-M5）：只覆盖出现的键，
    未提及键保留基础 spec 值 —— 浅替换是静默数据丢失路径。"""

    model_config = ConfigDict(extra="allow")

    visible: Optional[StrictBool] = None
    opacity: Optional[Number] = None


#: F14（ADR-0211 增补）：纸型 profile 词表（mm 预设，全部 ≤ MAX_PAGE_MM）。
#: 页面几何解析优先级：profile > 裸 width/height > A4 landscape 缺省。
PageProfile = Literal[
    "a4_portrait", "a4_landscape",
    "a3_portrait", "a3_landscape",
    "a2_landscape", "a1_landscape", "a0_landscape",
]


class FramePageSize(_SpecModel):
    width: Number
    height: Number
    # v1.1 additive（F14）：纸型预设 —— 合法值整体决定页宽高（覆盖裸宽高），
    # 非法值经 invalid_fields 如实披露（typed 拒绝语义不变）。
    profile: Optional[PageProfile] = None


class MapSpecFrame(_SpecModel):
    """spec 级 atlas 帧（v1.1 additive，写入面 = layout.frames，经既有
    SetLayoutIntent 字段级 merge 通道，零 lifecycle_engine 改动 —— R1-M4）。"""

    model_config = ConfigDict(extra="allow")

    id: Optional[StrictStr] = None
    title: Optional[StrictStr] = None
    view: Optional[MapView] = None
    extent: Optional[List[Number]] = None  # [w, s, e, n] 优先于 view
    layerOverrides: Optional[Dict[StrictStr, LayerOverride]] = None
    pageSize: Optional[FramePageSize] = None
    enabled: Optional[StrictBool] = None


class MapLabelConfig(_SpecModel):
    """导出标签通道（v1.1 additive）。collision 缺省 = 既有无碰撞放置
    （byte parity 保住）；``"deterministic"`` = 双孪生确定性碰撞求解。"""

    model_config = ConfigDict(extra="allow")

    collision: Optional[Literal["deterministic"]] = None
    maxLabels: Optional[StrictInt] = None


#: 组件图显式边类型（V7 Goal 08 Phase B：Component Graph）。
#: - binds_to：组件 → 图层/源的数据绑定（图例绑主题层、图表绑聚合表）
#: - requires：组件 → 组件的依赖（subtitle 依赖 title 在场）
#: - groups：容器 → 子组件的逻辑分组（版面容器）
#: - annotates：注记 → 被注记对象（callout 指向组件/图层）
#: - under：z 序约束（src 渲染在 dst 之下）
COMPONENT_LINK_TYPES = (
    "binds_to",
    "requires",
    "groups",
    "annotates",
    "under",
)

_COMPONENT_LINK_TARGET_KINDS = ("component", "layer", "source")


class ComponentLinkSpec(_SpecModel):
    """组件图显式边（v1.2 additive，写入面 = layout.component_links）。

    derived 边（options.layerId 等既有语义）不在此登记 —— 它们由
    component_graph.build_component_graph 推导；本表只承载**显式声明**
    （推导规则覆盖不了的组合关系）。src/dst 必须是已注册组件 id 或
    spec 内 layer/source id，悬空由 component_graph.validate 披露。"""

    model_config = ConfigDict(extra="allow")

    src: StrictStr
    dst: StrictStr
    type: Literal[COMPONENT_LINK_TYPES]  # type: ignore[valid-type]
    dst_kind: Optional[Literal[_COMPONENT_LINK_TARGET_KINDS]] = None  # type: ignore[valid-type]


class MapSpecLayoutConfig(_SpecModel):
    legend: Optional[MapSpecLegendConfig] = None
    controls: Optional[List[MapSpecControlConfig]] = None
    margins: Optional[Dict[str, Number]] = None  # top/right/bottom/left
    components: Optional[List[MapSpecComponent]] = None
    frames: Optional[List[MapSpecFrame]] = Field(default=None, max_length=MAX_SPEC_FRAMES)
    labels: Optional[MapLabelConfig] = None
    #: v1.2 additive：组件图显式边（有界 —— 组合关系是稀有声明，不是数据）。
    component_links: Optional[List[ComponentLinkSpec]] = Field(
        default=None, max_length=MAX_COMPONENT_LINKS)
    #: ADR-0214 D3 additive：组合身份块（开放 dict —— 键契约由
    #: composition_contract.CompositionIdentity 单一事实定义：template_id/
    #: template_version/contract_id/contract_fingerprint/component_abi_version/
    #: component_versions/applied_revision；有界写入）。随 layout 进入
    #: cartographic_fingerprint 投影 → 模板/组件版本变化可被 plan/product
    #: 指纹捕获。缺省/None = 未应用契约（存量 spec 指纹逐位不变）。
    composition: Optional[Dict[str, Any]] = None


class MapSpecDocument(_SpecModel):
    """authoritative 文档 spine。schema 权威 = 本模型族。"""

    model_config = ConfigDict(extra="allow")

    version: StrictStr
    view: Optional[MapView] = None
    sources: Optional[Dict[StrictStr, MapSpecSource]] = None
    layers: Optional[List[MapSpecLayer]] = None
    layout: Optional[MapSpecLayoutConfig] = None
    thresholds: Optional[MapThresholds] = None
    #: v1.3 additive（ADR-0193）：What-If 推演视图协议；缺失 = 非推演视图。
    scenario_mode: Optional[Literal[SCENARIO_MODES]] = None  # type: ignore[valid-type]
    #: v1.4 additive（ADR-0199）：多尺度场景协议；缺失 = 既有 2d 语义。
    scene: Optional[MapSceneConfig] = None


#: TS 投影（W3 生成器）消费的导出面：核心文档类型 → 模型类。
SCHEMA_EXPORT_MODELS: Tuple[Tuple[str, type], ...] = (
    ("MapSpec", MapSpecDocument),
    ("MapView", MapView),
    ("MapSpecSource", None),  # union —— 生成器特判
    ("GeoJSONMapSpecSource", GeoJSONMapSpecSource),
    ("VectorMapSpecSource", VectorMapSpecSource),
    ("RasterMapSpecSource", RasterMapSpecSource),
    ("RasterDemMapSpecSource", RasterDemMapSpecSource),
    ("DataFabricMapSpecSource", DataFabricMapSpecSource),
    ("ClusterSourceConfig", ClusterSourceConfig),
    ("MapSpecLayer", MapSpecLayer),
    ("MapSpecLayerExtrusion", MapSpecLayerExtrusion),
    ("MapSpecLayerVisibility", MapSpecLayerVisibility),
    ("MapSpecLayerBivariate", MapSpecLayerBivariate),
    ("MapSpecLayerDataBinding", MapSpecLayerDataBinding),
    ("MapSpecRepresentationWindow", MapSpecRepresentationWindow),
    ("MapSpecLayerRepresentation", MapSpecLayerRepresentation),
    ("MapSpecLayerLabel", MapSpecLayerLabel),
    ("MapSpecLabelZoomBand", MapSpecLabelZoomBand),
    ("MapSpecLegendConfig", MapSpecLegendConfig),
    ("MapSpecControlConfig", MapSpecControlConfig),
    ("ComponentPlacement", ComponentPlacement),
    ("MapSpecComponent", MapSpecComponent),
    ("LayerOverride", LayerOverride),
    ("FramePageSize", FramePageSize),
    ("MapSpecFrame", MapSpecFrame),
    ("MapLabelConfig", MapLabelConfig),
    ("ComponentLinkSpec", ComponentLinkSpec),
    ("MapSpecLayoutConfig", MapSpecLayoutConfig),
    ("MapThresholds", MapThresholds),
    ("MapSceneConfig", MapSceneConfig),
    ("TerrainSceneSpec", TerrainSceneSpec),
    ("SceneCameraSpec", SceneCameraSpec),
    ("Model3dMapSpecSource", Model3dMapSpecSource),
    ("SceneAssetSpec", SceneAssetSpec),
    ("SceneAssetPlacement", SceneAssetPlacement),
    ("SceneAssetVertical", SceneAssetVertical),
    ("SceneAssetLodPolicy", SceneAssetLodPolicy),
)


# ── 解析结果 / 迁移 ──────────────────────────────────────────────────────


@dataclass
class FieldDisclosure:
    """一条 schema 合规披露（unknown / invalid 字段）。"""

    path: str
    kind: Literal["unknown", "invalid"]
    got: str  # 值类型名（invalid）或 ""（unknown）

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "kind": self.kind, "got": self.got}


#: 披露清单上限（R2-M4：50MB 载荷全未知键可产出百万级 disclosure ——
#: 内存/响应双放大；封顶 + truncated 标志，与 DiagnosticSink 同词汇）。
MAX_DISCLOSURES_PER_KIND = 200


@dataclass
class MapSpecParseResult:
    """parse_mapspec 产物。document 恒为输入的保序深拷贝（canonical）。"""

    document: Dict[str, Any]
    original_version: str
    effective_version: str
    migrated: bool
    valid: bool
    forward_version: bool
    unknown_fields: List[FieldDisclosure] = _dc_field(default_factory=list)
    invalid_fields: List[FieldDisclosure] = _dc_field(default_factory=list)
    disclosures_truncated: bool = False

    @property
    def disclosures(self) -> List[FieldDisclosure]:
        return self.unknown_fields + self.invalid_fields

    def disclosures_payload(self) -> List[Dict[str, str]]:
        return [d.to_dict() for d in self.disclosures]


class MapSpecSchemaError(Exception):
    """publication 消费方对不合规 spec 的显式拒绝（forward version 等）。

    携带结构化上下文；绝不静默降级（R1-C2 / R1-M4 纪律）。"""

    def __init__(self, code: str, message: str, result: Optional[MapSpecParseResult] = None):
        super().__init__(message)
        self.code = code
        self.result = result


def _version_of(payload: Any) -> Tuple[str, bool]:
    """(version, missing)。非字符串 version 视为缺失（并交由 invalid 披露）。"""
    if isinstance(payload, dict):
        v = payload.get("version")
        if isinstance(v, str):
            return v, False
        return DEFAULT_VERSION, True
    return DEFAULT_VERSION, True


_UPGRADERS: Dict[Tuple[str, str], Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    # 1.1 相对 1.0 纯 additive（frames/labels 组件 options 均可选）：
    # 语义升级 = 通过校验，不需要改写文档（identity 拷贝）。
    ("1.0", "1.1"): lambda doc: doc,
    # 1.2 相对 1.1 纯 additive（layout.component_links 可选；缺省无图 =
    # 组件图全部由 derived 通道推导 —— 存量 spec 语义不变）。
    ("1.1", "1.2"): lambda doc: doc,
    # 1.3 相对 1.2 纯 additive（顶层 scenario_mode 可选；缺失 = 非推演
    # 视图，存量 spec 语义不变。ADR-0193）。
    ("1.2", "1.3"): lambda doc: doc,
    # 1.4 相对 1.3 纯 additive（顶层 scene 与 layer.extrusion 可选；缺失 =
    # 既有 2d 语义，存量 spec 语义不变。ADR-0199）。
    ("1.3", "1.4"): lambda doc: doc,
    # 1.5 相对 1.4 纯 additive（layer.visibility/bivariate/data_binding 均
    # 可选；缺失 = 既有开放面语义，存量 spec 语义不变。C11 CartoIR）。
    ("1.4", "1.5"): lambda doc: doc,
    # 1.6 相对 1.5 纯 additive（layer.representation 可选；缺失 = 层缺省
    # 表达 + 渲染端既有计数推断，存量 spec 语义不变。方向 5）。
    ("1.5", "1.6"): lambda doc: doc,
    # 1.7 相对 1.6 纯 additive（model3d 源类型 + scene.assets 可选；缺失 =
    # 无 3D 资产，存量 spec 语义不变。G3D02）。
    ("1.6", "1.7"): lambda doc: doc,
}


def register_upgrader(fr: str, to: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """声明显式升级路径（破坏性变更必须走这里 + 测试，禁止隐式）。"""
    _UPGRADERS[(fr, to)] = fn


def _find_upgrade_path(fr: str, to: str) -> Optional[List[Tuple[str, str]]]:
    if fr == to:
        return []
    steps: List[Tuple[str, str]] = []
    cur = fr
    seen = {cur}
    while cur != to:
        nxt = next((t for (f, t) in _UPGRADERS if f == cur and t not in seen), None)
        if nxt is None:
            return None
        steps.append((cur, nxt))
        seen.add(nxt)
        cur = nxt
    return steps


def _walk_schema_tree(annotation: Any, payload: Any, path: str, out: List[FieldDisclosure]) -> None:
    """沿 schema 已知形状收集 unknown 键（extra="allow" 值保留在文档中）。"""
    origin = _annotation_origin(annotation)
    if origin is dict:
        # Dict[str, X]：值节点按 X 递归；未知键合法（开放 dict 面）。
        args = _args_of(annotation)
        if args and len(args) == 2 and isinstance(payload, dict):
            for k, v in payload.items():
                _walk_schema_tree(args[1], v, f"{path}.{k}" if path else k, out)
        return
    if origin is list:
        args = _args_of(annotation)
        if args and isinstance(payload, list):
            for i, item in enumerate(payload):
                _walk_schema_tree(args[0], item, f"{path}[{i}]", out)
        return
    if origin is Union:
        # union：剥掉 NoneType 后单支直入；多支按"最优 BaseModel 匹配"
        # （对象载荷）或首个匹配容器分支（列表载荷）继续。
        args = [a for a in (_args_of(annotation) or ()) if a is not type(None)]
        if len(args) == 1:
            _walk_schema_tree(args[0], payload, path, out)
            return
        if isinstance(payload, dict):
            sub_models = [a for a in args if isinstance(a, type) and issubclass(a, BaseModel)]
            if sub_models:
                best, best_overlap = None, -1
                for m in sub_models:
                    overlap = len(set(payload) & set(m.model_fields))
                    if overlap > best_overlap:
                        best, best_overlap = m, overlap
                _walk_model(best, payload, path, out)
            return
        if isinstance(payload, list):
            for a in args:
                if _annotation_origin(a) is list:
                    _walk_schema_tree(a, payload, path, out)
                    return
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(payload, dict):
            _walk_model(annotation, payload, path, out)


def _walk_model(model: type, payload: Dict[str, Any], path: str, out: List[FieldDisclosure]) -> None:
    known = set(model.model_fields)
    for k, v in payload.items():
        child_path = f"{path}.{k}" if path else k
        if k not in known:
            out.append(FieldDisclosure(path=child_path, kind="unknown", got=""))
            continue
        _walk_schema_tree(model.model_fields[k].annotation, v, child_path, out)


def _annotation_origin(annotation: Any) -> Any:
    return getattr(annotation, "__origin__", None)


def _args_of(annotation: Any) -> Optional[Tuple[Any, ...]]:
    args = getattr(annotation, "__args__", None)
    return tuple(args) if args else None


#: pydantic union 失败会在 loc 尾部追加成员类型标签（'int'/'float'/...）；
#: 多成员同一处失败会生成重复 loci —— 归一到基础路径再去重。
_UNION_TAG_SEGMENTS = {"int", "float", "str", "bool", "bytes", "none", "list", "dict", "tuple"}


def _format_loc(loc: Tuple[Any, ...]) -> str:
    """ValidationError loc → 稳定路径；保留索引（layers[3].visible）。"""
    parts: List[str] = []
    for p in loc:
        if isinstance(p, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{p}]"
            else:
                parts.append(f"[{p}]")
        else:
            parts.append(str(p))
    if len(parts) >= 2 and (
        parts[-1] in _UNION_TAG_SEGMENTS or parts[-1].startswith(("literal[", "function[", "is-instance", "union_tag"))
    ):
        parts = parts[:-1]
    return ".".join(parts) if parts else "<root>"


def _collect_invalid(payload: Any) -> List[FieldDisclosure]:
    from pydantic import ValidationError

    try:
        MapSpecDocument.model_validate(payload)
        return []
    except ValidationError as e:
        out: List[FieldDisclosure] = []
        seen = set()
        for err in e.errors():
            loc = _format_loc(tuple(err.get("loc", ())))
            if loc in seen:
                continue
            seen.add(loc)
            out.append(FieldDisclosure(path=loc, kind="invalid", got=err.get("type", "")))
        return out


def parse_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> MapSpecParseResult:
    """冷路径解析：canonical 保序拷贝 + 严格校验披露 + 显式迁移。

    - 永不抛异常（结构化结果）；``forward_version`` 由调用方决定拒绝
      （publication 链用 :class:`MapSpecSchemaError` 显式拒绝）。
    - document 恒为深拷贝；调用方改写不影响输入。
    """
    if not isinstance(payload, dict):
        raise MapSpecSchemaError(
            "mapspec_not_an_object",
            f"MapSpec payload must be an object, got {type(payload).__name__}",
        )
    original_version, version_missing = _version_of(payload)
    if version_missing:
        original_version = DEFAULT_VERSION
    forward = original_version not in KNOWN_VERSIONS
    path = None if forward else _find_upgrade_path(original_version, target_version)

    doc = _copy.deepcopy(payload)
    migrated = bool(path)
    if path:
        for fr, to in path:
            doc = _UPGRADERS[(fr, to)](doc)

    unknown: List[FieldDisclosure] = []
    _walk_model(MapSpecDocument, doc, "", unknown)
    invalid = _collect_invalid(doc)
    truncated = len(unknown) > MAX_DISCLOSURES_PER_KIND or len(invalid) > MAX_DISCLOSURES_PER_KIND
    unknown = unknown[:MAX_DISCLOSURES_PER_KIND]
    invalid = invalid[:MAX_DISCLOSURES_PER_KIND]

    return MapSpecParseResult(
        document=doc,
        original_version=original_version,
        effective_version=target_version if path else original_version,
        migrated=migrated,
        valid=not invalid,
        forward_version=forward,
        unknown_fields=unknown,
        invalid_fields=invalid,
        disclosures_truncated=truncated,
    )


def require_parseable_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> MapSpecParseResult:
    """publication 消费入口：forward version / 非对象 → 显式拒绝。"""
    result = parse_mapspec(payload, target_version=target_version)
    if result.forward_version:
        raise MapSpecSchemaError(
            "mapspec_forward_version",
            f"MapSpec version {result.original_version!r} is newer than supported "
            f"{sorted(KNOWN_VERSIONS)}; refusing to compile with wrong semantics",
            result,
        )
    return result


def canonicalize_mapspec(payload: Any, *, target_version: str = LATEST_VERSION) -> Dict[str, Any]:
    """canonical 形式 = 校验 + 迁移后的**保序深拷贝**。

    有意不使用 ``model_dump()``：dump 会按声明序重排键（声明字段先、
    extras 后）且受 lax 转换影响 —— canonical 合同是"输入序忠实拷贝"
    （R1-M1），golden 锁定 ``dumps(canonicalize(x)) == dumps(x)``。
    """
    return require_parseable_mapspec(payload, target_version=target_version).document


def dumps_canonical(document: Dict[str, Any]) -> str:
    """canonical JSON 序列化（稳定格式：紧凑分隔 + 不转义非 ASCII）。"""
    return _json.dumps(document, ensure_ascii=False, separators=(",", ":"))
