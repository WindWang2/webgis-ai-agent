"""扩展平台版本契约（ADR-0104 / Wave 12）。

三个独立的版本轴：
- ``CORE_API_VERSION``：本仓库扩展平台宿主接口（ExtensionContext / 投影
  registrar 形状）的版本。扩展以 ``api_version`` 声明它针对的扩展 API，
  主版本不同即不兼容，次版本向下兼容（宿主次版本 >= 扩展声明次版本）。
- ``MANIFEST_SCHEMA_VERSION``：manifest JSON 自身的 schema 版本。宿主只
  认识 <= 当前值的版本，更高版本 fail closed（拒绝静默忽略新字段）。
- 核心版本：``minimum_core_version`` / ``maximum_core_version`` 表达对
  宿主整体发行版本的窗口要求，判定是纯函数、确定性、无 I/O。

任何兼容性判定失败都产生 typed diagnostic；不存在「默认兼容」路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# 宿主当前版本。破坏性变更（字段删除 / 语义改变）必须提升主版本，
# 并同步 docs/extension-platform/compatibility.md 的迁移矩阵。
CORE_API_VERSION = "1.0.0"
# 宿主整体发行版本（核心版本窗口判定的基准）。
CORE_RELEASE_VERSION = "0.1.3"
MANIFEST_SCHEMA_VERSION = 1

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_version(value: str) -> Optional[Tuple[int, int, int]]:
    """解析 X.Y[.Z]；非法返回 None（由调用方产出 typed diagnostic）。"""
    if not isinstance(value, str):
        return None
    m = _SEMVER_RE.match(value.strip())
    if not m:
        return None
    return (
        int(m.group("major")),
        int(m.group("minor")),
        int(m.group("patch") or 0),
    )


def is_version(value: str) -> bool:
    return parse_version(value) is not None


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    reason: Optional[str] = None


def check_extension_api_compatibility(api_version: str) -> CompatibilityResult:
    """扩展声明的 api_version 与宿主 EXTENSION API 的兼容判定。

    规则：主版本必须相等；次版本 <= 宿主次版本（宿主向后兼容次版本）。
    """
    ext = parse_version(api_version)
    if ext is None:
        return CompatibilityResult(False, f"invalid api_version {api_version!r}")
    host = parse_version(CORE_API_VERSION)
    assert host is not None
    if ext[0] != host[0]:
        return CompatibilityResult(
            False,
            f"api_version major {ext[0]} != host major {host[0]} "
            f"(host api_version={CORE_API_VERSION})",
        )
    if ext[1] > host[1]:
        return CompatibilityResult(
            False,
            f"api_version minor {ext[1]} newer than host minor {host[1]} "
            f"(host api_version={CORE_API_VERSION})",
        )
    return CompatibilityResult(True)


def check_core_version_window(
    minimum_core_version: str,
    maximum_core_version: Optional[str],
) -> CompatibilityResult:
    """核心版本窗口判定：[minimum_core_version, maximum_core_version)。

    上界采用排他语义（声明「下一个破坏性版本之前」），在
    docs/extension-platform/compatibility.md 固化。
    """
    lo = parse_version(minimum_core_version)
    if lo is None:
        return CompatibilityResult(False, f"invalid minimum_core_version {minimum_core_version!r}")
    core = parse_version(CORE_RELEASE_VERSION)
    assert core is not None
    if core < lo:
        return CompatibilityResult(
            False,
            f"core release {CORE_RELEASE_VERSION} older than "
            f"minimum_core_version {minimum_core_version}",
        )
    if maximum_core_version is not None:
        hi = parse_version(maximum_core_version)
        if hi is None:
            return CompatibilityResult(False, f"invalid maximum_core_version {maximum_core_version!r}")
        if core >= hi:
            return CompatibilityResult(
                False,
                f"core release {CORE_RELEASE_VERSION} >= exclusive "
                f"maximum_core_version {maximum_core_version}",
            )
    return CompatibilityResult(True)
