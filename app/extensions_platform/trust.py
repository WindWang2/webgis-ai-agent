"""扩展信任分级（ADR-0104 / Wave 11）。

诚实边界声明：本进程内扩展通过 importlib 加载 Python 代码，与核心共享
解释器与文件系统权限——**这不是沙箱**。trust 分级的唯一作用是把「运行
哪些代码、授予哪些权限」的决定权显式化：

- ``core``：仓库内置代码（非扩展平台管理）。
- ``trusted_builtin``：随仓库发行、由扩展平台托管的内置扩展包
  （如 examples）。代码经 code review 进 master，默认可激活。
- ``trusted_extension``：运维通过 ``EXTENSIONS_ALLOW`` 显式点名的第三方
  扩展。manifest 里的自声明**永不**作为信任依据。
- ``local_untrusted``：本地发现但未点名信任的扩展。可 inspect / validate；
  激活需要运维显式放行（``EXTENSIONS_ALLOW`` 点名或
  ``EXTENSIONS_ACTIVATE_UNTRUSTED=true``）。
- ``blocked``：运维显式封禁（``EXTENSIONS_BLOCK``）或命中保留策略，
  禁止加载执行。

任何「untrusted-code sandbox」的表述都是虚假宣传；文档与 CLI 输出必须
使用「trusted-code boundary」语义。
"""

from __future__ import annotations

from enum import Enum


class TrustLevel(str, Enum):
    CORE = "core"
    TRUSTED_BUILTIN = "trusted_builtin"
    TRUSTED_EXTENSION = "trusted_extension"
    LOCAL_UNTRUSTED = "local_untrusted"
    BLOCKED = "blocked"


# manifest 的 trust 字段只是「自荐」，宿主侧允许的声明词表（便于前向校验
# 与诊断信息；最终 trust 由 host 策略决定，见 host.resolve_trust）。
DECLARABLE_TRUST_LEVELS = frozenset(
    {TrustLevel.TRUSTED_BUILTIN, TrustLevel.TRUSTED_EXTENSION, TrustLevel.LOCAL_UNTRUSTED}
)


def resolve_trust(
    extension_id: str,
    allowlist: frozenset[str],
    blocklist: frozenset[str],
    builtin_ids: frozenset[str] = frozenset(),
) -> TrustLevel:
    """确定性信任裁决。blocklist > allowlist > builtin > 默认 local_untrusted。"""
    if extension_id in blocklist:
        return TrustLevel.BLOCKED
    if extension_id in allowlist:
        return TrustLevel.TRUSTED_EXTENSION
    if extension_id in builtin_ids:
        return TrustLevel.TRUSTED_BUILTIN
    return TrustLevel.LOCAL_UNTRUSTED
