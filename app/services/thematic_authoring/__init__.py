"""G33 专题表达编辑工作台（thematic authoring workbench）服务包。

对**已有**矢量专题层提供 检查（inspect）→ 预览（preview，零 mutation）→
单笔权威提交（commit，复用 MapSpec 生命周期引擎的 CAS/checkpoint/undo）。
架构对齐 G20 ``app/services/raster_authoring/``：本包只拥有矢量层
「哪个字段如何映射为已有符号通道」的作者决定，不新建第二套撤销栈、
MapSpec 存储或分类算法。
"""
from app.services.thematic_authoring.errors import ThematicAuthoringError
from app.services.thematic_authoring.service import (
    commit_expression,
    inspect_layer,
    preview_expression,
)

__all__ = [
    "ThematicAuthoringError",
    "commit_expression",
    "inspect_layer",
    "preview_expression",
]
