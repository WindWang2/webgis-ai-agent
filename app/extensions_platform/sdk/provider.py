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
from typing import Optional, Tuple

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
