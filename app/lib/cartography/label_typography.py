"""Label Typography — 标注排版公共原语（V11 W0.2，ADR-0160）。

``label_engine``（harness 工具链：shapely 候选 + 退让 + callout）与
``label_collision``（导出孪生便携子集：单锚点 + 预算抑制）在 V10 各自带
了一份同语义的排版原语（8 组重复：CJK 判定 / 标签框估算 / 角度归一与
keep-upright / AABB 判定 / 盒构造 / 均匀格网 / 8 方位候选表 / 文本常量）。
本模块把它们收敛为**单一实现**，两个旧模块改为薄适配层（公共名 re-export，
行为零变化；golden corpus 与既有测试不动）。

**keep-upright 的两种语义（诚实登记，W4 C3 统一前不合并）**：

- :func:`keep_upright`（引擎语义）：归一 → 翻转 → 再归一，输出恒落
  ``(-90, 90]``。``label_engine.solve_labels`` 的沿线标注用它保证
  「标注不头朝下」的输出不变量。
- :func:`keep_upright_export_twin`（导出孪生语义）：翻转后只做一次
  ``fmod(…, 360)``。与 TS 孪生 ``frontend/lib/mapspec-compiler/
  label-solver.ts`` 的 ``keepUpright`` 逐分支等价，并被 parity corpus
  **冻结**（``tests/cartography/golden_corpus/label_collision/
  line_keep_upright.json``：angle 135 → 315.0）。改它必须与 TS 同步改
  （W4 的 C3 LabelPlan 波次），单侧改动会打破跨孪生 parity。

两语义在调用方实际传入域（已被上游 upright 过的角度）上不可区分；
分歧域 (90°, 180°] 由上述冻结语料锁定，属已知且受控的差异。

确定性契约：纯函数、无随机、无 locale；同输入恒同输出。
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

# ── 点标注 8 方位候选序（GIS 惯例：右上最优，代价 = 序号）────────────────
DECLUTTER_CANDIDATE_OFFSETS: Tuple[Tuple[float, float], ...] = (
    (1.0, 1.0),    # 右上（首选 —— Imhof/ESRI 惯例最优位）
    (1.0, 0.0),    # 正右
    (1.0, -1.0),   # 右下
    (0.0, -1.0),   # 正下
    (-1.0, -1.0),  # 左下
    (-1.0, 0.0),   # 正左
    (-1.0, 1.0),   # 左上
    (0.0, 1.0),    # 正上
)

#: CJK 字符占 1em、其余按 0.6em 估宽（与 semantic_checks collision_est 同口径）
CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x3000, 0x303F),   # CJK 标点
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 统一表意文字
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
    (0xFF00, 0xFFEF),   # 全角形式
)

#: AABB (x1, y1, x2, y2)
Box = Tuple[float, float, float, float]

# ── 文本适配契约常量（W4 SVG 编译器 / W5 前端 exporter 共享口径）──────────
#: 单标签进入导出产物的最大字符数（Unicode code point 口径）。超过即由
#: :func:`fit_label_text` 截断并发出 ``label_truncated`` 诊断。
MAX_SVG_LABEL_CHARS = 60
#: solve_labels 判定 ``label_too_long`` 抑制理由的文本长度阈值（code point）。
LABEL_TOO_LONG_THRESHOLD = 80


def is_cjk(ch: str) -> bool:
    """单字符是否 CJK（按 :data:`CJK_RANGES` 五段码位表）。"""
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in CJK_RANGES)


def estimate_label_box(text: str, font_size: float = 12.0) -> Tuple[float, float]:
    """估算标签框 ``(width, height)``（纯函数）。

    宽 = Σ 每字符 em 宽 × font_size（CJK 1.0em / 其他 0.6em，中文注记宽度
    约为字号本身）；高 = font_size × 1.2。与 ``carto.label.collision_est``
    的字宽口径一致。
    """
    if not text:
        return (0.0, font_size * 1.2)
    em_sum = sum(1.0 if is_cjk(ch) else 0.6 for ch in text)
    return (em_sum * font_size, font_size * 1.2)


def fit_label_text(
    text: str,
    *,
    max_chars: int = MAX_SVG_LABEL_CHARS,
    ellipsis: str = "…",
) -> Tuple[str, bool]:
    """把标签文本截到 ``max_chars`` 内，返回 ``(fitted, was_truncated)``。

    超 ``max_chars`` 时取前 ``max_chars - 1`` 个字符追加 ``ellipsis``（总长
    恰为 ``max_chars``）；未超则原样返回、不附加省略号。截断按 **Unicode
    code point** 计（Python ``len()``/切片语义）。
    """
    s = text if isinstance(text, str) else str(text)
    if max_chars < 1:
        max_chars = 1
    if len(s) <= max_chars:
        return s, False
    return s[: max_chars - 1] + ellipsis, True


def wrap_label_text(
    text: str,
    *,
    max_chars: int = MAX_SVG_LABEL_CHARS,
    max_lines: int = 2,
) -> List[str]:
    """按 CJK 宽度口径把标签文本贪心断行，返回行列表（1..max_lines 行）。

    行宽预算 = ``max_chars × 0.6em``：一行恰容纳 max_chars 个窄字符，CJK
    字符按 1/0.6 ≈ 1.67 个窄字符位计。宽度以 0.1em 整数单位累加（窄字符
    6、CJK 10），避免浮点累加漂移破坏确定性边界。已达 ``max_lines`` 仍放
    不下时最后行按宽度截断（无省略号 —— 需要省略号语义的调用方对末行自行
    接 :func:`fit_label_text`）。
    """
    s = text if isinstance(text, str) else str(text)
    if max_lines < 1:
        max_lines = 1
    if max_chars < 1:
        max_chars = 1
    budget = max_chars * 6  # 0.1em 单位：max_chars 个窄字符位

    lines: List[str] = []
    cur: List[str] = []
    cur_w = 0
    for ch in s:
        w = 10 if is_cjk(ch) else 6
        if cur and cur_w + w > budget:
            if len(lines) == max_lines - 1:
                break  # 最后一行已就位：剩余内容按宽度截断
            lines.append("".join(cur))
            cur = []
            cur_w = 0
        cur.append(ch)
        cur_w += w
    lines.append("".join(cur))
    return lines


# ── 角度工具（确定性）────────────────────────────────────────────────────
def normalize_angle(deg: float) -> float:
    """归一化到 (-180, 180]。"""
    a = math.fmod(deg, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


def keep_upright(deg: float) -> float:
    """keep-upright（引擎语义）：输出恒落 ``(-90, 90]``（沿线标注不头朝下）。

    归一 → 越出 ±90 则 +180 翻转 → 再归一。
    """
    a = normalize_angle(deg)
    if a > 90.0 or a < -90.0:
        a = normalize_angle(a + 180.0)
    return a


def keep_upright_export_twin(deg: float) -> float:
    """keep-upright（导出孪生语义）：与 TS ``keepUpright`` 逐分支等价。

    翻转后只做一次 ``fmod(a + 180, 360)`` —— 输出可能落在 (180, 360]
    （如 135° → 315.0）。该行为被 parity corpus 冻结，详见模块 docstring；
    仅 ``label_collision.solve_export_labels``（Python/TS 双端同表驱动）
    使用。
    """
    a = math.fmod(deg, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    if a > 90.0 or a < -90.0:
        a = math.fmod(a + 180.0, 360.0)
    return a


# ── AABB 工具 ────────────────────────────────────────────────────────────
def corner_box(x: float, y: float, w: float, h: float) -> Box:
    """(x, y) 为左下角的轴对齐盒（点标注，角度恒 0）。"""
    return (x, y, x + w, y + h)


def centered_box(x: float, y: float, w: float, h: float, angle_deg: float) -> Box:
    """(x, y) 为中心的盒，按角度旋转四个角后取 AABB（线/面标注）。"""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    hw, hh = w / 2.0, h / 2.0
    xs: List[float] = []
    ys: List[float] = []
    for sx in (-hw, hw):
        for sy in (-hh, hh):
            xs.append(x + sx * c - sy * s)
            ys.append(y + sx * s + sy * c)
    return (min(xs), min(ys), max(xs), max(ys))


def overlaps(a: Box, b: Box) -> bool:
    """严格重叠（贴边不算碰撞）。"""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside_viewport(box: Box, vp: List[float]) -> bool:
    """标签框须完整落在视口内（贴边允许）。"""
    return vp[0] <= box[0] and box[2] <= vp[2] and vp[1] <= box[1] and box[3] <= vp[3]


# ── 均匀格网空间索引（dict[cell] → 已放置框；插入序确定性）───────────────
class LabelGrid:
    """按格宽切片的 AABB 索引：查询时只遍历候选框覆盖的格子。

    自动格宽 = 平均标签最大边 ×2，典型标签每维跨 1–2 格、邻域 3×3 量级；
    格宽被调用方调小则退化为精确范围查询（正确性不受影响）。
    """

    def __init__(self, cell: float) -> None:
        self.cell = cell if cell > 0.0 else 1e-6
        self._cells: Dict[Tuple[int, int], List[Box]] = {}

    def _span(self, box: Box) -> Tuple[int, int, int, int]:
        return (
            math.floor(box[0] / self.cell), math.floor(box[2] / self.cell),
            math.floor(box[1] / self.cell), math.floor(box[3] / self.cell),
        )

    def insert(self, box: Box) -> None:
        cx0, cx1, cy0, cy1 = self._span(box)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                self._cells.setdefault((cx, cy), []).append(box)

    def collides(self, box: Box) -> bool:
        cx0, cx1, cy0, cy1 = self._span(box)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for other in self._cells.get((cx, cy), ()):
                    if overlaps(box, other):
                        return True
        return False


__all__ = [
    "wrap_label_multilingual",
    "polygon_label_point",
    "DECLUTTER_CANDIDATE_OFFSETS",
    "CJK_RANGES",
    "Box",
    "MAX_SVG_LABEL_CHARS",
    "LABEL_TOO_LONG_THRESHOLD",
    "is_cjk",
    "estimate_label_box",
    "fit_label_text",
    "wrap_label_text",
    "normalize_angle",
    "keep_upright",
    "keep_upright_export_twin",
    "corner_box",
    "centered_box",
    "overlaps",
    "inside_viewport",
    "LabelGrid",
]


# ── W4.3 专业排版（ADR-0164）：多语言断行 + 面内标注点 ───────────────────

def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if is_cjk(ch)) / len(text)


def wrap_label_multilingual(
    text: str,
    *,
    max_chars: int = 25,
    max_lines: int = 2,
    wrap_mode: str = "auto",
) -> List[str]:
    """多语言断行（W4.3）：CJK 按字、拉丁按词、auto 按占比选。

    - ``cjk_char``：逐字符贪心（与 :func:`wrap_label_text` 同宽度口径，
      CJK 1em / 其他 0.6em）；任意位置可断 —— 中文词间无空格，按字断是
      GIS 标注惯例；
    - ``latin_word``：**词边界断行**（空格分割，单超长词再按字符兜底）；
      拉丁文按字断词是排版错误；
    - ``auto``：CJK 占比 ≥0.3 → cjk_char，否则 latin_word（中英混排以
      主导文字决定断行策略，用例锁定）。

    确定性：纯函数，同输入恒同输出。返回 1..max_lines 行。
    """
    s = text if isinstance(text, str) else str(text)
    if max_lines < 1:
        max_lines = 1
    if max_chars < 1:
        max_chars = 1
    mode = wrap_mode
    if mode == "auto":
        mode = "cjk_char" if _cjk_ratio(s) >= 0.3 else "latin_word"
    if mode == "cjk_char":
        return wrap_label_text(s, max_chars=max_chars, max_lines=max_lines)

    # latin_word：词边界贪心（三步；诚实语义 = 溢出**可见截断**，不静默
    # 丢词：截断点之后的内容由调用方按 max_lines 预算预期 —— 与 CJK
    # 分支的末行宽截断同规）：
    #   1) 分词；超预算词按字符硬断为原子单元（长 URL/编号不断行更糟）；
    #   2) 单元贪心装箱（" " 连接，行预算 max_chars 窄字符位）；
    #   3) 超出 max_lines 时尾部行合并并截断进末行（可见截断）。
    units: List[str] = []
    for word in s.split(" "):
        while len(word) > max_chars:
            units.append(word[:max_chars])
            word = word[max_chars:]
        if word:
            units.append(word)
    packed: List[str] = []
    cur = ""
    for unit in units:
        candidate = unit if not cur else f"{cur} {unit}"
        if len(candidate) <= max_chars or not cur:
            cur = candidate[:max_chars] if len(candidate) > max_chars else candidate
        else:
            packed.append(cur)
            cur = unit[:max_chars] if len(unit) > max_chars else unit
    if cur:
        packed.append(cur)
    if len(packed) <= max_lines:
        return packed
    tail = " ".join(packed[max_lines - 1:])[:max_chars]
    return packed[: max_lines - 1] + [tail]


def polygon_label_point(ring: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """面内标注点（W4.3）：最大内接圆圆心（visually stable），质心兜底。

    ``shapely.maximum_inscribed_circle``（2.1）：圆心对不规则面（狭长/
    L 形）显著优于质心（质心可能落在面外）；退化/无效环回退
    ``representative_point``（保证在面内）。确定性。
    """
    from shapely.geometry import Polygon

    try:
        poly = Polygon([(float(x), float(y)) for x, y in ring])
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
    if poly.is_empty or not poly.is_valid:
        try:
            pt = poly.representative_point()
            return (round(pt.x, 6), round(pt.y, 6))
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)
    try:
        import shapely

        mic = shapely.maximum_inscribed_circle(poly, 0.01)
        # 2.x 返回两点 LineString：首点 = 内接圆圆心
        center = mic.coords[0]
        return (round(center[0], 6), round(center[1], 6))
    except Exception:  # noqa: BLE001 —— 几何退化时回退 representative_point
        try:
            pt = poly.representative_point()
            return (round(pt.x, 6), round(pt.y, 6))
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)
