"""Label Engine Foundation — 确定性制图标注布局引擎（Cartographic Design V4）.

与前端 MapLibre symbol 标注的关系：MapLibre 在 GPU 上逐帧做交互态标注，
其碰撞避让随缩放/相机实时变化、结果依赖渲染时序。本引擎**不替代**它 ——
交互面的标注仍完全由 MapLibre 承担。本引擎服务于两个纯确定性场景：

1. **导出画布 / SVG 的确定性标注**：离线渲染没有 MapLibre 的碰撞器，
   导出产物必须可复现、可审计；
2. **语义检查 ``carto.label.*`` 的确定性估计**：``collision_est`` 目前只做
   标注盒面积占比的粗估计，本引擎提供同口径的免费升级路径（真实盒碰撞、
   位移与抑制计数），且同样不做任何渲染。

确定性契约：同输入两次求解的 ``model_dump()`` **完全相等**（含浮点 ——
无随机数、无时间因子、无集合迭代序依赖；``sorted`` 为稳定排序，候选生成
顺序固定）。golden 测试可直接锁定输出。

算法（单遍贪心 + 均匀格网空间索引，同输入永远同输出）：

1. 门控（按 (priority 升序, id 字典序) 逐要素）：空文本 → ``empty_text``；
   zoom < min_zoom → ``below_min_zoom``（先判空文本后判 zoom）；
2. 候选生成：
   - point：8 方位 GIS 惯例序（``DECLUTTER_CANDIDATE_OFFSETS``，首选右上），
     offset = font_size × 0.75，角度 0，代价 = 序号；
   - line：沿线等弧长取 k 个锚点（shield 取线中点单候选），角度取所在线段
     方位角并 keep-upright 翻转到 [-90, 90]；同文本已放置锚点间距 <
     repeat_distance 的候选跳过；
   - polygon：质心 + representative_point 两个候选，角度 0；
3. 标签框估算 ``estimate_label_box``：宽按字符加权（CJK 1.0em / 其他
   0.6em）× font_size，高 = font_size × 1.2；
4. 放置循环：候选按 cost 升序（稳定），AABB 相交（均匀格网查询，自动格宽
   下为 3×3 邻域量级）+ 标签框完全落在视口内 → 放置并记入索引；
   - point 三段退让：全候选碰撞 → 右上 max_displacement 处再试一次 →
     仍碰撞则 callout（允许与已放置标签重叠，由引线消歧；仍要求完整在视口
     内，出视口才最终抑制）。三段式自洽的关键：callout 阶段放宽盒碰撞 ——
     否则它与第二段同位同判、永远不会触发；
   - line/polygon 不做位移退让：沿线 repeat 与第二内点是它们的疏散手段，
     把线标注推离线或面标注推离面在制图上无意义；
5. 输出：placements（placed + callout）、suppressed（含 reason）、warnings
   （抑制比例 >50% 时一条中文告警）与 stats。

抑制理由枚举：``empty_text`` / ``below_min_zoom`` / ``collision`` /
``repeat_distance``（同文本候选全部被 repeat_distance 跳过）/
``no_candidates``（几何退化无候选）/ ``label_too_long``（放置失败且文本
超过 ``LABEL_TOO_LONG_THRESHOLD`` 字符 —— 长文本的抑制根因是框过大，
如实标注而不是笼统报 collision；门控理由 empty_text/below_min_zoom 优先，
不被覆盖）。标签框锚定约定：point 标注 (x, y) 为标签框**左下角**（位移
方向语义自然），line/polygon 为标签框**中心**。

文本适配契约（W3，纯函数增量，不改变求解语义）：``fit_label_text`` 按
max_chars 截断加省略号、``wrap_label_text`` 按 CJK 宽度口径贪心断行，
``MAX_SVG_LABEL_CHARS`` 是导出孪生（Python SVG 编译器 / 前端 exporter）
共享的单标签字符上限。
"""
from __future__ import annotations

import math
from typing import Dict, List, Literal, Tuple

from pydantic import BaseModel
from shapely.geometry import Polygon

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

# CJK 字符占 1em、其余按 0.6em 估宽（与 semantic_checks collision_est 同口径）
_CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x3000, 0x303F),   # CJK 标点
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 统一表意文字
    (0xF900, 0xFAFF),   # CJK 兼容表意文字
    (0xFF00, 0xFFEF),   # 全角形式
)

Box = Tuple[float, float, float, float]  # AABB (x1, y1, x2, y2)

# ── 文本适配契约常量（W4 SVG 编译器 / W5 前端 exporter 共享口径）──────────
#: 单标签进入导出产物的最大字符数（Unicode code point 口径）。超过即由
#: ``fit_label_text`` 截断并发出 ``label_truncated`` 诊断。
MAX_SVG_LABEL_CHARS = 60
#: solve_labels 判定 ``label_too_long`` 抑制理由的文本长度阈值（code point）。
LABEL_TOO_LONG_THRESHOLD = 80


class LabelCandidate(BaseModel):
    """单条候选：锚点 + 沿线角度（度）+ 代价（越小越优先）。"""

    x: float
    y: float
    angle: float = 0.0
    cost: float = 0.0


class LabelFeature(BaseModel):
    """求解输入：一个待标注要素。

    geometry 约定：point 为 ``[[x, y]]``；line 为有序点列；polygon 为环点列
    （闭合与否不强求，shapely 自动闭合）。坐标可以是地图坐标或画布坐标 ——
    本引擎只做几何，无投影概念。
    """

    id: str
    text: str
    kind: Literal["point", "line", "polygon"]
    priority: int = 50              # 小值优先（与 layout solver 同约定）
    min_zoom: float = 0.0           # 当前 zoom < min_zoom 则不参与
    geometry: list[list[float]]
    font_size: float = 12.0
    max_displacement: float = 24.0  # 点标注最大位移（画布单位）
    allow_callout: bool = True      # 位移超限时是否允许引线标注（callout）
    shield: bool = False            # 盾牌牌匾标注（道路编号）


class LabelPlacement(BaseModel):
    """单条输出：标注落位 / 引线标注 / 抑制。"""

    feature_id: str
    x: float
    y: float
    angle: float
    status: Literal["placed", "callout", "suppressed"]
    leader: list[list[float]] | None = None  # callout 引线 [[锚点],[标签左下角]]
    reason: str = ""                          # placed 默认空；抑制时必填


class LabelSolution(BaseModel):
    """求解输出：placements 含 placed + callout，suppressed 单列。"""

    placements: list[LabelPlacement] = []
    suppressed: list[LabelPlacement] = []
    warnings: list[str] = []
    stats: Dict[str, int] = {}


class LabelEngineInput(BaseModel):
    """求解请求包。"""

    features: list[LabelFeature]
    viewport: list[float]           # [minx, miny, maxx, maxy]，标签框须完全落在其中
    zoom: float = 10.0
    repeat_distance: float = 200.0  # 线标注同文本重复的最小间距
    grid_cell: float = 0.0          # 空间索引格宽；0 = 自动（平均标签宽高 ×2）


# ── 标签框估算（导出：测试与语义检查共用同一口径）────────────────────────
def _is_cjk_char(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def estimate_label_box(text: str, font_size: float = 12.0) -> Tuple[float, float]:
    """估算标签框 ``(width, height)``（纯函数）。

    宽 = Σ 每字符 em 宽 × font_size（CJK 1.0em / 其他 0.6em，中文注记宽度
    约为字号本身）；高 = font_size × 1.2。与 ``carto.label.collision_est``
    的字宽口径一致。
    """
    if not text:
        return (0.0, font_size * 1.2)
    em_sum = sum(1.0 if _is_cjk_char(ch) else 0.6 for ch in text)
    return (em_sum * font_size, font_size * 1.2)


# ── 文本适配契约（W3：fit / wrap，纯函数，无随机无 locale）────────────────
def fit_label_text(
    text: str,
    *,
    max_chars: int = MAX_SVG_LABEL_CHARS,
    ellipsis: str = "…",
) -> Tuple[str, bool]:
    """把标签文本截到 ``max_chars`` 内，返回 ``(fitted, was_truncated)``。

    超 ``max_chars`` 时取前 ``max_chars - 1`` 个字符追加 ``ellipsis``（总长
    恰为 ``max_chars``）；未超则原样返回、不附加省略号。确定性：无随机、
    无 locale 依赖，同输入永远同输出。

    截断按 **Unicode code point** 计（Python ``len()``/切片语义，与 TS 侧
    ``String.prototype.slice`` 在 BMP 内一致；astral 代理对字符在 JS UTF-16
    下口径不同，属已知边界 —— 跨孪生 parity 用 W10 corpus 锁定）。
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

    行宽预算 = ``max_chars × 0.6em``（即 max_chars 个"窄字符"位的 em 总量，
    与 ``estimate_label_box`` 的 CJK 1.0em / 其他 0.6em 加权同口径）：一行
    恰容纳 max_chars 个窄字符，CJK 字符按 1/0.6 ≈ 1.67 个窄字符位计。
    贪心：逐字符累加，下一个字符越界即换行；已达 ``max_lines`` 仍放不下时
    最后行按宽度截断（无省略号 —— 需要省略号语义的调用方对末行自行接
    ``fit_label_text``）。宽度以 0.1em 整数单位累加（窄字符 6、CJK 10），
    避免浮点累加漂移破坏确定性边界。确定性：纯字符循环，同输入永远同输出。
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
        w = 10 if _is_cjk_char(ch) else 6
        if cur and cur_w + w > budget:
            if len(lines) == max_lines - 1:
                break  # 最后一行已就位：剩余内容按宽度截断
            lines.append("".join(cur))
            cur = []
            cur_w = 0.0
        cur.append(ch)
        cur_w += w
    lines.append("".join(cur))
    return lines


# ── 角度工具（确定性；keep-upright 结果落在 [-90, 90]）──────────────────
def _normalize_angle(deg: float) -> float:
    """归一化到 (-180, 180]。"""
    a = math.fmod(deg, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


def _keep_upright(deg: float) -> float:
    """keep-upright：角度 >90 或 <-90 时 +180 翻转（沿线标注不头朝下）。"""
    a = _normalize_angle(deg)
    if a > 90.0 or a < -90.0:
        a = _normalize_angle(a + 180.0)
    return a


# ── AABB 工具 ────────────────────────────────────────────────────────────
def _corner_box(x: float, y: float, w: float, h: float) -> Box:
    """(x, y) 为左下角的轴对齐盒（点标注，角度恒 0）。"""
    return (x, y, x + w, y + h)


def _centered_box(x: float, y: float, w: float, h: float, angle_deg: float) -> Box:
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


def _overlaps(a: Box, b: Box) -> bool:
    """严格重叠（贴边不算碰撞）。"""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside_viewport(box: Box, vp: List[float]) -> bool:
    """标签框须完整落在视口内（贴边允许）。"""
    return vp[0] <= box[0] and box[2] <= vp[2] and vp[1] <= box[1] and box[3] <= vp[3]


# ── 均匀格网空间索引（dict[cell] → 已放置框；插入序确定性）───────────────
class _GridIndex:
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
                    if _overlaps(box, other):
                        return True
        return False


# ── 候选生成（确定性）────────────────────────────────────────────────────
def _point_candidates(feat: LabelFeature) -> List[LabelCandidate]:
    """8 方位候选：dx, dy = ±offset（offset = font_size × 0.75），代价 = 序号。"""
    if not feat.geometry:
        return []
    offset = feat.font_size * 0.75
    px, py = float(feat.geometry[0][0]), float(feat.geometry[0][1])
    return [
        LabelCandidate(x=px + dx * offset, y=py + dy * offset, angle=0.0, cost=float(i))
        for i, (dx, dy) in enumerate(DECLUTTER_CANDIDATE_OFFSETS)
    ]


def _line_candidates(feat: LabelFeature, text_width: float) -> List[LabelCandidate]:
    """沿线等弧长锚点 + 段方位角 keep-upright；shield 取线中点单候选。"""
    pts = [(float(p[0]), float(p[1])) for p in feat.geometry]
    if len(pts) < 2:
        return []
    seg_len: List[float] = []
    cum: List[float] = [0.0]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        d = math.hypot(x2 - x1, y2 - y1)
        seg_len.append(d)
        cum.append(cum[-1] + d)
    total = cum[-1]
    if total <= 0.0:
        return []
    if feat.shield:
        stations = [total / 2.0]
    else:
        k = min(len(pts), max(1, int(total / (text_width * 1.5))))
        stations = [(i + 0.5) * total / k for i in range(k)]

    n_seg = len(seg_len)
    cands: List[LabelCandidate] = []
    for idx, s in enumerate(stations):
        j = 0
        while j < n_seg - 1 and cum[j + 1] <= s:
            j += 1
        while j < n_seg - 1 and seg_len[j] == 0.0:  # 跳过重复顶点的零长段
            j += 1
        d = seg_len[j]
        if d <= 0.0:
            continue
        t = min(max((s - cum[j]) / d, 0.0), 1.0)
        x1, y1 = pts[j]
        x2, y2 = pts[j + 1]
        cands.append(LabelCandidate(
            x=x1 + (x2 - x1) * t,
            y=y1 + (y2 - y1) * t,
            angle=_keep_upright(math.degrees(math.atan2(y2 - y1, x2 - x1))),
            cost=float(idx),
        ))
    return cands


def _polygon_candidates(feat: LabelFeature) -> List[LabelCandidate]:
    """质心 + representative_point 两个候选（后者保证落在环内）。"""
    if len(feat.geometry) < 3:
        return []
    try:
        poly = Polygon([(float(p[0]), float(p[1])) for p in feat.geometry])
        centroid = poly.centroid
        inside = poly.representative_point()
    except Exception:  # 自交/退化环：shapely 抛错则无候选（确定性）
        return []
    cands: List[LabelCandidate] = []
    if math.isfinite(centroid.x) and math.isfinite(centroid.y):
        cands.append(LabelCandidate(x=centroid.x, y=centroid.y, angle=0.0, cost=0.0))
    if math.isfinite(inside.x) and math.isfinite(inside.y):
        cands.append(LabelCandidate(x=inside.x, y=inside.y, angle=0.0, cost=1.0))
    return cands


def _box_for(feat: LabelFeature, cand: LabelCandidate, w: float, h: float) -> Box:
    if feat.kind == "point":
        return _corner_box(cand.x, cand.y, w, h)  # 点标注角度恒 0
    return _centered_box(cand.x, cand.y, w, h, cand.angle)


def _feature_anchor(feat: LabelFeature) -> Tuple[float, float]:
    """抑制记录用的锚点（首顶点；空几何回原点）。"""
    if feat.geometry:
        return (float(feat.geometry[0][0]), float(feat.geometry[0][1]))
    return (0.0, 0.0)


# ── 主求解（单遍贪心）────────────────────────────────────────────────────
def solve_labels(payload: LabelEngineInput) -> LabelSolution:
    """确定性标注布局求解（单遍；同输入两次求解 ``model_dump()`` 完全相等）。"""
    vp = payload.viewport
    ordered = sorted(payload.features, key=lambda f: (f.priority, f.id))

    # 1. 门控 + 候选生成（gate 顺序：空文本 → min_zoom）
    gated: List[Tuple[LabelFeature, float, float, List[LabelCandidate]]] = []
    suppressed: List[LabelPlacement] = []
    for f in ordered:
        anchor = _feature_anchor(f)
        if not f.text.strip():
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=anchor[0], y=anchor[1], angle=0.0,
                status="suppressed", reason="empty_text",
            ))
            continue
        if payload.zoom < f.min_zoom:
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=anchor[0], y=anchor[1], angle=0.0,
                status="suppressed", reason="below_min_zoom",
            ))
            continue
        w, h = estimate_label_box(f.text, f.font_size)
        if f.kind == "point":
            cands = _point_candidates(f)
        elif f.kind == "line":
            cands = _line_candidates(f, w)
        else:
            cands = _polygon_candidates(f)
        gated.append((f, w, h, cands))

    # 2. 格网：显式格宽优先；自动 = 平均标签最大边 ×2
    if payload.grid_cell > 0.0:
        cell = payload.grid_cell
    elif gated:
        avg_w = sum(bw for _, bw, _, _ in gated) / len(gated)
        avg_h = sum(bh for _, _, bh, _ in gated) / len(gated)
        cell = 2.0 * max(avg_w, avg_h)
    else:
        cell = 48.0
    grid = _GridIndex(cell)

    # 3. 放置循环（候选按 cost 升序，稳定排序）
    placements: List[LabelPlacement] = []
    anchors_by_text: Dict[str, List[Tuple[float, float]]] = {}
    cand_total = 0
    placed_n = 0
    callout_n = 0

    for f, w, h, cands in gated:
        cand_total += len(cands)
        sorted_cands = sorted(cands, key=lambda c: c.cost)
        px, py = _feature_anchor(f)

        prev_anchors = anchors_by_text.get(f.text, ()) if f.kind == "line" else ()
        repeat_blocked = 0
        chosen: Tuple[LabelCandidate, Box, str] | None = None
        for c in sorted_cands:
            if prev_anchors:
                dmin = min(math.hypot(c.x - ax, c.y - ay) for ax, ay in prev_anchors)
                if dmin < payload.repeat_distance:
                    repeat_blocked += 1
                    continue
            box = _box_for(f, c, w, h)
            if _inside_viewport(box, vp) and not grid.collides(box):
                chosen = (c, box, "placed")
                break

        # 点要素三段退让：全候选碰撞 → max_displacement 右上再试 → callout
        if chosen is None and f.kind == "point" and f.allow_callout and cands:
            sx = px + f.max_displacement
            sy = py + f.max_displacement
            box2 = _corner_box(sx, sy, w, h)
            if _inside_viewport(box2, vp):
                if not grid.collides(box2):
                    chosen = (LabelCandidate(x=sx, y=sy, angle=0.0,
                                             cost=float(len(sorted_cands))),
                              box2, "placed_max_displacement")
                else:
                    # callout：允许与已放置标签重叠（引线消歧），视口约束保留
                    chosen = (LabelCandidate(x=sx, y=sy, angle=0.0,
                                             cost=float(len(sorted_cands))),
                              box2, "callout")

        if chosen is None:
            if not cands:
                reason = "no_candidates"
            elif f.kind == "line" and repeat_blocked == len(sorted_cands):
                reason = "repeat_distance"
            else:
                reason = "collision"
            # W3：放置失败且文本超长者，抑制根因是标签框过大 —— 如实标注
            # label_too_long（门控理由 empty_text/below_min_zoom 在更早的
            # gate 分支已落地，不受此覆盖影响）。
            if len(f.text) > LABEL_TOO_LONG_THRESHOLD:
                reason = "label_too_long"
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=px, y=py, angle=0.0,
                status="suppressed", reason=reason,
            ))
            continue

        c, box, mode = chosen
        if mode == "callout":
            callout_n += 1
            placements.append(LabelPlacement(
                feature_id=f.id, x=c.x, y=c.y, angle=_normalize_angle(c.angle),
                status="callout", leader=[[px, py], [c.x, c.y]],
                reason="displacement_exceeded",
            ))
        else:
            placed_n += 1
            placements.append(LabelPlacement(
                feature_id=f.id, x=c.x, y=c.y, angle=_normalize_angle(c.angle),
                status="placed",
                reason=("max_displacement" if mode == "placed_max_displacement" else ""),
            ))
        grid.insert(box)
        if f.kind == "line":
            anchors_by_text.setdefault(f.text, []).append((c.x, c.y))

    # 4. 汇总
    total = len(payload.features)
    sup_n = len(suppressed)
    warnings: List[str] = []
    if total > 0 and sup_n / total > 0.5:
        warnings.append(
            f"标注抑制比例过高：{sup_n}/{total} 个标注未能放置（碰撞或视口限制），"
            "建议抽稀要素、缩小字号或增大画布"
        )
    return LabelSolution(
        placements=placements,
        suppressed=suppressed,
        warnings=warnings,
        stats={
            "total": total,
            "candidates": cand_total,
            "placed": placed_n,
            "callout": callout_n,
            "suppressed": sup_n,
        },
    )


__all__ = [
    "LabelCandidate",
    "LabelFeature",
    "LabelPlacement",
    "LabelSolution",
    "LabelEngineInput",
    "solve_labels",
    "estimate_label_box",
    "fit_label_text",
    "wrap_label_text",
    "DECLUTTER_CANDIDATE_OFFSETS",
    "MAX_SVG_LABEL_CHARS",
    "LABEL_TOO_LONG_THRESHOLD",
]
