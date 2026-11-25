"""模型作者域契约（Model Authoring Schema v1）。

面向用户的可编辑 GIS 模型（DAG + 类型化端口 + 参数声明），是「不可变
作者模型 vs 编译计划 vs 运行实例」三层中的第一层：

- 本 schema 只描述**声明**（节点/端口/参数/边/来源/布局），不携带任何
  执行态（实例状态、receipt、缓存）——执行态归 workflow_runtime 实例行。
- 节点引用既有 capability / transform 算子词表，禁止任意代码/SQL/网络/
  凭证字段；校验（validate.py）与编译（compile.py）是唯一出口。
- ``layout`` 是纯 UI 坐标，**不参与科学指纹**（compile 忽略之）。
- 边界：本文件无 I/O、无 DB、无 LLM —— 纯 pydantic 契约。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 节点种类（作者词表）。与运行时 TYPED_NODE_KINDS 的映射在 compile.py：
#: data_input/transform/analysis/output 一一对应；cartography 映射为
#: 运行时 analysis?否 —— 运行时 driver 以 kind=="cartography" 分派真实
#: 渲染，因此作者词表直接携带 cartography（wire 契约 kind 是自由 str）。
MODEL_NODE_KINDS = (
    "data_input",
    "transform",
    "analysis",
    "cartography",
    "output",
)

#: transform 可执行算子词表 = workflow_runtime/adapters_geocompute.
#: _TRANSFORM_OPS 的已接线键（诚实子集；未接线算子会被 driver 以
#: NODE_NOT_EXECUTABLE 阻断，作者面直接不给）。单一事实源在适配器，
#: 此处经惰性读取避免环导入。
EXECUTABLE_TRANSFORM_OPS = ("buffer", "clip", "dissolve", "overlay", "filter", "reproject")

#: 参数种类词表（覆盖任务书：数值/字段/枚举/布尔/单位/时间窗）。
MODEL_PARAM_KINDS = (
    "number",
    "integer",
    "string",
    "enum",
    "boolean",
    "field",       # 引用某 data_input 的属性字段（input_node 必填）
    "unit",        # 计量单位（有界字符串）
    "time_window", # ISO-8601 区间 "start/end"
)

#: 模型来源词表（草稿从哪里来 —— 审计与导入面）。
MODEL_SOURCE_KINDS = ("manual", "project_workflow", "session_plan", "template")

#: 节点/参数名的稳定标识词法（slug；前端/工具/REST 共用）。
#: 29 上限的依据：transform 节点 wire id = "transform:<op>:<slug>"，
#: TypedWorkflowEdge.to_bounded_dict 截 node 段至 48 —— op 最长 9 字符时
#: slug ≤ 29 保证 wire 边标签精确可逆（rsplit 还原作者 id）。
_SLUG_MAX = 29


def _check_slug(value: str) -> str:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,%d}" % (_SLUG_MAX - 1), value or ""):
        raise ValueError(
            "must match [a-z][a-z0-9_]{0,47} (stable slug)")
    return value


class BoundedValue(BaseModel):
    """有界参数值/默认值载体（标量或短列表；拒绝深嵌套与巨型负载）。

    参数默认值必须是**标量或 ≤8 元素的标量列表**——模型参数不是数据
    载体（数据只能经 input ref 绑定），此处从契约上拒绝 misuse。
    """

    model_config = ConfigDict(extra="forbid")

    value: Any = None

    @field_validator("value")
    @classmethod
    def _bounded(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, bool) or isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            if len(v) > 256:
                raise ValueError("string value exceeds 256 chars")
            return v
        if isinstance(v, list):
            if len(v) > 8:
                raise ValueError("list value exceeds 8 items")
            if any(not isinstance(x, (str, int, float, bool)) for x in v):
                raise ValueError("list value must be scalars")
            return v
        raise ValueError("value must be scalar or short scalar list")


class ModelParamSpec(BaseModel):
    """工作流级参数声明（模型 = 模板；参数 = 实例化时用户可变的部分）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal[MODEL_PARAM_KINDS]  # type: ignore[valid-type]
    label: str = Field(default="", max_length=120)
    required: bool = True
    default: Optional[BoundedValue] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = Field(default_factory=tuple)
    unit: str = Field(default="", max_length=24)
    description: str = Field(default="", max_length=500)
    # field/time_window 参数的宿主 data_input 节点（字段枚举/时间锚定）。
    input_node: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_slug(v)

    @field_validator("choices")
    @classmethod
    def _choices(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        if len(v) > 16:
            raise ValueError("choices exceeds 16")
        for c in v:
            if not isinstance(c, str) or not c or len(c) > 64:
                raise ValueError("choice must be non-empty string ≤64")
        return v

    @model_validator(mode="after")
    def _kind_discipline(self) -> "ModelParamSpec":
        if self.kind == "enum" and not self.choices:
            raise ValueError("enum param requires choices")
        if self.kind == "field" and not self.input_node:
            raise ValueError("field param requires input_node")
        if self.kind not in ("enum", "field") and self.input_node:
            raise ValueError("input_node only valid for field params")
        if self.kind == "field" and self.input_node:
            _check_slug(self.input_node)
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum exceeds maximum")
        if self.kind not in ("number", "integer") and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("min/max only valid for number/integer params")
        return self


class ModelNodeParams(BaseModel):
    """节点静态参数（有界；值可引用模型参数 ``"$param:<name>"``）。"""

    model_config = ConfigDict(extra="forbid")

    values: Dict[str, BoundedValue] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _bounded(cls, v: Dict[str, BoundedValue]) -> Dict[str, BoundedValue]:
        if len(v) > 16:
            raise ValueError("node params exceed 16 keys")
        for k in v:
            import re

            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", k):
                raise ValueError(f"param key invalid: {k!r}")
        return v

    def canonical(self) -> Dict[str, Any]:
        return {k: b.value for k, b in self.values.items()}


class ModelNode(BaseModel):
    """作者模型节点：对既有 capability / transform 算子的受控引用。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: Literal[MODEL_NODE_KINDS]  # type: ignore[valid-type]
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    # data_input：数据角色（⊆ DATA_ROLES，单一事实源在 workflow_schema）。
    role: str = ""
    # analysis：能力 id（⊆ CapabilityRegistry；可用性由 validate 裁决）。
    capability: str = ""
    # analysis：钉定输入端口 artifact 类型（必须 ∈ capability 接受集）。
    input_artifact_type: str = ""
    # transform：算子（⊆ EXECUTABLE_TRANSFORM_OPS）。
    transform_op: str = ""
    # cartography/output 的静态参数（如 title/field）。
    params: ModelNodeParams = Field(default_factory=ModelNodeParams)
    optional: bool = False

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, v: str) -> str:
        return _check_slug(v)

    @model_validator(mode="after")
    def _kind_discipline(self) -> "ModelNode":
        if self.kind == "data_input" and not self.role:
            raise ValueError("data_input node requires role")
        if self.kind == "analysis" and not self.capability:
            raise ValueError("analysis node requires capability")
        if self.kind == "transform" and self.transform_op not in EXECUTABLE_TRANSFORM_OPS:
            raise ValueError(
                f"transform op must be one of {EXECUTABLE_TRANSFORM_OPS}")
        if self.kind in ("data_input", "transform", "cartography", "output") and self.capability:
            raise ValueError("capability only valid on analysis nodes")
        if self.role and self.kind != "data_input":
            raise ValueError("role only valid on data_input nodes")
        return self


class ModelEdge(BaseModel):
    """端口到端口的有向边（from → to；编译期与运行时双重校验）。"""

    model_config = ConfigDict(extra="forbid")

    from_node: str
    from_port: str = "out"
    to_node: str
    to_port: str = ""

    @field_validator("from_node", "to_node")
    @classmethod
    def _slugs(cls, v: str) -> str:
        return _check_slug(v)


class ModelLayoutPos(BaseModel):
    """纯 UI 坐标（不参与科学指纹）。"""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=4000)
    y: float = Field(ge=0, le=4000)


class ModelSource(BaseModel):
    """草稿来源（审计面；不携带外部内容）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[MODEL_SOURCE_KINDS]  # type: ignore[valid-type]
    ref: str = Field(default="", max_length=255)


class ModelDocument(BaseModel):
    """不可变作者模型契约 v1：模型只保存声明/版本/ref，不复制执行态。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=2000)
    source: ModelSource
    params: List[ModelParamSpec] = Field(default_factory=list)
    nodes: List[ModelNode] = Field(default_factory=list)
    edges: List[ModelEdge] = Field(default_factory=list)
    # 完成义务：显式输出节点清单（每个都必须存在且被边指到）。
    output_node_ids: List[str] = Field(default_factory=list)
    layout: Dict[str, ModelLayoutPos] = Field(default_factory=dict)
    template_id: str = Field(default="", max_length=120)

    @field_validator("params")
    @classmethod
    def _params_cap(cls, v: List[ModelParamSpec]) -> List[ModelParamSpec]:
        if len(v) > 16:
            raise ValueError("params exceed 16")
        names = [p.name for p in v]
        if len(names) != len(set(names)):
            raise ValueError("duplicate param name")
        return v

    @field_validator("nodes")
    @classmethod
    def _nodes_cap(cls, v: List[ModelNode]) -> List[ModelNode]:
        if len(v) > 32:
            raise ValueError("nodes exceed 32 (editor cap; runtime cap is 64)")
        ids = [n.node_id for n in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node_id")
        return v

    @field_validator("edges")
    @classmethod
    def _edges_cap(cls, v: List[ModelEdge]) -> List[ModelEdge]:
        if len(v) > 64:
            raise ValueError("edges exceed 64")
        return v

    @field_validator("output_node_ids")
    @classmethod
    def _outputs_cap(cls, v: List[str]) -> List[str]:
        if len(v) > 8:
            raise ValueError("output_node_ids exceed 8")
        return v

    @field_validator("layout")
    @classmethod
    def _layout_cap(cls, v: Dict[str, ModelLayoutPos]) -> Dict[str, ModelLayoutPos]:
        if len(v) > 32:
            raise ValueError("layout exceeds 32 entries")
        return v

    @model_validator(mode="after")
    def _structural_closure(self) -> "ModelDocument":
        ids = {n.node_id for n in self.nodes}
        for e in self.edges:
            if e.from_node not in ids or e.to_node not in ids:
                raise ValueError(
                    f"edge references unknown node: {e.from_node}->{e.to_node}")
        for oid in self.output_node_ids:
            if oid not in ids:
                raise ValueError(f"output_node_id unknown: {oid}")
        for p in self.params:
            if p.input_node and p.input_node not in ids:
                raise ValueError(f"param {p.name} input_node unknown: {p.input_node}")
        for nid in self.layout:
            if nid not in ids:
                raise ValueError(f"layout references unknown node: {nid}")
        return self

    def canonical_json(self) -> str:
        """科学指纹输入（layout 显式剔除；键排序确定性）。"""
        payload = self.model_dump(mode="json")
        payload.pop("layout", None)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))

    def document_fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


__all__ = [
    "MODEL_NODE_KINDS",
    "MODEL_PARAM_KINDS",
    "MODEL_SOURCE_KINDS",
    "EXECUTABLE_TRANSFORM_OPS",
    "BoundedValue",
    "ModelParamSpec",
    "ModelNodeParams",
    "ModelNode",
    "ModelEdge",
    "ModelLayoutPos",
    "ModelSource",
    "ModelDocument",
]
