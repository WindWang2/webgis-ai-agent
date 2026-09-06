"""Cartographic Theme & Palette Descriptors — 制图主题/调色板描述层（ADR-0101 D4）.

设计约束（不建立平行颜色事实）：

- 十六进制真值不搬家：thematic 色带唯一真值仍是 ``palettes.py``；UI chrome
  颜色唯一真值仍是前端 design tokens（globals.css 语义变量 / lib/theme.ts
  导出画布主题）。本模块只登记**描述与契约**：
  - ``PaletteDescriptor`` 引用 palette id，colorblind 标志承接
    ``PALETTE_KINDS``，print-safe 由灰度邻近亮度差推导计算；
  - ``CartographicThemeDescriptor`` 的 chrome 颜色一律是对前端语义 token
    名的**引用**（如 ``surface-panel`` / ``ink-primary``），不携带 hex；
  - typography / spacing / stroke 层级是结构性契约（数值可独立于颜色存在）。
- WCAG 对比度基础检查：提供 ``wcag_contrast_ratio`` 工具，供前端测试对
  globals.css 实测（前端测试锁定 AA）；后端校验主题引用完整性 + 灰度
  可分级性。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ── 灰度 / 亮度工具（print-safe 推导 + 对比度检查共用）─────────────────


def _srgb_channel_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG 相对亮度（#rrggbb；rgba() 取 rgb 部分并视为不透明近似）。"""
    raw = hex_color.strip()
    if raw.startswith("rgba"):
        raw = raw[raw.index("(") + 1: raw.rindex(")")]
        parts = [p.strip() for p in raw.split(",")]
        r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
    elif raw.startswith("#"):
        h = raw[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    else:
        raise ValueError(f"unsupported color format: {hex_color!r}")
    def lin(v: int) -> float:
        return _srgb_channel_to_linear(v / 255.0)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def wcag_contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x 对比度（1.0–21.0）。"""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def grayscale_luminance(hex_color: str) -> float:
    return relative_luminance(hex_color)


# ── PaletteDescriptor：引用 palettes.py id，不复制 hex ─────────────────

class PaletteDescriptor(BaseModel):
    """thematic 色带的目录描述（颜色值唯一真值在 palettes.py）。"""

    id: str                       # == COLOR_PALETTES / NATIVE_HEATMAP_COLORS key
    kind: Literal["sequential", "diverging", "qualitative", "perceptual_uniform", "native_heatmap"]
    colorblind_safe: bool = False  # 承接 PALETTE_KINDS（native_heatmap 无 kind 记录 → 显式）
    # ColorBrewer 的色盲安全按「色带 × 类数」标注（如 Set2/Dark2 仅 ≤3 类
    # 安全）—— 超过该类数使用时安全承诺失效，消费方必须按类数过滤。
    colorblind_safe_max_classes: int = 9
    max_recommended_classes: int = 7
    roles_zh: List[str] = Field(default_factory=list)
    note_zh: str = ""
    # 以下为推导字段（build 时计算，不手填）
    print_safe: bool = False
    min_gray_delta: float = 0.0
    gray_levels: int = 0          # 灰度可辨级数（≥0.06 邻距链）


def _gray_chain(grays: List[float], min_gap: float = 0.06) -> int:
    """灰度可辨级数：排序去重后相邻差 ≥ min_gap 的贪心链长度。"""
    levels: List[float] = []
    for g in sorted(grays):
        if not levels or g - levels[-1] >= min_gap:
            levels.append(g)
    return len(levels)


def _derive_print_safety(hexes: List[str]) -> tuple:
    """灰度可分级性（判据必须真实可失败，无 OR 兜底）：

    读者逐级读图时**相邻两级**必须可辨 —— 因此判据是 ramp 序上
    最小相邻灰度差 ≥ 0.06（严格；类别族 Set1 的相邻灰度差可低至
    0.001，必须判为不安全）。
    """
    grays = [grayscale_luminance(h) for h in hexes]
    deltas = [abs(grays[i + 1] - grays[i]) for i in range(len(grays) - 1)]
    min_delta = round(min(deltas), 4) if deltas else 0.0
    levels = _gray_chain(grays)
    return (min_delta >= 0.06), min_delta, levels


def build_palette_descriptors() -> List[PaletteDescriptor]:
    """从 palettes.py + PALETTE_KINDS 推导目录（确定性排序）。

    禁止在此处书写任何 hex —— 全部引用 ``COLOR_PALETTES`` /
    ``NATIVE_HEATMAP_COLORS``。
    """
    from app.lib.cartography.model_library import PALETTE_KINDS
    from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS

    # ColorBrewer 官方按「色带 × 类数」标注色盲安全：qualitative 族中
    # Set2/Dark2 仅 ≤3 类安全（n≥4 仅 Paired，未入库）；Qualitative 上限
    # 9（Set1/Pastel1）本身不承诺色盲安全。sequential/diverging 按
    # ColorBrewer 全域安全标注（YlOrRd/Blues/…/RdBu）与类数无关。
    CB_MAX_BY_ID = {"Set2": 3, "Dark2": 3, "Set1": 0, "Pastel1": 0}

    out: List[PaletteDescriptor] = []
    for pid in sorted(COLOR_PALETTES):
        kind_meta = PALETTE_KINDS.get(pid)
        cb_safe = bool(kind_meta.colorblind_safe) if kind_meta else False
        desc = PaletteDescriptor(
            id=pid,
            kind=kind_meta.kind if kind_meta else "sequential",
            colorblind_safe=cb_safe,
            colorblind_safe_max_classes=CB_MAX_BY_ID.get(pid, 9) if cb_safe else 0,
            note_zh=kind_meta.note_zh if kind_meta else "",
            roles_zh=(["counts", "density", "intensity"] if kind_meta and kind_meta.kind == "sequential" else []),
        )
        print_safe, min_delta, levels = _derive_print_safety(COLOR_PALETTES[pid])
        desc.print_safe = print_safe
        desc.min_gray_delta = min_delta
        desc.gray_levels = levels
        out.append(desc)
    for pid in sorted(NATIVE_HEATMAP_COLORS):
        out.append(PaletteDescriptor(
            id=pid, kind="native_heatmap",
            colorblind_safe=pid in ("viridis", "magma"),
            colorblind_safe_max_classes=0,   # 连续色带无类数概念
            note_zh="原生热力色带（首色透明；图例取不透明 6 段）",
        ))
    return out


# ── CartographicThemeDescriptor：结构 + token 引用 ──────────────────────

ThemeProfile = Literal["light", "dark", "print", "high_contrast"]


class TypographySpec(BaseModel):
    """结构化排版契约（字体族引用前端 token 名，不嵌字体内联）。"""

    font_family_token: str = "font-sans"        # 前端 tailwind font-family token
    mono_family_token: str = "font-mono"
    title_size_token: str = "text-title"
    presentation_title_size_token: str = "text-heading"
    body_size_token: str = "text-caption"
    micro_size_token: str = "text-micro"
    title_weight: int = 600
    report_title_weight: int = 700
    min_chrome_px: int = 11                     # chrome 最小可读字号（px）


class SpacingSpec(BaseModel):
    """chrome 结构间距（px；live 与 export 共用的布局契约值）。"""

    chrome_padding_px: int = 8
    slot_gap_px: int = 12
    stack_step_px: int = 36
    panel_radius_token: str = "rounded-chrome"
    border_width_px: int = 1
    border_width_academic_px: int = 2


class ChromeTokenRefs(BaseModel):
    """对前端语义 token 名的引用（颜色真值在前端 globals.css）。

    命名 = CSS 变量去 ``--`` 前缀（如 --surface-panel → surface-panel）。
    """

    surface: str = "surface-panel"
    surface_raised: str = "surface-raised"
    ink: str = "text-primary"
    ink_muted: str = "text-secondary"
    border: str = "map-chrome-border"
    map_chrome_bg: str = "map-chrome-bg"
    map_chrome_ink: str = "map-chrome-text"
    map_chrome_ink_muted: str = "map-chrome-text-muted"
    map_chrome_border: str = "map-chrome-border"


class PaletteRecommendation(BaseModel):
    """按色带语义族的推荐清单（引用 palette id，按优先序）。"""

    sequential: List[str] = Field(default_factory=list)
    diverging: List[str] = Field(default_factory=list)
    qualitative: List[str] = Field(default_factory=list)
    perceptual_uniform: List[str] = Field(default_factory=list)

    def ids(self) -> List[str]:
        return [*self.sequential, *self.diverging, *self.qualitative,
                *self.perceptual_uniform]


class CartographicThemeDescriptor(BaseModel):
    """一份制图主题 = 排版 + 间距 + chrome token 引用 + palette 推荐。"""

    id: str
    name_zh: str
    profile: ThemeProfile = "light"
    output_targets: List[str] = Field(
        default_factory=lambda: ["interactive", "png", "pdf", "svg"])
    layout_profiles: List[str] = Field(default_factory=list)  # 适配的组合版式
    typography: TypographySpec = Field(default_factory=TypographySpec)
    spacing: SpacingSpec = Field(default_factory=SpacingSpec)
    chrome: ChromeTokenRefs = Field(default_factory=ChromeTokenRefs)
    palettes: PaletteRecommendation = Field(default_factory=PaletteRecommendation)
    colorblind_safe_first: bool = False   # 是否优先推荐色盲安全色带
    notes_zh: List[str] = Field(default_factory=list)


SEED_THEMES: List[CartographicThemeDescriptor] = [
    CartographicThemeDescriptor(
        id="cartographic.light_interactive",
        name_zh="交互亮色主题",
        profile="light",
        layout_profiles=["minimal", "standard", "dense"],
        palettes=PaletteRecommendation(
            sequential=["YlOrRd", "Blues", "Greens", "Oranges"],
            diverging=["RdBu", "RdYlGn"],
            qualitative=["Set1", "Set2", "Dark2"],
            perceptual_uniform=["Viridis", "Magma", "Inferno", "Plasma"],
        ),
        colorblind_safe_first=False,
        notes_zh=["默认工作区主题；chrome 颜色全部引用前端语义 token"],
    ),
    CartographicThemeDescriptor(
        id="cartographic.dark_interactive",
        name_zh="交互暗色主题",
        profile="dark",
        layout_profiles=["minimal", "standard"],
        palettes=PaletteRecommendation(
            # 暗底上低亮度端（暗红/深蓝/深紫）不可辨 —— sequential 槽
            # 留空，推荐一律走感知均匀族（亮端可辨、感知均匀）。
            sequential=[],
            diverging=["RdBu"],
            qualitative=["Dark2", "Set2"],
            perceptual_uniform=["Viridis", "Magma", "Inferno", "Plasma"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "暗背景优先感知均匀族（Viridis 系）—— 若必须用 sequential 色带，"
            "应反转使用顺序（高值→低亮度端）并披露",
            "深色 chrome token（map-chrome-* dark 分支）",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.print_paper",
        name_zh="纸张印刷主题",
        profile="print",
        output_targets=["png", "pdf", "svg", "print"],
        layout_profiles=["academic", "report"],
        typography=TypographySpec(title_weight=700, min_chrome_px=9),
        spacing=SpacingSpec(stack_step_px=34),
        palettes=PaletteRecommendation(
            # 严格灰度判据（ramp 序 ΔL>=0.06）下的 print_safe 色带才可入列
            sequential=["YlOrRd", "Blues", "Greens", "Reds", "Oranges", "Purples"],
            diverging=["RdBu", "PuOr"],
            # qualitative 族无一通过灰度判据 —— 留空并在 note 披露：
            # 分类面黑白打印应以符号形状/标签区分，不依赖色相
            qualitative=[],
            perceptual_uniform=["Viridis"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "黑白打印安全：推荐清单全部 print_safe（灰度 ΔL 严格可分级）",
            "红绿色盲不友好的 RdYlGn 不进入推荐清单；发散用 RdBu/PuOr",
            "qualitative 色带灰度打印均不可分级 —— 类别面黑白输出改用"
            "符号形状/填充图案区分（映射由导出侧承担，planned）",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.high_contrast",
        name_zh="高对比无障碍主题",
        profile="high_contrast",
        palettes=PaletteRecommendation(
            sequential=["YlOrRd", "Blues"],
            diverging=["RdBu"],
            qualitative=["Dark2", "Set2"],
            perceptual_uniform=["Viridis", "Inferno"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "WCAG AA：chrome 前景/背景对 ≥4.5:1（前端测试对 globals.css 实测）",
            "状态不得仅靠颜色表达（图例同步符号形状/标签）",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.presentation_screen",
        name_zh="演示投屏主题",
        profile="dark",
        output_targets=["interactive", "png"],
        layout_profiles=["presentation"],
        typography=TypographySpec(
            title_weight=700,
            title_size_token="text-heading",
            min_chrome_px=12,
        ),
        spacing=SpacingSpec(stack_step_px=40, chrome_padding_px=10),
        palettes=PaletteRecommendation(
            sequential=["YlOrRd", "Blues"],
            diverging=["RdBu"],
            qualitative=["Set1", "Dark2"],
            perceptual_uniform=["Plasma", "Viridis"],
        ),
        notes_zh=["远距离可读：标题走 heading 级、chrome 最小 12px"],
    ),
    # ── V4（Design System）：领域主题族 ─────────────────────────────────
    CartographicThemeDescriptor(
        id="cartographic.scientific",
        name_zh="科学分析主题",
        profile="light",
        layout_profiles=["academic", "standard", "report"],
        typography=TypographySpec(title_weight=600, min_chrome_px=10),
        spacing=SpacingSpec(stack_step_px=36),
        palettes=PaletteRecommendation(
            sequential=["YlOrRd", "Blues", "Greens"],
            diverging=["RdBu", "PuOr"],
            qualitative=["Set2", "Dark2"],
            perceptual_uniform=["Viridis", "Magma", "Inferno", "Plasma"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "期刊/报告图：优先感知均匀与色盲安全色带；chrome 极简",
            "色带语义必须与统计口径一致（发散带以中点为中心）",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.publication",
        name_zh="出版印刷主题",
        profile="print",
        output_targets=["png", "pdf", "svg", "print"],
        layout_profiles=["academic", "report"],
        typography=TypographySpec(title_weight=700, min_chrome_px=9),
        spacing=SpacingSpec(stack_step_px=32),
        palettes=PaletteRecommendation(
            # print profile 校验：推荐清单全部 print_safe（灰度 ΔL 可分级）
            sequential=["YlOrRd", "Blues", "Greens", "Reds", "Oranges", "Purples"],
            diverging=["RdBu", "PuOr"],
            qualitative=[],
            perceptual_uniform=["Viridis"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "正式出版：灰度安全 + 色盲安全双约束；类别面黑白输出改用"
            "符号形状/填充图案区分",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.government",
        name_zh="政务报告主题",
        profile="light",
        layout_profiles=["report", "presentation"],
        typography=TypographySpec(title_weight=700, min_chrome_px=11),
        spacing=SpacingSpec(stack_step_px=38, chrome_padding_px=10),
        palettes=PaletteRecommendation(
            sequential=["Blues", "YlOrRd", "Greens"],
            diverging=["RdBu"],
            qualitative=["Pastel1", "Set2"],
            perceptual_uniform=["Viridis"],
        ),
        notes_zh=[
            "政务场景：大面积柔和填色（Pastel1）+ 高权重标题；"
            "地类/区划配色沿用规划惯例",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.remote_sensing",
        name_zh="遥感影像主题",
        profile="dark",
        layout_profiles=["standard", "dense"],
        palettes=PaletteRecommendation(
            sequential=[],
            diverging=["RdBu"],
            qualitative=["Dark2"],
            perceptual_uniform=["Viridis", "Magma", "Inferno", "Plasma"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "暗底影像判读：感知均匀族优先；合成影像不参与专题设色语义",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.terrain",
        name_zh="地形表达主题",
        profile="light",
        layout_profiles=["standard", "report", "academic"],
        palettes=PaletteRecommendation(
            sequential=["Oranges", "YlOrRd", "Greens", "Blues"],
            diverging=["RdBu"],
            qualitative=["Dark2"],
            perceptual_uniform=["Magma", "Viridis"],
        ),
        notes_zh=[
            "地形族：hypsometric 设色以 Oranges 近似；晕渲光源参数必须披露",
        ],
    ),
    CartographicThemeDescriptor(
        id="cartographic.risk_communication",
        name_zh="风险沟通主题",
        profile="light",
        layout_profiles=["report", "presentation"],
        palettes=PaletteRecommendation(
            sequential=["Reds", "YlOrRd", "Oranges"],
            diverging=["RdBu", "PuOr"],
            # colorblind_safe_first 校验：Set1 不安全 → Dark2（风险受众广，
            # 色盲读者占比不可忽略）
            qualitative=["Dark2", "Set2"],
            perceptual_uniform=["Inferno"],
        ),
        colorblind_safe_first=True,
        notes_zh=[
            "风险语义固定：高值=暖色深端；『无数据』不得画成『低风险』",
            "色盲安全优先（RdBu/PuOr 发散；禁 RdYlGn 表达风险方向）",
        ],
    ),
]


class CartographicThemeRegistry:
    """theme / palette 只读目录（确定性、无 I/O）。"""

    def __init__(self) -> None:
        self._themes: Dict[str, CartographicThemeDescriptor] = {}
        self._palettes: Dict[str, PaletteDescriptor] = {}
        self.load_builtins()

    def load_builtins(self) -> None:
        self._themes = {t.id: t for t in SEED_THEMES}
        self._palettes = {p.id: p for p in build_palette_descriptors()}

    # ── themes ──
    def get_theme(self, theme_id: str) -> Optional[CartographicThemeDescriptor]:
        return self._themes.get(theme_id)

    def themes(self) -> List[CartographicThemeDescriptor]:
        return sorted(self._themes.values(), key=lambda t: t.id)

    def themes_for_profile(self, profile: str) -> List[CartographicThemeDescriptor]:
        return [t for t in self.themes() if t.profile == profile]

    # ── palettes ──
    def get_palette(self, palette_id: str) -> Optional[PaletteDescriptor]:
        return self._palettes.get(palette_id)

    def palettes(self) -> List[PaletteDescriptor]:
        return sorted(self._palettes.values(), key=lambda p: p.id)

    def palettes_for_kind(self, kind: str) -> List[PaletteDescriptor]:
        return [p for p in self.palettes() if p.kind == kind]

    def colorblind_safe_ids(self) -> List[str]:
        return sorted(p.id for p in self._palettes.values() if p.colorblind_safe)

    def print_safe_ids(self) -> List[str]:
        return sorted(p.id for p in self._palettes.values() if p.print_safe)

    def recommend(self, theme_id: str, kind: str) -> List[str]:
        theme = self.get_theme(theme_id)
        if theme is None:
            return []
        rec = theme.palettes
        ids = {
            "sequential": rec.sequential,
            "diverging": rec.diverging,
            "qualitative": rec.qualitative,
            "perceptual_uniform": rec.perceptual_uniform,
        }.get(kind, [])
        # 推荐清单只含已注册色带（注册表是权威；主题引用陈旧 id 时收敛）
        return [i for i in ids if i in self._palettes]

    # ── 校验 ──
    def validate(self) -> List[str]:
        from app.lib.cartography.model_library import PALETTE_KINDS
        from app.lib.cartography.palettes import COLOR_PALETTES, NATIVE_HEATMAP_COLORS

        issues: List[str] = []
        known = set(COLOR_PALETTES) | set(NATIVE_HEATMAP_COLORS)
        for pid, p in self._palettes.items():
            if pid not in known:
                issues.append(f"palette descriptor '{pid}' 引用了未注册色带")
            if p.kind != "native_heatmap" and pid in COLOR_PALETTES:
                kind_meta = PALETTE_KINDS.get(pid)
                if kind_meta and bool(kind_meta.colorblind_safe) != p.colorblind_safe:
                    issues.append(f"palette '{pid}': colorblind_safe 与 PALETTE_KINDS 漂移")
        for theme in self._themes.values():
            for pid in theme.palettes.ids():
                if pid not in self._palettes:
                    issues.append(f"theme '{theme.id}' 推荐 '{pid}' 未注册")
                elif theme.profile == "print":
                    p = self._palettes[pid]
                    if not p.print_safe:
                        issues.append(
                            f"theme '{theme.id}'（print）推荐 '{pid}' 不是 print_safe")
            if theme.profile == "print" and not theme.colorblind_safe_first:
                issues.append(f"theme '{theme.id}': print 主题必须 colorblind_safe_first")
            if theme.colorblind_safe_first:
                for pid in theme.palettes.ids():
                    p = self._palettes.get(pid)
                    if p is not None and not p.colorblind_safe:
                        issues.append(
                            f"theme '{theme.id}'（colorblind_safe_first）推荐 "
                            f"'{pid}' 不是色盲安全色带")
        return issues


_registry: Optional[CartographicThemeRegistry] = None


def get_cartographic_theme_registry() -> CartographicThemeRegistry:
    global _registry
    if _registry is None:
        _registry = CartographicThemeRegistry()
    return _registry


def reset_cartographic_theme_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "PaletteDescriptor",
    "TypographySpec",
    "SpacingSpec",
    "ChromeTokenRefs",
    "PaletteRecommendation",
    "CartographicThemeDescriptor",
    "CartographicThemeRegistry",
    "SEED_THEMES",
    "get_cartographic_theme_registry",
    "reset_cartographic_theme_registry",
    "build_palette_descriptors",
    "relative_luminance",
    "wcag_contrast_ratio",
]
