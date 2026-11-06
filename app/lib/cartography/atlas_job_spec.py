"""AtlasJobSpec —— 批量图册 durable job 的版本化输入契约（atlas_job.v1）。

输入是**已存在的单张权威 MapSpec**（会话 committed spec；执行时由 worker
加载并水合 ``ref:`` 载体源）+ 既有 :class:`AtlasPolicy` 分页策略 + 本模块
新增的图册级语义字段。红线：

- 不发明第二套分页几何/版面模型 —— 页面规划 100% 复用
  ``publication_ir.plan_publication_pages``（PublicationIR 单一真相）；
- ``classification_scope`` 只承载**语义披露与 QA 判据**，不代算分级
  （数据清洗/分析是 G1/G2 领域；local 分级由 frames 驱动 + 用户显式
  per-frame ``layerOverrides`` 表达，本契约如实记录并在图册间标注
  不可直接跨页比较）；
- 字段全部有界；typed 失败（``MapSpecSchemaError``，路由/工具映射 400），
  绝不静默退化。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.lib.cartography.publication_ir import AtlasPolicy

#: 契约版本（语义变更 = bump + 迁移说明）。
ATLAS_JOB_SPEC_VERSION = "atlas_job.v1"


class ClassificationScope(str, Enum):
    """图册图例/分级的跨页语义（显式声明，杜绝偷换科学含义）。

    - ``shared``：全册继承同一 spec 级 legend/classification（绝对阈值）。
      QA 门逐页校验分类摘要一致，manifest 记 ``cross_page_comparability=true``。
    - ``local``：逐页局部分级（相对阈值）—— 仅 frames 驱动 + 用户显式
      per-frame layerOverrides 可表达；每页 manifest 记本页分类摘要，
      图册级 ``cross_page_comparability=false``（每页明示不可直接跨页比较）。
    """

    shared = "shared"
    local = "local"


class AtlasJobSpec(BaseModel):
    """批量图册任务输入（frozen；业务规则经 :meth:`validate_semantics`）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: Literal["atlas_job.v1"] = ATLAS_JOB_SPEC_VERSION
    session_id: str = Field(min_length=1, max_length=255)
    """权威 spec 与数据归属的会话（属主校验 + ref 水合域）。"""
    atlas: AtlasPolicy
    classification_scope: ClassificationScope = ClassificationScope.shared
    target_dpi: int = Field(default=300, ge=72, le=600)
    title: str = Field(default="", max_length=160)
    simulated_data: bool = False
    """数据来源披露：合成/测试数据必须置 true（manifest ``simulated_data``
    恒如实传播；生产会话数据缺省 false）。这是披露字段，不是开关——渲染
    行为不因它改变。"""

    def validate_semantics(self) -> None:
        """构造后的业务规则（失败 → typed ``MapSpecSchemaError``）。"""
        if not self.session_id.strip():
            raise MapSpecSchemaError(
                "atlas_session_missing", "atlas job 需要 session_id（权威 spec 归属）")
        if self.classification_scope == ClassificationScope.local \
                and self.atlas.driver != "frames":
            raise MapSpecSchemaError(
                "atlas_local_requires_frames",
                "classification_scope=local 需要显式逐页分级 —— 仅 frames 驱动"
                "（layout.frames + per-frame layerOverrides）可表达；"
                "category/feature 驱动共享 spec 级分类（用 shared）")

    def job_params(self) -> Dict[str, Any]:
        """幂等键与落库参数投影（无载荷；``submit_durable_job`` 消费）。

        幂等语义：同一 (session, 策略, 语义, dpi, 标题) 的重复提交复用同一
        job 行 —— 双击/SSE 重连不产生 N 份记录。数据内容不入键：committed
        spec 变更不新建 job，由页指纹在执行/重试层识别需要刷新的页。
        """
        return {
            "spec_version": self.spec_version,
            "session_id": self.session_id,
            "atlas": self.atlas.model_dump(),
            "classification_scope": self.classification_scope.value,
            "target_dpi": self.target_dpi,
            "title": self.title,
            "simulated_data": self.simulated_data,
        }


__all__ = [
    "ATLAS_JOB_SPEC_VERSION",
    "AtlasJobSpec",
    "ClassificationScope",
]
