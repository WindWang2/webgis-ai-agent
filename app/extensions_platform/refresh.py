"""投影变化 → 权威视图刷新（ADR-0105 V2 / Wave 9）。

消除 V1 known limitation「post-startup deactivate 不重编译 runtime
manifest」：host 的 ``on_projection_change`` 钩子接到本工厂产出的刷新器，
任何投影提交（激活 / 停用 / 回滚）后立即：

1. ``refresh_list_available_tools_args`` —— 扩展域词表进 schema 枚举；
2. ``compile_runtime_manifest`` + 缓存替换 —— 权威 runtime manifest 与
   registry 状态恢复一致（plan/reproducibility 指纹不再悬挂旧值）；
3. strict 校验 —— 启动期同源 gate（post-startup 降级为告警，不把运维
   操作变成 RuntimeError）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

ProjectionHook = Callable[[str, str], None]


def make_projection_refresher(tool_registry: Any) -> ProjectionHook:
    """产出 host 钩子：每次投影变化后重建权威运行时视图。"""

    def refresh(extension_id: str, event: str) -> None:
        from app.lib.gis import runtime_manifest as rm
        from app.tools.meta_tools import refresh_list_available_tools_args

        refresh_list_available_tools_args(tool_registry)
        manifest = rm.compile_runtime_manifest(tool_registry)
        rm._cached_manifest = manifest
        try:
            rm.validate_runtime_manifest_strict(manifest)
        except Exception as exc:  # noqa: BLE001 - 运行期 gate 降级为告警
            logger.warning(
                "[extensions] post-projection manifest strict validation flagged "
                "issues after %s.%s: %s",
                extension_id,
                event,
                exc,
            )
        logger.info(
            "[extensions] runtime view refreshed after %s.%s (fp=%s)",
            extension_id,
            event,
            manifest.fingerprint[:12],
        )

    return refresh
