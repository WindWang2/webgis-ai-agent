"""模型作者域共享契约：finding 词表、端口推导、角色端口默认。

单一事实源纪律：
- 数据角色 → `workflow_schema.DATA_ROLES`（不复制词表，运行时引用）；
- artifact 类型 → `lib.gis.artifacts.ArtifactTypeRegistry`（只读）；
- capability → `lib.gis.capability_registry.CapabilityRegistry`（只读）；
- 运行时端口校验词表 → `workflow_runtime.binding`（违规码同门）。
本模块只做**作者面投影**（模型节点 ↔ 上述权威的映射），不实现第二份
科学语义。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from app.schemas.model_authoring_schema import ModelNode

# ── typed findings 词表（validate 输出；错误级阻断编译/发布）──────────

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# 结构面
F_EMPTY_GRAPH = "MODEL_EMPTY_GRAPH"
F_CYCLE = "MODEL_CYCLE"
F_EDGE_PORT_UNKNOWN = "EDGE_PORT_UNKNOWN"
F_OUTPUT_UNREACHABLE = "OUTPUT_UNREACHABLE"
F_OUTPUT_NOT_SINK = "OUTPUT_NOT_SINK"
F_NODE_UNREACHABLE = "NODE_UNREACHABLE"
# 数据角色面
F_ROLE_UNKNOWN = "ROLE_UNKNOWN"
F_MULTIPLE_INPUT_SAME_ROLE = "ROLE_DUPLICATE_INPUT"
# capability 面
F_CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
F_CAPABILITY_NOT_NATIVE = "CAPABILITY_NOT_NATIVE"
F_INPUT_ARTIFACT_MISMATCH = "INPUT_ARTIFACT_MISMATCH"
F_PORT_TYPE_MISMATCH = "PORT_TYPE_MISMATCH"
F_GEOMETRY_MISMATCH = "GEOMETRY_MISMATCH"
# 参数面
F_PARAM_REQUIRED_MISSING_DEFAULT = "PARAM_REQUIRED_NO_DEFAULT"
F_PARAM_REF_UNKNOWN = "PARAM_REF_UNKNOWN"
F_PARAM_INVALID_VALUE = "PARAM_INVALID_VALUE"
F_PARAM_TYPE_MISMATCH = "PARAM_TYPE_MISMATCH"
# 科学/执行面
F_TRANSFORM_OP_UNKNOWN = "TRANSFORM_OP_UNKNOWN"
F_INPUT_NODE_MISSING = "INPUT_NODE_MISSING"
F_DATA_ROLE_BLOCKED = "DATA_ROLE_BLOCKED"

#: finding 码 → 双语短述（bounded；前端/工具直接投影）。
FINDING_MESSAGES: Dict[str, Tuple[str, str]] = {
    F_EMPTY_GRAPH: ("模型没有任何节点", "Model has no nodes"),
    F_CYCLE: ("存在环依赖", "Cycle detected"),
    F_EDGE_PORT_UNKNOWN: ("边引用了不存在的节点或端口", "Edge references unknown node/port"),
    F_OUTPUT_UNREACHABLE: ("输出节点不可达（无上游）", "Output node is unreachable"),
    F_OUTPUT_NOT_SINK: ("输出节点还有下游（输出必须是汇点）", "Output node must be a sink"),
    F_NODE_UNREACHABLE: ("节点不可达（悬空）", "Node is unreachable (dangling)"),
    F_ROLE_UNKNOWN: ("数据角色不在词表中", "Unknown data role"),
    F_MULTIPLE_INPUT_SAME_ROLE: ("同一数据角色被声明了多个输入", "Duplicate input for the same data role"),
    F_CAPABILITY_UNKNOWN: ("能力 id 不在注册表", "Capability id not in registry"),
    F_CAPABILITY_NOT_NATIVE: ("能力不是 native 可用状态", "Capability is not native"),
    F_INPUT_ARTIFACT_MISMATCH: ("钉定的输入类型不在能力接受集", "Pinned input type not accepted by capability"),
    F_PORT_TYPE_MISMATCH: ("端口类型不兼容", "Port artifact types are incompatible"),
    F_GEOMETRY_MISMATCH: ("几何族不兼容", "Geometry kinds are incompatible"),
    F_PARAM_REQUIRED_MISSING_DEFAULT: ("必填参数没有默认值（实例化时必须提供）", "Required parameter has no default"),
    F_PARAM_REF_UNKNOWN: ("节点参数引用了未声明的模型参数", "Node param references undeclared model param"),
    F_PARAM_INVALID_VALUE: ("参数默认值不满足声明约束", "Parameter default violates its declaration"),
    F_PARAM_TYPE_MISMATCH: ("参数默认值类型与声明不符", "Parameter default type mismatch"),
    F_TRANSFORM_OP_UNKNOWN: ("transform 算子不在可执行词表", "Transform op not executable"),
    F_INPUT_NODE_MISSING: ("analysis/cartography 节点没有数据输入", "Node has no data input"),
    F_DATA_ROLE_BLOCKED: ("必选数据角色缺失（block 策略）", "Required data role missing (blocking)"),
}


@dataclass(frozen=True)
class ModelFinding:
    """静态验证发现（typed；前端/工具直接消费）。"""

    code: str
    severity: str            # error | warning
    node_id: str = ""
    edge: str = ""           # "from.port->to.port"（结构面定位）
    param: str = ""
    detail: str = ""

    def to_bounded(self) -> Dict[str, str]:
        zh, en = FINDING_MESSAGES.get(self.code, ("", ""))
        return {
            "code": self.code,
            "severity": self.severity,
            "node_id": self.node_id[:64],
            "edge": self.edge[:120],
            "param": self.param[:64],
            "detail_zh": zh,
            "detail_en": en,
            "detail": self.detail[:300],
        }


# ── 角色端口默认（data_input 输出端口的宽类型推导；validate 细化）────

#: role → (artifact_type, geometry_kind)。宽端口优先（feature_collection
#: 是运行时绑定门认可的特征宽类型）；table 类角色落 stats_table。
_ROLE_PORT_DEFAULTS: Dict[str, Tuple[str, str]] = {
    "subject": ("feature_collection", "unknown"),
    "measure": ("stats_table", "table"),
    "boundary": ("admin_boundary_set", "polygon"),
    "denominator": ("stats_table", "table"),
    "network": ("feature_collection", "network"),
    "elevation": ("surface", "raster"),
    "hazard": ("feature_collection", "unknown"),
    "receptor": ("feature_collection", "unknown"),
    "population": ("stats_table", "table"),
    "facility": ("poi_feature_set", "point"),
    "criteria": ("feature_collection", "unknown"),
    "constraint": ("feature_collection", "unknown"),
    "reference": ("feature_collection", "unknown"),
    "baseline": ("feature_collection", "unknown"),
    "target_time": ("feature_collection", "unknown"),
    "comparison_time": ("feature_collection", "unknown"),
}


def role_port_default(role: str) -> Tuple[str, str]:
    """role → (默认 artifact_type, geometry_kind)；未知角色给宽端口。"""
    return _ROLE_PORT_DEFAULTS.get(role, ("feature_collection", "unknown"))


# ── 端口推导（模型节点 → (inputs, outputs) 端口清单）────────────────

def _cap_io(capability: str) -> Tuple[List[str], List[str], str]:
    """capability → (accepted input artifact types, output types, geometry)。

    registry 不可用/未知 → 诚实空集（validate 报 CAPABILITY_UNKNOWN）。
    """
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        if desc is None:
            return [], [], "unknown"
        geometry = str(getattr(desc, "geometry_requirement", "") or "unknown")
        return (
            [str(t) for t in (desc.input_artifact_types or [])],
            [str(t) for t in (desc.output_artifact_types or [])],
            geometry,
        )
    except Exception:  # noqa: BLE001 — 只读投影，失败诚实空
        return [], [], "unknown"


def node_ports(node: ModelNode) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """模型节点 → (输入端口清单, 输出端口清单)。

    端口 dict：{name, artifact_type, geometry_kind, required}。
    - data_input：无输入；单输出（名 = role，宽类型按角色默认）。
    - transform：单输入 "in"（feature_collection）；单输出 "out"
      （reproject/buffer/filter/clip/dissolve 保持特征集语义）。
    - analysis：单输入 "in"（能力接受集第一项或作者钉定）；单输出 "out"
      （能力第一输出类型）。
    - cartography：单输入 "in"（feature_collection）；单输出 "out"
      （map_image）。
    - output：单输入 "in"；无输出（汇点）。
    """
    ins: List[Dict[str, str]] = []
    outs: List[Dict[str, str]] = []
    if node.kind == "data_input":
        at, gk = role_port_default(node.role)
        outs.append({"name": node.role or "out", "artifact_type": at,
                     "geometry_kind": gk, "required": True})
    elif node.kind == "transform":
        ins.append({"name": "in", "artifact_type": "feature_collection",
                    "geometry_kind": "unknown", "required": True})
        outs.append({"name": "out", "artifact_type": "feature_collection",
                     "geometry_kind": "unknown", "required": True})
    elif node.kind == "analysis":
        accepted, produced, _gk = _cap_io(node.capability)
        pinned = node.input_artifact_type or (accepted[0] if accepted else "feature_collection")
        ins.append({"name": "in", "artifact_type": pinned,
                    "geometry_kind": "unknown", "required": True})
        out_type = produced[0] if produced else "feature_collection"
        outs.append({"name": "out", "artifact_type": out_type,
                     "geometry_kind": "unknown", "required": True})
    elif node.kind == "cartography":
        ins.append({"name": "in", "artifact_type": "feature_collection",
                    "geometry_kind": "unknown", "required": True})
        outs.append({"name": "out", "artifact_type": "map_image",
                     "geometry_kind": "unknown", "required": True})
    else:  # output
        ins.append({"name": "in", "artifact_type": "feature_collection",
                    "geometry_kind": "unknown", "required": True})
    return ins, outs


def node_input_ports(node: ModelNode) -> Dict[str, Dict[str, str]]:
    """节点 → {port_name: port}（校验/编译共用）。"""
    ins, _ = node_ports(node)
    return {p["name"]: p for p in ins}


def node_output_ports(node: ModelNode) -> Dict[str, Dict[str, str]]:
    _ins, outs = node_ports(node)
    return {p["name"]: p for p in outs}


def artifact_type_known(artifact_type: str) -> bool:
    """artifact 类型是否在权威注册表（未知类型端口按宽类型放行前先查）。"""
    try:
        from app.lib.gis.artifacts import get_artifact_type_registry

        return get_artifact_type_registry().get(artifact_type) is not None
    except Exception:  # noqa: BLE001
        return False


def capability_status(capability: str) -> str:
    """capability 声明状态（native/planned/unavailable/"" = 未知）。"""
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        return str(getattr(desc, "status", "") or "") if desc else ""
    except Exception:  # noqa: BLE001
        return ""


def geometry_kind_of(artifact_type: str) -> str:
    try:
        from app.lib.gis.artifacts import get_artifact_type_registry

        d = get_artifact_type_registry().get(artifact_type)
        return str(d.geometry_kind) if d else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


#: 宽特征类型（运行时绑定门的 ANY-FEATURE 端口，typed_dag 同词）。
_WIDE_FEATURE_TYPE = "feature_collection"
#: 宽特征端口可喂入的目标 category（特征族；table/aggregate/raster 不收）。
_FEATURE_CATEGORIES = ("feature_set", "zone", "network")
_FEATURE_GEOMETRIES = ("point", "line", "polygon", "network")


def ports_compatible(src_artifact: str, dst_artifact: str) -> bool:
    """边兼容裁决（作者面静态近似；运行时绑定门以真实 descriptor 复核）。

    - 同类型直通；
    - 目标宽类型（feature_collection）收一切；
    - 源宽类型可喂特征族目标（feature_set/zone/network category 或
      point/line/polygon/network 几何）—— 不喂 table/aggregate/raster；
    - 其余要求注册表 category 相同。
    """
    if src_artifact == dst_artifact:
        return True
    if dst_artifact == _WIDE_FEATURE_TYPE:
        return True
    if src_artifact == _WIDE_FEATURE_TYPE:
        dst_gk = geometry_kind_of(dst_artifact)
        if dst_gk in _FEATURE_GEOMETRIES:
            return True
        try:
            from app.lib.gis.artifacts import get_artifact_type_registry

            d = get_artifact_type_registry().get(dst_artifact)
            return d is not None and str(d.category) in _FEATURE_CATEGORIES
        except Exception:  # noqa: BLE001
            return False
    try:
        from app.lib.gis.artifacts import get_artifact_type_registry

        reg = get_artifact_type_registry()
        s, d = reg.get(src_artifact), reg.get(dst_artifact)
        if s is None or d is None:
            return src_artifact == dst_artifact
        return str(s.category) == str(d.category) and s.category != ""
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "F_EMPTY_GRAPH",
    "F_CYCLE",
    "F_EDGE_PORT_UNKNOWN",
    "F_OUTPUT_UNREACHABLE",
    "F_OUTPUT_NOT_SINK",
    "F_NODE_UNREACHABLE",
    "F_ROLE_UNKNOWN",
    "F_MULTIPLE_INPUT_SAME_ROLE",
    "F_CAPABILITY_UNKNOWN",
    "F_CAPABILITY_NOT_NATIVE",
    "F_INPUT_ARTIFACT_MISMATCH",
    "F_PORT_TYPE_MISMATCH",
    "F_GEOMETRY_MISMATCH",
    "F_PARAM_REQUIRED_MISSING_DEFAULT",
    "F_PARAM_REF_UNKNOWN",
    "F_PARAM_INVALID_VALUE",
    "F_PARAM_TYPE_MISMATCH",
    "F_TRANSFORM_OP_UNKNOWN",
    "F_INPUT_NODE_MISSING",
    "F_DATA_ROLE_BLOCKED",
    "FINDING_MESSAGES",
    "ModelFinding",
    "role_port_default",
    "node_ports",
    "node_input_ports",
    "node_output_ports",
    "artifact_type_known",
    "capability_status",
    "geometry_kind_of",
    "ports_compatible",
]
