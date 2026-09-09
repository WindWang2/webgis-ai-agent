"""Data Provider Extension SDK（ADR-0104 / Wave 7）。

第三方把外部 GIS 系统接入为 data fabric 的 source type：实现（或复用）核心
``GeospatialDataSourceAdapter`` ABC，SDK 负责注册进**唯一**的
``AdapterRegistry``（app/services/data_fabric/registry.py），并强制：

- source type 命名空间隔离：canonical = ``<ns>_<type>``，alias 同前缀；
- adapter 类必须是 ``GeospatialDataSourceAdapter`` 的子类；
- 能力旗标显式声明（pushdown 协商的输入，缺省全 False 最保守）；
- 网络型 provider 必须声明 ``network`` 权限（manifest 层联动）；
- 凭据以 ``ConnectionProfile`` 传入，SDK 不提供任何「返回 secret 给
  LLM」的通道。

Provider API 对齐核心 ABC 的既有方法面：probe / capabilities /
list_datasets / describe / preview / query / health（sync 语义与核心一致，
V1 不新增流式/瓦片方法——见 limitations）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic
from ..permissions import Permission


@dataclass
class ProviderExtensionSpec:
    """一个扩展数据 provider 的声明。"""

    source_type: str
    description: str
    adapter_cls: Optional[type] = None
    aliases: Tuple[str, ...] = ()
    # pushdown 能力协商（与核心 AdapterSpec 同名字段；缺省最保守）。
    supports_bbox: bool = False
    supports_filter: bool = False
    supports_pagination: bool = False
    supports_datetime: bool = False
    supports_projection: bool = False
    is_raster_tile: bool = False
    notes: str = ""
    # SDK 层权限语义声明（host 校验其 ⊆ manifest.permissions）。
    requires_network: bool = True
    credentials_ref: Optional[str] = None

    def validate(self, declared_permissions: frozenset[str]) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        from .identifier import NAME_RE

        st = self.source_type
        if not NAME_RE.match(st or ""):
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"source_type {st!r} must be snake_case identifier"
                )
            )
        if self.requires_network and Permission.NETWORK not in declared_permissions:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"provider {st!r} requires network but manifest does not declare "
                    f"'{Permission.NETWORK}' permission",
                )
            )
        if self.adapter_cls is not None:
            from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter

            if not (isinstance(self.adapter_cls, type) and issubclass(self.adapter_cls, GeospatialDataSourceAdapter)):
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"provider {st!r}: adapter_cls must subclass GeospatialDataSourceAdapter",
                    )
                )
        else:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"provider {st!r}: adapter_cls is required"
                )
            )
        return diagnostics


# ── V2（ADR-0105 / Wave 10）：扩展能力协议（可选 mixin）──────────────────
#
# 核心 ``GeospatialDataSourceAdapter`` ABC 保持 7 个 sync 方法不变（核心
# 契约，归 Data Control Plane 所有）。这些协议是**扩展作者的可选 mixin**：
# 适配器额外实现即获得对应扩展能力；是否消费由调用方（data fabric 分发、
# 认证 harness、worker 通道）按探测结果决定。V2 内 data_fabric 对这些
# mixin 的分发接入是明确的 follow-up（接口边界见 ADR-0105）。


@dataclass(frozen=True)
class TilePayload:
    """一个栅格瓦片的传输形态（bytes + content type + 元信息）。"""

    data: bytes
    content_type: str = "image/png"
    extent: Optional[tuple[float, float, float, float]] = None
    metadata: Optional[dict[str, Any]] = None


class StreamingVectorProvider:
    """矢量流式分页协议：``stream_features(query, page_size) -> Iterator[dict]``。

    事件为 GeoJSON Feature dict；由实现方内部翻页，调用方按迭代消费
    （天然支持提前 close 的协作式取消）。
    """

    def stream_features(self, query: dict[str, Any], page_size: int = 500):  # pragma: no cover - 协议
        raise NotImplementedError


class TileProvider:
    """栅格瓦片协议：``get_tile(z, x, y, **params) -> TilePayload``。"""

    def get_tile(self, z: int, x: int, y: int, **params: Any) -> TilePayload:  # pragma: no cover - 协议
        raise NotImplementedError


class RasterWindowProvider:
    """栅格窗口协议：``get_raster_window(bbox, crs, width, height) -> dict``。

    返回 dict：``{"data_b64": str, "dtype": str, "shape": [h, w],
    "crs": str, "bbox": [...], "nodata": ...}``（小窗口分析取数用）。
    """

    def get_raster_window(
        self,
        bbox: tuple[float, float, float, float],
        crs: str,
        width: int,
        height: int,
    ) -> dict[str, Any]:  # pragma: no cover - 协议
        raise NotImplementedError


_EXTENDED_PROTOCOLS: tuple[tuple[str, type], ...] = (
    ("streaming_vector", StreamingVectorProvider),
    ("tiles", TileProvider),
    ("raster_window", RasterWindowProvider),
)


def extended_provider_capabilities(adapter: Any) -> list[str]:
    """探测适配器实现的扩展能力（确定性排序；供认证/审计/状态展示）。"""
    cls = adapter if isinstance(adapter, type) else type(adapter)
    return [name for name, protocol in _EXTENDED_PROTOCOLS if issubclass(cls, protocol)]
