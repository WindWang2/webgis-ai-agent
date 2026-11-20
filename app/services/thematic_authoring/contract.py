"""G33 有版本的专题作者计划（WP02）。

关闭词表的作者操作模型：选择字段、自动分级、人工断点、类别顺序/颜色/
显示标签、连续/发散色域、比例符号/线宽映射、nodata 规则、恢复自动。
任何 paint JSON 都**不**直接暴露给模型——只有这份 typed 计划能进入
预览/提交服务；未知版本、未知操作、过长类表与异常颜色一律拒绝。

计划绑定（§三.1）：层/源身份在 service 层锚定（plan 只携带 layer_id 与
可选 source_anchor 用于防陈旧校验），统计范围与方法版本随回执披露。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.thematic_authoring.errors import ThematicAuthoringError

#: 计划合同版本。未知版本必须拒绝（不猜测降级）。
PLAN_SCHEMA_VERSION = 1

#: 数值类数硬帽（含人工断点；与现有工具面 k≤10 保持同量级并留余量）。
MAX_CLASSES = 12
#: 类别表长度硬帽（类别模式整表 + 分页由前端负责，服务端只收最终表）。
MAX_CATEGORIES = 60
#: 断点数组长度帽 = MAX_CLASSES + 1。
MAX_BREAKS = MAX_CLASSES + 1

#: 支持的自动分级方法（与 classify_values 词表一致；lisa 是固定科学类别，
#: 不进入作者数值分级词表——跨类型转换经适用性检查另行处理）。
GRADUATED_METHODS = ("quantiles", "equal_interval", "natural_breaks", "std_dev", "head_tail")

MODES = (
    "graduated_auto",     # 方法/k/色带交给既有符号化引擎或显式 method/k
    "graduated_manual",   # 人工断点（精确数字，服务端不自动排序）
    "categorical",        # 类别表编辑（键=源值，顺序只改呈现）
    "continuous",         # 连续色域（值域 + 色带）
    "divergent",          # 发散（中心 + 色带）
    "revert_auto",        # 清除本作者域覆盖，回到自动裁决
)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SIZE_CHANNELS = ("radius", "width")
SIZE_SCALINGS = ("linear", "area_sqrt")


def require_hex_color(value: str, *, field: str, index: Optional[int] = None) -> str:
    """颜色必须是 #rrggbb——异常颜色拒绝（§三/资源纪律）。"""
    if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
        raise ThematicAuthoringError(
            "INVALID_COLOR",
            f"{field} 颜色必须是 #rrggbb 十六进制，收到 {value!r}",
            field=field, index=index,
            correction_hint="使用 #rrggbb（如 #1f77b4）。",
        )
    return value.lower()


class SourceAnchor(BaseModel):
    """预览/提交时的源身份锚（防「预览用 A 源、应用时源已变」）。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1, max_length=128)
    content_revision: int = Field(ge=0)


class NodataRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str = Field(min_length=7, max_length=7)
    label: str = Field(min_length=1, max_length=32)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return require_hex_color(v, field="nodata.color")


class CategoryEdit(BaseModel):
    """类别编辑条目：key=源类别值（字符串形态），顺序=列表顺序（只改呈现）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=0, max_length=128)
    color: str = Field(min_length=7, max_length=7)
    label: str = Field(default="", max_length=64)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return require_hex_color(v, field="categories.color")


class CategoryOther(BaseModel):
    """显式 other 桶：未列出/未知值的颜色与图例文本（paint default）。"""

    model_config = ConfigDict(extra="forbid")

    color: str = Field(min_length=7, max_length=7)
    label: str = Field(min_length=0, max_length=64)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return require_hex_color(v, field="category_other.color")


class SizeChannel(BaseModel):
    """已有符号通道上的比例映射（WP06）。

    - radius：圆符号。linear=半径∝数值；area_sqrt=半径∝√数值（面积∝数值，
      精确函数非近似）——后者要求值域非负。
    - width：线宽（视觉量，恒 linear）。
    """

    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern=r"^(radius|width)$")
    field: str = Field(min_length=1, max_length=128)
    min_value: float
    max_value: float
    min_size: float = Field(gt=0)
    max_size: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "SizeChannel":
        if self.max_value <= self.min_value:
            raise ThematicAuthoringError(
                "INVALID_SIZE_DOMAIN",
                "size_channel 值域必须满足 min_value < max_value",
                field="size_channel",
                correction_hint="从分布摘要选择真实值域端点。",
            )
        if self.max_size < self.min_size:
            raise ThematicAuthoringError(
                "INVALID_SIZE_RANGE",
                "size_channel 尺寸必须满足 min_size ≤ max_size",
                field="size_channel",
            )
        # 面积语义要求非负值域（√ 负数不可用——不能把非正值强塞进对数/幂律）
        if self.scaling == "area_sqrt" and self.min_value < 0:
            raise ThematicAuthoringError(
                "INVALID_SIZE_SCALING",
                "area_sqrt（面积∝数值）要求值域非负；当前字段含负值",
                field="size_channel",
                correction_hint="改用 linear，或先确认字段的物理语义。",
            )
        if self.channel == "width" and self.scaling != "linear":
            raise ThematicAuthoringError(
                "INVALID_SIZE_SCALING",
                "线宽是视觉量，只支持 linear 缩放",
                field="size_channel",
            )
        if self.channel == "radius":
            if not (0.5 <= self.min_size <= 80.0) or not (0.5 <= self.max_size <= 80.0):
                raise ThematicAuthoringError(
                    "INVALID_SIZE_RANGE",
                    "radius 必须在 0.5–80px 内",
                    field="size_channel",
                )
        else:
            if not (0.1 <= self.min_size <= 25.0) or not (0.1 <= self.max_size <= 25.0):
                raise ThematicAuthoringError(
                    "INVALID_SIZE_RANGE",
                    "width 必须在 0.1–25px 内",
                    field="size_channel",
                )
        return self

    scaling: str = Field(default="linear", pattern=r"^(linear|area_sqrt)$")


class ThematicAuthoringPlan(BaseModel):
    """作者计划（typed，extra=forbid——任意 paint JSON 不得直通）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=PLAN_SCHEMA_VERSION)
    layer_id: str = Field(min_length=1, max_length=200)
    field: Optional[str] = Field(default=None, min_length=0, max_length=128)
    mode: str = Field(pattern=r"^(graduated_auto|graduated_manual|categorical|continuous|divergent|revert_auto)$")

    # ── graduated_auto ──
    method: Optional[str] = None
    k: Optional[int] = Field(default=None, ge=2, le=MAX_CLASSES)
    # ── graduated_manual ──
    breaks: Optional[List[float]] = None
    # ── 通用视觉 ──
    palette: Optional[str] = None
    invert_palette: bool = False
    # ── divergent ──
    divergent_center: Optional[float] = None
    # ── graduated 类级覆盖（长度=类数）──
    labels: Optional[List[str]] = None
    class_colors: Optional[List[str]] = None
    # ── categorical ──
    categories: Optional[List[CategoryEdit]] = None
    category_other: Optional[CategoryOther] = None
    # ── nodata / 文本语义 ──
    nodata: Optional[NodataRule] = None
    unit: Optional[str] = Field(default=None, max_length=24)
    title: Optional[str] = Field(default=None, max_length=120)
    # ── 大小/线宽映射 ──
    size_channel: Optional[SizeChannel] = None
    # ── 源锚（提交时防陈旧；缺省=以提交时当前源为准并回读校验）──
    source_anchor: Optional[SourceAnchor] = None

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != PLAN_SCHEMA_VERSION:
            raise ThematicAuthoringError(
                "UNKNOWN_PLAN_VERSION",
                f"不支持的作者计划版本 {v}（当前 {PLAN_SCHEMA_VERSION}）",
                status_code=400,
            )
        return v

    @field_validator("method")
    @classmethod
    def _method(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in GRADUATED_METHODS:
            raise ThematicAuthoringError(
                "UNKNOWN_METHOD",
                f"未知分类方法 {v!r}；可用：{', '.join(GRADUATED_METHODS)}",
                field="method",
            )
        return v

    @field_validator("class_colors")
    @classmethod
    def _class_colors(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if len(v) > MAX_CLASSES:
            raise ThematicAuthoringError(
                "CLASS_TABLE_TOO_LARGE",
                f"类级颜色最多 {MAX_CLASSES} 项，收到 {len(v)}",
                field="class_colors",
            )
        return [require_hex_color(c, field="class_colors", index=i) for i, c in enumerate(v)]

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if len(v) > MAX_CLASSES:
            raise ThematicAuthoringError(
                "CLASS_TABLE_TOO_LARGE",
                f"类级标签最多 {MAX_CLASSES} 项，收到 {len(v)}",
                field="labels",
            )
        return [str(s)[:64] for s in v]

    @field_validator("categories")
    @classmethod
    def _categories(cls, v: Optional[List[CategoryEdit]]) -> Optional[List[CategoryEdit]]:
        if v is None:
            return None
        if len(v) > MAX_CATEGORIES:
            raise ThematicAuthoringError(
                "CATEGORY_TABLE_TOO_LARGE",
                f"类别表最多 {MAX_CATEGORIES} 项，收到 {len(v)}",
                field="categories",
            )
        keys = [c.key for c in v]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ThematicAuthoringError(
                "DUPLICATE_CATEGORY_KEY",
                f"类别键重复：{dupes[:5]}（key 必须与源值一一对应）",
                field="categories",
            )
        return v

    @model_validator(mode="after")
    def _mode_shape(self) -> "ThematicAuthoringPlan":
        """模式形状校验：不属本模式的字段必须缺席（拒绝歧义计划）。"""
        mode = self.mode
        allowed: Dict[str, set] = {
            "graduated_auto": {"method", "k", "palette", "invert_palette", "labels", "class_colors", "unit", "title"},
            "graduated_manual": {"breaks", "palette", "invert_palette", "labels", "class_colors", "unit", "title"},
            "categorical": {"categories", "category_other", "palette", "unit", "title"},
            "continuous": {"palette", "invert_palette", "unit", "title"},
            "divergent": {"divergent_center", "palette", "invert_palette", "unit", "title"},
            "revert_auto": set(),
        }
        # 通用可选：field / source_anchor；除 revert_auto 外另有 size_channel/nodata。
        # revert_auto = 清除**整个** G33 作者域覆盖（分类 + 大小映射 + nodata），
        # 可预测地回到自动裁决；不删除其它人工样式（G16 标签等）。
        universal = {"field", "source_anchor"}
        if mode != "revert_auto":
            universal |= {"size_channel", "nodata"}
        extra = []
        for name in (
            "method", "k", "breaks", "palette", "invert_palette", "divergent_center",
            "labels", "class_colors", "categories", "category_other", "unit", "title",
        ):
            value = getattr(self, name)
            present = value is not None and value is not False
            if name == "invert_palette":
                present = bool(value)
            if present and name not in allowed.get(mode, set()) | universal:
                extra.append(name)
        if extra:
            raise ThematicAuthoringError(
                "MODE_FIELD_MISMATCH",
                f"模式 {mode} 不接受字段：{', '.join(sorted(extra))}",
                field=extra[0],
                correction_hint=f"{mode} 模式的可用字段：{', '.join(sorted(allowed[mode] | universal)) or '（仅通用字段）'}",
            )
        if mode == "graduated_manual":
            breaks = self.breaks or []
            if not (2 <= len(breaks) <= MAX_BREAKS):
                raise ThematicAuthoringError(
                    "INVALID_BREAKS",
                    f"人工断点需要 {2}–{MAX_BREAKS} 个有限数值，收到 {len(breaks)} 个",
                    field="breaks",
                )
            for i in range(1, len(breaks)):
                a, b = breaks[i - 1], breaks[i]
                if not (a < b):
                    raise ThematicAuthoringError(
                        "BREAKS_NOT_STRICTLY_INCREASING",
                        f"断点必须严格递增：第 {i - 1} 个 {a!r} ≥ 第 {i} 个 {b!r}",
                        field="breaks", index=i,
                        correction_hint="相等的断点会产生空类；倒序输入不会被自动排序。",
                    )
        if mode == "graduated_manual":
            n_classes = len(self.breaks or []) - 1
            if self.labels is not None and len(self.labels) != n_classes:
                raise ThematicAuthoringError(
                    "LABEL_COUNT_MISMATCH",
                    f"labels 长度 {len(self.labels)} ≠ 类数 {n_classes}",
                    field="labels",
                )
            if self.class_colors is not None and len(self.class_colors) != n_classes:
                raise ThematicAuthoringError(
                    "COLOR_COUNT_MISMATCH",
                    f"class_colors 长度 {len(self.class_colors)} ≠ 类数 {n_classes}",
                    field="class_colors",
                )
        if mode == "revert_auto":
            # revert = 清除整个 G33 作者域（分类 + 大小映射 + nodata）
            if self.size_channel is not None:
                raise ThematicAuthoringError(
                    "MODE_FIELD_MISMATCH",
                    "revert_auto 清除整个作者域覆盖（含大小映射）——不接受 size_channel",
                    field="size_channel",
                )
            if self.nodata is not None:
                raise ThematicAuthoringError(
                    "MODE_FIELD_MISMATCH",
                    "revert_auto 恢复 nodata 缺省——不接受 nodata 覆盖",
                    field="nodata",
                )
        if mode == "divergent" and self.divergent_center is None:
            raise ThematicAuthoringError(
                "MISSING_DIVERGENT_CENTER",
                "divergent 模式必须提供 divergent_center（且必须在合法值域内）",
                field="divergent_center",
            )
        if mode == "categorical" and not self.categories:
            raise ThematicAuthoringError(
                "EMPTY_CATEGORIES",
                "categorical 模式必须提供非空 categories 表",
                field="categories",
            )
        return self

    def plan_digest(self) -> str:
        """计划摘要指纹（回执/证据块用；内容寻址，非随机）。"""
        payload = json.dumps(
            self.model_dump(mode="json", exclude_none=True, exclude={"source_anchor"}),
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def parse_plan(request: Any) -> ThematicAuthoringPlan:
    """外部请求体 → typed 计划。形状错误 → 422 INVALID_PLAN（逐字段定位）。"""
    try:
        return ThematicAuthoringPlan.model_validate(request)
    except ThematicAuthoringError:
        raise
    except Exception as exc:  # pydantic ValidationError 等
        raise ThematicAuthoringError(
            "INVALID_PLAN",
            f"作者计划形状无效：{exc}",
            status_code=422,
            correction_hint="按 contracts 文档提供 plan 字段。",
        ) from exc
