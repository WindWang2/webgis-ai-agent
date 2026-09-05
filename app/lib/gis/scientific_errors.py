"""Scientific Error Taxonomy —— 科学失败的类型化错误（VNext）。

设计约束：

- 每个错误携带稳定的 ``scientific_code``（机器可读，进 correction_hint /
  evidence）+ 人类解释；产品层永远不解析 Python traceback 来「解释科学」；
- 全部 subclass ``ValueError``：ToolRegistry.dispatch 既有错误映射
  （ValueError → std_error_response）逐位兼容，科学错误天然获得
  correction_hint 通道，不需要第二套 dispatch 改造；
- 不滥用：只有**科学性**失败（样本不足/CRS 非法/退化数据/病态系统）用
  这些类型；普通参数校验继续走 pydantic / ValueError。
"""
from __future__ import annotations

from typing import Optional


class ScientificError(ValueError):
    """科学失败的基类（subclass ValueError 以复用 dispatch 错误映射）。"""

    scientific_code = "SCIENTIFIC_ERROR"

    def __init__(self, detail: str, *, correction_hint: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.correction_hint = correction_hint or self._default_hint()

    def _default_hint(self) -> str:
        return ""

    def to_dict(self) -> dict:
        return {
            "scientific_code": self.scientific_code,
            "detail": self.detail,
            "correction_hint": self.correction_hint,
        }


class InsufficientSamples(ScientificError):
    """样本量低于方法学下限（如克里金 < 8 点、MK 趋势 < 4 期）。"""

    scientific_code = "INSUFFICIENT_SAMPLES"

    def _default_hint(self) -> str:
        return "add observations or choose a method valid at this sample size"


class InvalidCRS(ScientificError):
    """CRS 缺失/无法解析/与方法的度量假设冲突。"""

    scientific_code = "INVALID_CRS"

    def _default_hint(self) -> str:
        return "declare a known CRS (e.g. EPSG:4326) or reproject to a metric CRS"


class InvalidUnits(ScientificError):
    """参数单位与算法单位契约不一致。"""

    scientific_code = "INVALID_UNITS"

    def _default_hint(self) -> str:
        return "check parameter units against the algorithm parameter contract"


class MissingRequiredField(ScientificError):
    """缺少方法必需的字段（数值字段/时间字段/权重字段）。"""

    scientific_code = "MISSING_REQUIRED_FIELD"

    def _default_hint(self) -> str:
        return "provide the required field or pick a method without it"


class InvalidGeometry(ScientificError):
    """几何不可用（空/非法拓扑/维度不符）。"""

    scientific_code = "INVALID_GEOMETRY"

    def _default_hint(self) -> str:
        return "repair geometry (make_valid / remove empty) before analysis"


class DegenerateData(ScientificError):
    """数据退化：零方差 / 全重合点 / 空域 —— 统计量无意义。"""

    scientific_code = "DEGENERATE_DATA"

    def _default_hint(self) -> str:
        return "check the numeric field for constant values or coincident samples"


class UnsupportedBandSemantics(ScientificError):
    """波段语义不满足（缺 NIR/red 等角色，或 SAR 极化缺失）。"""

    scientific_code = "UNSUPPORTED_BAND_SEMANTICS"

    def _default_hint(self) -> str:
        return "map band roles explicitly (band_map) or use an index the data supports"


class DisconnectedNetwork(ScientificError):
    """网络不连通：目标不可达（不是错误路径，是结构事实）。"""

    scientific_code = "DISCONNECTED_NETWORK"

    def _default_hint(self) -> str:
        return "verify connectivity/snap tolerance, or report target as unreachable"


class IllConditionedSystem(ScientificError):
    """线性系统病态（克里金矩阵近奇异），稳定化后仍不可信。"""

    scientific_code = "ILL_CONDITIONED_SYSTEM"

    def _default_hint(self) -> str:
        return "reduce neighbors, deduplicate coincident samples, or switch method"


class NoValidObservations(ScientificError):
    """过滤/nodata 后无有效观测（诚实空结果，非静默成功）。"""

    scientific_code = "NO_VALID_OBSERVATIONS"

    def _default_hint(self) -> str:
        return "check nodata/valid_range settings or widen the analysis window"


class ScientificPreconditionFailed(ScientificError):
    """声明式科学前置条件未通过（resolver/planner 同款条件的运行时面）。"""

    scientific_code = "SCIENTIFIC_PRECONDITION_FAILED"

    def __init__(self, detail: str, *, precondition_id: str = "",
                 correction_hint: str = "") -> None:
        super().__init__(detail, correction_hint=correction_hint)
        self.precondition_id = precondition_id

    def to_dict(self) -> dict:
        out = super().to_dict()
        out["precondition_id"] = self.precondition_id
        return out


class UnsupportedMethod(ScientificError):
    """方法在当前数据/环境下不适用（不是没实现）。"""

    scientific_code = "UNSUPPORTED_METHOD"

    def _default_hint(self) -> str:
        return "choose a method appropriate for this data regime"


class ResourceScaleMismatch(ScientificError):
    """规模超出方法的安全内存/CPU 包络（先拒绝，不 OOM）。"""

    scientific_code = "RESOURCE_SCALE_MISMATCH"

    def __init__(self, detail: str, *, estimated: Optional[str] = None,
                 limit: Optional[str] = None, correction_hint: str = "") -> None:
        super().__init__(detail, correction_hint=correction_hint)
        self.estimated = estimated
        self.limit = limit

    def to_dict(self) -> dict:
        out = super().to_dict()
        out["estimated"] = self.estimated
        out["limit"] = self.limit
        return out
