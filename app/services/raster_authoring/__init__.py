"""G20 — 持久化栅格表达领域服务（单幅，权威 MapSpec 驱动）。

职责边界：
- 只做数值栅格的「表达」与「检视」，不重算遥感算法、不改上传/解析协议、
  不做画幅编排、不做数据成果打包。
- UI HTTP 路由与 Pi ``@tool`` 共用本包的 service 门面，同数据引用、同提交语义。
- 权威状态只落在 MapSpec（经既有 ``apply_gis_mutation``），本包无第二 store。

模块：contract（严格表达契约）/ source（owner-scoped 数据身份）/
descriptor（元数据+有界直方图）/ probe（源像元）/ palettes（色带单源）/
legend（图例+表达说明）/ render（瓦片与快照渲染）/ snapshot（单幅导出）/
service（门面）。
"""
from app.services.raster_authoring.service import (  # noqa: F401
    commit_expression,
    describe_raster,
    export_snapshot,
    probe_raster,
    resolve_committed_expression,
    validate_expression,
)

__all__ = [
    "commit_expression",
    "describe_raster",
    "export_snapshot",
    "probe_raster",
    "resolve_committed_expression",
    "validate_expression",
]
