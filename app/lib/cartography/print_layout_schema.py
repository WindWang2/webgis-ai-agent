"""G34 — 纸面版式作者模型（``print_layout.v1``）。

单一权威承载位 = ``MapSpec.layout.print_layout``（additive）。本模块只定义
**作者决定**（毫米纸面几何、受控字体、文本框、图例排版、模板、页册策划），
不定义第二套 MapSpec store / undo 栈 / frame 系统 / 证据库。

设计纪律（ADR-0290）：

* 关闭词表 + ``extra="forbid"``：未知字段/未知版本/未知 kind 一律拒绝，
  不把旧文档静默解释成新语义。
* 长度/数量/数值全部有界；组件上限沿用 C2 的 32，页上限沿用 atlas 既有帽。
* ``PrintComponent.id`` 复用 ``MapSpecComponent.id``；``PrintPage.page_id``
  复用 ``AtlasPageBinding.page_id``；``locked`` 只是
  ``mapspec.workbench.lockedComponentIds`` 的**只读投影**，不是第二锁位。
* 旧 spec 无 ``layout.print_layout`` → 所有既有导出路径与字节完全不变。

单位：纸张/框 = 毫米；字号/行距/字距 = pt；IR/浏览器设计面 = 逻辑 px
（``PX_PER_MM = 4``）；SVG viewBox / 采样 px = DPI 派生。换算单一事实在
``app/services/print_layout_authoring/units.py``（本模块不重复定义）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── 版本与预算 ────────────────────────────────────────────────────────────

PRINT_LAYOUT_SCHEMA_VERSION = "print_layout.v1"
PRINT_RENDER_PATH = "publication_ir_svg_v1"

#: C2 LayoutIR v2 的组件帽（layout_description.validate_layout_ir: components ≤32）。
MAX_PRINT_COMPONENTS = 32
#: 与 publication_ir.MAX_PAGE_MM / atlas MAX_ATLAS_PAGES 同源；不抬高旧上限。
MAX_PAGE_MM = 1200.0
MAX_ATLAS_PAGES = 20
MAX_TEMPLATES = 8
MAX_TEXT_BYTES = 2048
MAX_TEMPLATE_BYTES = 16 * 1024
MAX_OVERRIDES_PER_PAGE = 32
MAX_SAFE_AREA_MM = 60.0
MAX_FONT_SIZE_PT = 96.0
MIN_FONT_SIZE_PT = 4.0
MAX_LEGEND_COLUMNS = 4
MAX_LEGEND_ENTRIES = 256
MAX_Z = 31

# 关闭词表：仅列部署中真实可加载、允许被引擎加载的字体资源 id。
# 真实文件/字形/weight 由 fonts.py 在运行时核验；缺席一律如实 unavailable。
FONT_IDS = Literal[
    "noto-sans-sc-embedded",   # vendored NotoSansSC-Regular-subset.ttf + @font-face
    "noto-sans-cjk-sc",        # 系统 Noto Sans CJK SC Regular
    "noto-serif-cjk-sc",       # 系统 Noto Serif CJK SC Regular
    "dejavu-sans",             # 系统 DejaVu Sans（拉丁基线）
]

COMPONENT_KINDS = Literal[
    "title", "subtitle", "legend", "scale_bar", "north_arrow",
    "attribution", "disclosure", "map_slot", "text_block",
    "page_number", "toc", "group",
]

TEXT_BINDINGS = Literal[
    "page_title", "page_number", "page_count", "data_time",
    "unit", "source_attribution", "map_title", "map_subtitle",
]

OVERFLOW_POLICIES = Literal["wrap", "expand", "continue", "block"]
WRAP_MODES = Literal["word", "char", "none"]
ALIGN_MODES = Literal["left", "center", "right"]
FONT_WEIGHTS = Literal["normal", "bold"]
FONT_STYLES = Literal["normal", "italic"]
MAP_FIT_MODES = Literal["contain", "crop"]
LEGEND_CONTINUATIONS = Literal["expand", "continue", "block"]
PAPER_PROFILES = Literal[
    "a4_portrait", "a4_landscape", "a3_portrait", "a3_landscape",
    "a2_landscape", "a1_landscape", "a0_landscape", "custom",
]


class _Closed(BaseModel):
    """关闭词表基类：未知键即拒绝（作者模型不接受「以后再说」的字段）。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _finite(v: float) -> bool:
    import math

    return isinstance(v, (int, float)) and math.isfinite(float(v))


# ── 纸张 / 组件几何 ────────────────────────────────────────────────────────


class PrintPaperSpec(_Closed):
    """纸张毫米尺寸。profile 词表复用 publication_ir.PAGE_PROFILES。"""

    profile: PAPER_PROFILES = "a4_landscape"
    width_mm: float = Field(gt=10.0, le=MAX_PAGE_MM)
    height_mm: float = Field(gt=10.0, le=MAX_PAGE_MM)
    orientation: Literal["landscape", "portrait"] = "landscape"

    @model_validator(mode="after")
    def _profile_matches_size(self) -> "PrintPaperSpec":
        # 与 publication_ir.PAGE_PROFILES 同一事实源（惰性 import 避免
        # mapspec_schema → print_layout_schema → publication_ir 的环）。
        if self.profile == "custom":
            return self
        from app.lib.cartography.publication_ir import PAGE_PROFILES

        want = PAGE_PROFILES.get(self.profile)
        if want is None:
            raise ValueError(f"unknown paper profile: {self.profile}")
        if abs(float(want[0]) - self.width_mm) > 0.01 or abs(float(want[1]) - self.height_mm) > 0.01:
            raise ValueError(
                f"paper {self.profile} is {want[0]}x{want[1]}mm, "
                f"got {self.width_mm}x{self.height_mm}mm"
            )
        return self


class PrintSafeArea(_Closed):
    top: float = Field(default=5.0, ge=0.0, le=MAX_SAFE_AREA_MM)
    right: float = Field(default=5.0, ge=0.0, le=MAX_SAFE_AREA_MM)
    bottom: float = Field(default=5.0, ge=0.0, le=MAX_SAFE_AREA_MM)
    left: float = Field(default=5.0, ge=0.0, le=MAX_SAFE_AREA_MM)


class PrintFrameMm(_Closed):
    """组件框（毫米，纸面左上原点，y 向下）。"""

    x: float = Field(ge=0.0, le=MAX_PAGE_MM)
    y: float = Field(ge=0.0, le=MAX_PAGE_MM)
    width: float = Field(gt=0.0, le=MAX_PAGE_MM)
    height: float = Field(gt=0.0, le=MAX_PAGE_MM)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


class PrintTypography(_Closed):
    """受控文字排版。font_id 是关闭词表；weight/style 缺失由 fonts.py 如实报告。"""

    font_id: FONT_IDS = "noto-sans-sc-embedded"
    size_pt: float = Field(default=12.0, ge=MIN_FONT_SIZE_PT, le=MAX_FONT_SIZE_PT)
    line_height: float = Field(default=1.35, ge=1.0, le=3.0)
    weight: FONT_WEIGHTS = "normal"
    style: FONT_STYLES = "normal"
    align: ALIGN_MODES = "left"
    letter_spacing_pt: float = Field(default=0.0, ge=-2.0, le=6.0)
    color: str = Field(default="#1e293b", min_length=4, max_length=32)
    wrap_mode: WRAP_MODES = "word"
    overflow_policy: OVERFLOW_POLICIES = "expand"

    @field_validator("color")
    @classmethod
    def _color_is_hex(cls, v: str) -> str:
        s = v.strip()
        if not (s.startswith("#") and len(s) in (4, 7, 9)
                and all(c in "0123456789abcdefABCDEF" for c in s[1:])):
            raise ValueError("color must be a #rgb/#rrggbb/#rrggbbaa hex literal")
        return s


class PrintTextSpec(_Closed):
    """固定文字或受控绑定；缺值保持可见占位，不猜地名/年份/许可。"""

    mode: Literal["literal", "binding"] = "literal"
    value: str = Field(default="", max_length=MAX_TEXT_BYTES)
    binding: TEXT_BINDINGS = "page_title"
    #: 关闭格式化函数集——无 eval / HTML 注入 / SQL。
    format: Literal["plain", "upper", "lower", "padded2"] = "plain"
    missing_placeholder: str = Field(default="〔数据缺失〕", max_length=64)

    @model_validator(mode="after")
    def _bounded_bytes(self) -> "PrintTextSpec":
        if len(self.value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"text exceeds {MAX_TEXT_BYTES} bytes")
        return self


class PrintLegendLayout(_Closed):
    """图例**纸面**排版；G33 继续拥有条目/颜色/断点/单位的内容语义。"""

    columns: int = Field(default=1, ge=1, le=MAX_LEGEND_COLUMNS)
    column_gap_mm: float = Field(default=4.0, ge=0.0, le=40.0)
    row_gap_mm: float = Field(default=1.5, ge=0.0, le=20.0)
    swatch_mm: float = Field(default=4.0, gt=0.0, le=30.0)
    symbol_mm: float = Field(default=6.0, gt=0.0, le=30.0)
    group_by_layer: bool = False
    continuation: LEGEND_CONTINUATIONS = "expand"
    title_wrap: bool = True


class PrintMapSlot(_Closed):
    """纸面地图槽：只引用既有 frame/product 或整幅当前地图。

    ``fit`` 决定新长宽比下的有效范围；``effective_extent`` 由编译器回填，
    用户不可直接伪造地理范围（组件框移动绝不改变数据或地理范围）。
    """

    frame_id: Optional[str] = Field(default=None, max_length=128)
    frame_index: Optional[int] = Field(default=None, ge=0, le=49)
    fit: MAP_FIT_MODES = "contain"
    effective_extent: Optional[List[float]] = Field(default=None, max_length=4, min_length=4)

    @field_validator("effective_extent")
    @classmethod
    def _extent_finite(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if len(v) != 4 or not all(_finite(x) for x in v):
            raise ValueError("effective_extent must be 4 finite numbers")
        return v


class PrintComponent(_Closed):
    """一个纸面组件。id 与 MapSpec.layout.components 里的 id 对齐（不新建身份）。"""

    id: str = Field(min_length=1, max_length=128)
    kind: COMPONENT_KINDS
    frame: PrintFrameMm
    z: int = Field(default=0, ge=0, le=MAX_Z)
    group_id: Optional[str] = Field(default=None, max_length=128)
    locked: bool = False          # 只读投影；唯一事实 = workbench.lockedComponentIds
    visible: bool = True
    typography: Optional[PrintTypography] = None
    text: Optional[PrintTextSpec] = None
    legend: Optional[PrintLegendLayout] = None
    map_slot: Optional[PrintMapSlot] = None

    @model_validator(mode="after")
    def _kind_consistency(self) -> "PrintComponent":
        if self.kind == "map_slot" and self.map_slot is None:
            raise ValueError("map_slot component requires map_slot spec")
        # legend 缺省时由 plan.legend_defaults 兜底（不在此处赋值，避免
        # validate_assignment 触发二次 model validator）。
        return self


class PrintSourceBinding(_Closed):
    """作者计划绑定的源修订（stale 检测；不复制整份 MapSpec）。"""

    mapspec_revision: int = Field(default=0, ge=0)
    style_revision: str = Field(default="", max_length=128)
    spec_fingerprint: str = Field(default="", max_length=256)


class PrintTemplate(_Closed):
    """有限版式配置模板：只含本领域版式参数 + 关闭占位符，不含脚本/临时路径。"""

    id: str = Field(min_length=3, max_length=128)
    version: int = Field(default=1, ge=1, le=9999)
    name: str = Field(default="", max_length=128)
    paper_profile: PAPER_PROFILES = "a4_landscape"
    safe_area: PrintSafeArea = Field(default_factory=PrintSafeArea)
    components: List[PrintComponent] = Field(default_factory=list, max_length=MAX_PRINT_COMPONENTS)
    legend_defaults: PrintLegendLayout = Field(default_factory=PrintLegendLayout)
    placeholders: List[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _bounded(self) -> "PrintTemplate":
        import json

        raw = json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_TEMPLATE_BYTES:
            raise ValueError(f"template exceeds {MAX_TEMPLATE_BYTES} bytes")
        return self


class PrintComponentOverride(_Closed):
    """页级覆写：只覆盖本领域字段（frame/typography/text/legend/visible）。"""

    component_id: str = Field(min_length=1, max_length=128)
    frame: Optional[PrintFrameMm] = None
    typography: Optional[PrintTypography] = None
    text: Optional[PrintTextSpec] = None
    legend: Optional[PrintLegendLayout] = None
    visible: Optional[bool] = None


class PrintPage(_Closed):
    """图册页册策划。page_id 复用既有 AtlasPageBinding.page_id，排序不换数据。"""

    page_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    order: int = Field(default=0, ge=0, le=999)
    title: str = Field(default="", max_length=MAX_TEXT_BYTES)
    #: 既有 planner 给出的原始页标题（只读）；复位只把 title 恢复到这里，
    #: 绝不触碰源图标题 / 人工标签 / 锁 / 图层可见性。
    original_title: str = Field(default="", max_length=MAX_TEXT_BYTES)
    paper_profile: Optional[PAPER_PROFILES] = None
    template_id: Optional[str] = Field(default=None, max_length=128)
    overrides: List[PrintComponentOverride] = Field(
        default_factory=list, max_length=MAX_OVERRIDES_PER_PAGE)
    #: 既有 planner 的绑定指纹（只读）；页标题不得指向另一份数据。
    binding_digest: str = Field(default="", max_length=256)


class PrintAtlasPlan(_Closed):
    pages: List[PrintPage] = Field(default_factory=list, max_length=MAX_ATLAS_PAGES)
    toc: bool = False
    page_number_start: int = Field(default=1, ge=1, le=9999)

    @model_validator(mode="after")
    def _unique_pages(self) -> "PrintAtlasPlan":
        ids = [p.page_id for p in self.pages]
        if len(ids) != len(set(ids)):
            raise ValueError("atlas page_id must be unique")
        return self


class PrintLayoutV1(_Closed):
    """作者计划根模型。未知版本必须被显式拒绝（见 validate_print_layout）。"""

    version: Literal["print_layout.v1"] = PRINT_LAYOUT_SCHEMA_VERSION
    paper: PrintPaperSpec = Field(default_factory=lambda: PrintPaperSpec(
        profile="a4_landscape", width_mm=297.0, height_mm=210.0))
    safe_area: PrintSafeArea = Field(default_factory=PrintSafeArea)
    source: PrintSourceBinding = Field(default_factory=PrintSourceBinding)
    font_asset_version: str = Field(default="", max_length=128)
    render_path: Literal["publication_ir_svg_v1"] = PRINT_RENDER_PATH
    template_id: Optional[str] = Field(default=None, max_length=128)
    template_version: int = Field(default=0, ge=0, le=9999)
    components: List[PrintComponent] = Field(default_factory=list, max_length=MAX_PRINT_COMPONENTS)
    legend_defaults: PrintLegendLayout = Field(default_factory=PrintLegendLayout)
    atlas: PrintAtlasPlan = Field(default_factory=PrintAtlasPlan)
    templates: List[PrintTemplate] = Field(default_factory=list, max_length=MAX_TEMPLATES)
    updated_at: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _unique_component_ids(self) -> "PrintLayoutV1":
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("print_layout component ids must be unique")
        return self


# ── 显式校验入口（关闭版本 / 页边界 / 安全区 / 组件框）───────────────────


class PrintLayoutIssue(_Closed):
    code: str
    message: str
    component_id: str = ""
    severity: Literal["error", "warning"] = "error"


class PrintLayoutValidation(_Closed):
    ok: bool
    version: str = ""
    issues: List[PrintLayoutIssue] = Field(default_factory=list)


def validate_print_layout(raw: Any) -> PrintLayoutValidation:
    """校验作者计划；未知版本/未知字段/越界返回 typed issues，绝不静默接受。"""
    if not isinstance(raw, dict):
        return PrintLayoutValidation(ok=False, issues=[
            PrintLayoutIssue(code="PRINT_LAYOUT_NOT_OBJECT", message="print_layout must be an object")])
    ver = raw.get("version")
    if ver != PRINT_LAYOUT_SCHEMA_VERSION:
        return PrintLayoutValidation(ok=False, version=str(ver or ""), issues=[
            PrintLayoutIssue(
                code="UNKNOWN_PRINT_LAYOUT_VERSION",
                message=f"unsupported print_layout version {ver!r}; expected {PRINT_LAYOUT_SCHEMA_VERSION}",
            )])
    try:
        plan = PrintLayoutV1.model_validate(raw)
    except Exception as ex:  # pydantic ValidationError + ValueError
        return PrintLayoutValidation(ok=False, version=str(ver), issues=[
            PrintLayoutIssue(code="PRINT_LAYOUT_INVALID", message=str(ex)[:512])])

    issues: List[PrintLayoutIssue] = []
    w, h = plan.paper.width_mm, plan.paper.height_mm
    sa = plan.safe_area
    inner_w, inner_h = w - sa.left - sa.right, h - sa.top - sa.bottom
    if inner_w <= 0 or inner_h <= 0:
        issues.append(PrintLayoutIssue(
            code="PRINT_LAYOUT_SAFE_AREA_EXCEEDS_PAPER",
            message="safe area leaves no printable area"))
    for c in plan.components:
        f = c.frame
        if f.right > w + 0.01 or f.bottom > h + 0.01:
            issues.append(PrintLayoutIssue(
                code="PRINT_LAYOUT_FRAME_OUTSIDE_PAPER",
                message=f"frame {f.x},{f.y},{f.width},{f.height} exceeds {w}x{h}mm",
                component_id=c.id))
        elif (f.x < sa.left - 0.01 or f.y < sa.top - 0.01
              or f.right > w - sa.right + 0.01 or f.bottom > h - sa.bottom + 0.01):
            issues.append(PrintLayoutIssue(
                code="PRINT_LAYOUT_FRAME_OUTSIDE_SAFE_AREA",
                message=f"frame breaches safe area (printable {inner_w}x{inner_h}mm)",
                component_id=c.id, severity="warning"))
    if len(plan.components) == 0:
        issues.append(PrintLayoutIssue(
            code="PRINT_LAYOUT_NO_COMPONENTS",
            message="authoring plan has no components; nothing would be authored",
            severity="warning"))
    if len([c for c in plan.components if c.kind == "map_slot"]) > 1:
        issues.append(PrintLayoutIssue(
            code="PRINT_LAYOUT_MULTIPLE_MAP_SLOTS",
            message="only one paper map slot is supported per page"))
    enabled = [p for p in plan.atlas.pages if p.enabled]
    if len(enabled) > MAX_ATLAS_PAGES:
        issues.append(PrintLayoutIssue(
            code="PRINT_LAYOUT_PAGE_BUDGET_EXCEEDED",
            message=f"{len(enabled)} enabled pages exceed {MAX_ATLAS_PAGES}"))
    return PrintLayoutValidation(
        ok=not any(i.severity == "error" for i in issues),
        version=plan.version, issues=issues)


def parse_print_layout(raw: Any, *, strict: bool = True) -> Optional[PrintLayoutV1]:
    """生产入口：严格模式下未知/非法计划抛 ValueError；宽松模式返回 None。"""
    verdict = validate_print_layout(raw)
    if not verdict.ok:
        if strict:
            codes = ",".join(i.code for i in verdict.issues if i.severity == "error")
            raise ValueError(f"invalid print_layout: {codes}")
        return None
    return PrintLayoutV1.model_validate(raw)


def empty_print_layout(paper_profile: str = "a4_landscape") -> Dict[str, Any]:
    """生成一个最小可用 A4 计划（供 UI 初始化 / 测试 fixture）。"""
    from app.lib.cartography.publication_ir import PAGE_PROFILES

    width, height = PAGE_PROFILES.get(paper_profile, PAGE_PROFILES["a4_landscape"])
    plan = PrintLayoutV1(
        paper=PrintPaperSpec(profile=paper_profile, width_mm=width, height_mm=height),
        components=[
            PrintComponent(id="title", kind="title",
                           frame=PrintFrameMm(x=15.0, y=10.0, width=180.0, height=18.0),
                           typography=PrintTypography(size_pt=18.0, weight="bold", align="center"),
                           text=PrintTextSpec(mode="binding", binding="map_title")),
            PrintComponent(id="map", kind="map_slot",
                           frame=PrintFrameMm(x=10.0, y=34.0, width=277.0, height=150.0),
                           map_slot=PrintMapSlot(fit="contain")),
            PrintComponent(id="legend", kind="legend",
                           frame=PrintFrameMm(x=15.0, y=186.0, width=110.0, height=14.0),
                           legend=PrintLegendLayout(columns=2)),
            PrintComponent(id="scale", kind="scale_bar",
                           frame=PrintFrameMm(x=210.0, y=186.0, width=72.0, height=12.0)),
            PrintComponent(id="attribution", kind="attribution",
                           frame=PrintFrameMm(x=15.0, y=198.0, width=180.0, height=5.0),
                           typography=PrintTypography(size_pt=7.0),
                           text=PrintTextSpec(mode="binding", binding="source_attribution")),
        ],
    )
    return plan.model_dump(mode="json", exclude_none=True)
