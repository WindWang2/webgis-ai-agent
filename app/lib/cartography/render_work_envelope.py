"""RenderWorkEnvelope —— MapSpec → versioned 执行信封的纯投影
（Goal 03 RenderWork Protocol V1，ADR-0224）。

与 :mod:`app.lib.cartography.render_work_projection`（ADR-0214 D1，**估工**
投影 → governor 预算面）的分工：本模块是**执行信封** —— 描述"这一代
desired state 要被执行成什么样的渲染工作"，供前端 runtime 对账与 ACK
绑定。二者都是 MapSpec 的派生只读投影，互不替代：

    MapSpec（authored cartographic truth，唯一真相）
      ├─ render_work_projection.v1  → 估工数字（governor admission）
      └─ render_work.v1（本模块）   → 执行信封（refs + identities）
                                        ↓ SSE step_result / map_state
                                    前端 runtime apply
                                        ↓
                    render_apply_ack.v1 work echo + sources[] 对账

纪律（红线）：

- **不是第二 MapSpec**：信封只含 refs/IDs/revisions/fingerprints/计数与
  布尔 —— 绝不含样式、几何、GeoJSON、组件配置载荷；任何消费方都不得
  从信封重建地图，只能用它对账「执行的是哪一代工作」；
- **确定性**：同 (mapspec, session_id, revision, fingerprint, seq) 必产
  同一 ``work_id`` / ``work_fingerprint``（无时间戳、无随机数）—— 幂等
  重放可检测；
- **零扫描**：只读 spec 骨架与源级身份字段，绝不触碰 inlineData 本体；
- **有界**：deps ≤64、layer_work ≤128、component_work ≤32、诊断 ≤8、
  id ≤64 字符 —— 超限截断 + 诊断披露（与 ACK 同值预算纪律）；
- **#1617 前向兼容**：``layer.representation`` 等 schema 开放面字段
  （extra="allow"）原样留在 MapSpec 内，信封不重裁决、不复述 ——
  RepresentationAtScale 的语义归其协议所有。

stale / generation 语义：信封自带 ``mapspec_revision`` +
``mapspec_fingerprint`` + ``observation_target_seq``；ACK echo 对账三者
任一不匹配 → 该 ACK 只能进诊断（详见 render_apply_ack 的 work 块校验）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: 执行信封契约版本（additive 演进升位；消费方按版本解释字段）。
RENDER_WORK_SCHEMA_VERSION = "render_work.v1"

# ── 有界预算（与 render_apply_ack 同值契约；manifest parity 锁定）──────
MAX_DATA_DEPENDENCIES = 64
MAX_LAYER_WORK = 128
MAX_COMPONENT_WORK = 32
MAX_DIAGNOSTICS = 8
_MAX_ID = 64
_MAX_FINGERPRINT = 64

#: 源身份字段投影（按 source.type；缺席字段 → None，不虚构）。
_SOURCE_KINDS = frozenset({
    "geojson", "vector", "raster", "raster-dem",
    "data_fabric", "wms", "wmts", "pmtiles",
})


@dataclass(frozen=True)
class DataDependency:
    """一个源的数据身份（ref-first：身份进信封，载荷永不进）。"""

    source_id: str
    kind: str
    #: 数据内容代次（geojson content_revision / fabric ref descriptor 同源）。
    revision: Optional[str] = None
    #: 语义/数据指纹（descriptor_fingerprint / data_fingerprint / imageRef）。
    fingerprint: Optional[str] = None
    #: ref 载体（fabric ref_id / raster imageRef）—— 数据面拉取键。
    ref: Optional[str] = None


@dataclass(frozen=True)
class LayerWork:
    """一层的执行工作项（id/类型/源绑定/可见性 —— 无样式）。"""

    layer_id: str
    layer_type: str
    #: 绑定的 source id（background 层 = "" 哨兵）。
    source_id: str
    visible: bool


@dataclass(frozen=True)
class ComponentWork:
    """一个 layout 组件的执行工作项。"""

    component_id: str
    component_type: str
    enabled: bool


@dataclass(frozen=True)
class InvalidationHints:
    """相对前一份信封的失效语义（style-only patch vs data reload 的显式化）。

    ``previous=None``（首份/会话恢复）→ 全部 unknown（``style_only=None``），
    绝不虚构"什么都没变"。
    """

    #: True = 无数据依赖 revision/fingerprint 变化（style-only patch，
    #: 数据面不应重拉）；False = 有数据面变化；None = 无前代可比。
    style_only: Optional[bool] = None
    data_changed: Tuple[str, ...] = field(default_factory=tuple)
    layers_added: Tuple[str, ...] = field(default_factory=tuple)
    layers_removed: Tuple[str, ...] = field(default_factory=tuple)
    components_changed: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RenderWorkEnvelope:
    """versioned 执行信封（纯数据，可序列化，deterministic）。"""

    schema_version: str
    work_id: str
    session_id: str
    mapspec_revision: int
    mapspec_fingerprint: str
    observation_target_seq: int
    data_dependencies: Tuple[DataDependency, ...]
    layer_work: Tuple[LayerWork, ...]
    component_work: Tuple[ComponentWork, ...]
    invalidation: InvalidationHints
    #: 本信封要求 renderer 具备的能力键（layer types / source kinds /
    #: component types；封闭词表来自 renderer_capability）。
    required_capabilities: Tuple[str, ...]
    diagnostics: Tuple[str, ...]
    work_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        """有界 dict 投影（SSE/map_state/诊断面；不含任何载荷）。"""
        return {
            "schema_version": self.schema_version,
            "work_id": self.work_id,
            "session_id": self.session_id,
            "mapspec_revision": self.mapspec_revision,
            "mapspec_fingerprint": self.mapspec_fingerprint[:_MAX_FINGERPRINT],
            "observation_target_seq": self.observation_target_seq,
            "data_dependencies": [
                {
                    "source_id": d.source_id,
                    "kind": d.kind,
                    **({"revision": d.revision} if d.revision is not None else {}),
                    **({"fingerprint": d.fingerprint} if d.fingerprint is not None else {}),
                    **({"ref": d.ref} if d.ref is not None else {}),
                }
                for d in self.data_dependencies
            ],
            "layer_work": [
                {
                    "layer_id": w.layer_id,
                    "layer_type": w.layer_type,
                    "source_id": w.source_id,
                    "visible": w.visible,
                }
                for w in self.layer_work
            ],
            "component_work": [
                {
                    "component_id": w.component_id,
                    "component_type": w.component_type,
                    "enabled": w.enabled,
                }
                for w in self.component_work
            ],
            "invalidation": {
                **({"style_only": self.invalidation.style_only}
                   if self.invalidation.style_only is not None else {}),
                "data_changed": list(self.invalidation.data_changed),
                "layers_added": list(self.invalidation.layers_added),
                "layers_removed": list(self.invalidation.layers_removed),
                "components_changed": list(self.invalidation.components_changed),
            },
            "required_capabilities": list(self.required_capabilities),
            "diagnostics": list(self.diagnostics),
            "work_fingerprint": self.work_fingerprint,
        }


def _bounded_id(value: Any) -> str:
    return str(value or "")[:_MAX_ID]


def _bounded_fp(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text[:_MAX_FINGERPRINT] if text else None


def _data_dependency(source_id: str, source: Any) -> DataDependency:
    """源 dict → 数据身份（只读身份字段；kind 词表外 → "unknown" 披露性保留）。"""
    if not isinstance(source, dict):
        return DataDependency(source_id=source_id, kind="unknown")
    kind = str(source.get("type") or "unknown")
    if kind not in _SOURCE_KINDS:
        kind = "unknown"
    revision: Optional[str] = None
    cr = source.get("content_revision")
    if isinstance(cr, (int, float)) and not isinstance(cr, bool):
        revision = str(int(cr))
    elif isinstance(cr, str) and cr:
        revision = cr[:_MAX_ID]
    fingerprint = (
        _bounded_fp(source.get("descriptor_fingerprint"))
        or _bounded_fp(source.get("data_fingerprint"))
        or _bounded_fp(source.get("profile_fingerprint"))
    )
    ref = (
        source.get("ref_id") if isinstance(source.get("ref_id"), str) else None
    ) or (
        source.get("imageRef") if isinstance(source.get("imageRef"), str) else None
    )
    return DataDependency(
        source_id=source_id,
        kind=kind,
        revision=revision,
        fingerprint=fingerprint,
        ref=ref[:_MAX_ID] if ref else None,
    )


def _identity_of(dep: DataDependency) -> Tuple[Any, ...]:
    """数据身份元组（revision/fingerprint/ref —— invalidation 判据）。"""
    return (dep.kind, dep.revision, dep.fingerprint, dep.ref)


def _fingerprint_payload(envelope_like: Dict[str, Any]) -> str:
    """canonical JSON → sha256 短指纹（deterministic；键序稳定）。"""
    canonical = json.dumps(
        envelope_like, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def project_render_work_envelope(
    mapspec: Optional[dict],
    *,
    session_id: str,
    revision: int = 0,
    fingerprint: str = "",
    observation_seq: int = 0,
    previous: Optional["RenderWorkEnvelope"] = None,
) -> RenderWorkEnvelope:
    """MapSpec + 会话事实 → RenderWorkEnvelope（纯函数，deterministic）。

    ``mapspec=None``/空 spec = 空图信封（显式存在，不抛错 —— 投影失败
    绝不中断 mutation 链）。``previous`` 供给时计算 invalidation hints
    （style-only / data-changed / 层增删 / 组件变化）。
    """
    if not isinstance(mapspec, dict):
        mapspec = {}
    diagnostics: List[str] = []

    sources = mapspec.get("sources")
    source_map: Dict[str, Any] = sources if isinstance(sources, dict) else {}
    deps_list: List[DataDependency] = []
    truncated_deps = False
    for sid in source_map:
        if len(deps_list) >= MAX_DATA_DEPENDENCIES:
            truncated_deps = True
            break
        if not isinstance(sid, str) or not sid:
            continue
        deps_list.append(_data_dependency(sid[:_MAX_ID], source_map.get(sid)))
    if truncated_deps:
        diagnostics.append(f"data_dependencies truncated at {MAX_DATA_DEPENDENCIES}")
    # 排序稳定（dict 序在不同运行间不保证；信封字段序必须 deterministic）。
    deps_list.sort(key=lambda d: d.source_id)
    deps: Tuple[DataDependency, ...] = tuple(deps_list)

    layers_raw = [
        ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)
    ]
    layers_total = len(layers_raw)
    truncated_layers = layers_total > MAX_LAYER_WORK
    if truncated_layers:
        diagnostics.append(f"layer_work truncated at {MAX_LAYER_WORK}")
        layers_raw = layers_raw[:MAX_LAYER_WORK]
    layer_work = tuple(
        LayerWork(
            layer_id=_bounded_id(ly.get("id")),
            layer_type=_bounded_id(ly.get("type")),
            source_id=_bounded_id(ly.get("source")),
            visible=ly.get("visible") is not False,
        )
        for ly in layers_raw
        if _bounded_id(ly.get("id"))
    )

    layout = mapspec.get("layout")
    components_raw = (
        layout.get("components") if isinstance(layout, dict) else None
    ) or []
    components = [
        c for c in components_raw
        if isinstance(c, dict) and _bounded_id(c.get("id"))
    ]
    truncated_components = len(components) > MAX_COMPONENT_WORK
    if truncated_components:
        diagnostics.append(f"component_work truncated at {MAX_COMPONENT_WORK}")
        components = components[:MAX_COMPONENT_WORK]
    component_work = tuple(
        ComponentWork(
            component_id=_bounded_id(c.get("id")),
            component_type=_bounded_id(c.get("type")),
            enabled=c.get("enabled") is not False,
        )
        for c in components
    )

    # invalidation hints：previous 在场才可比（首份 → 全 unknown，不虚构）。
    invalidation = InvalidationHints()
    if previous is not None:
        prev_deps = {d.source_id: _identity_of(d) for d in previous.data_dependencies}
        cur_deps = {d.source_id: _identity_of(d) for d in deps}
        data_changed = tuple(sorted(
            sid for sid, ident in cur_deps.items()
            if prev_deps.get(sid) != ident
        ))
        prev_layers = {w.layer_id for w in previous.layer_work}
        cur_layers = {w.layer_id for w in layer_work}
        prev_components = {
            (w.component_id, w.component_type, w.enabled)
            for w in previous.component_work
        }
        cur_components = {
            (w.component_id, w.component_type, w.enabled)
            for w in component_work
        }
        invalidation = InvalidationHints(
            style_only=(
                not data_changed
                and not (cur_layers - prev_layers)
                and not (prev_layers - cur_layers)
                and prev_components == cur_components
            ),
            data_changed=data_changed[:MAX_DATA_DEPENDENCIES],
            layers_added=tuple(sorted(cur_layers - prev_layers))[:MAX_LAYER_WORK],
            layers_removed=tuple(sorted(prev_layers - cur_layers))[:MAX_LAYER_WORK],
            components_changed=tuple(sorted(
                {c[0] for c in cur_components ^ prev_components}
            ))[:MAX_COMPONENT_WORK],
        )

    # required capabilities：layer types + source kinds + component types
    # （封闭词表键 = renderer_capability 声明面；这里只收集不裁决 ——
    # 裁决（supported/unsupported）属 capability 声明与 ACK 面）。
    caps = sorted({
        *[w.layer_type for w in layer_work if w.layer_type],
        *[d.kind for d in deps if d.kind != "unknown"],
        *[f"component:{w.component_type}" for w in component_work if w.component_type],
    })

    revision_int = max(0, int(revision or 0))
    seq_int = max(0, int(observation_seq or 0))
    fp = str(fingerprint or "")[:_MAX_FINGERPRINT]
    sid = str(session_id or "")[:_MAX_ID]

    # work_id：deterministic（revision + mapspec fingerprint 短前缀 ——
    # 同代重投影必同 id；幂等重放可检测）。
    work_id = f"rw-{revision_int}-{fp[:12]}" if fp else f"rw-{revision_int}-empty"

    semantic = {
        "schema_version": RENDER_WORK_SCHEMA_VERSION,
        "session_id": sid,
        "mapspec_revision": revision_int,
        "mapspec_fingerprint": fp,
        "observation_target_seq": seq_int,
        "data_dependencies": [
            [d.source_id, *_identity_of(d)] for d in deps
        ],
        "layer_work": [
            [w.layer_id, w.layer_type, w.source_id, w.visible]
            for w in layer_work
        ],
        "component_work": [
            [w.component_id, w.component_type, w.enabled]
            for w in component_work
        ],
        "required_capabilities": caps,
    }
    work_fp = _fingerprint_payload(semantic)

    return RenderWorkEnvelope(
        schema_version=RENDER_WORK_SCHEMA_VERSION,
        work_id=work_id,
        session_id=sid,
        mapspec_revision=revision_int,
        mapspec_fingerprint=fp,
        observation_target_seq=seq_int,
        data_dependencies=deps,
        layer_work=layer_work,
        component_work=component_work,
        invalidation=invalidation,
        required_capabilities=tuple(caps),
        diagnostics=tuple(diagnostics[:MAX_DIAGNOSTICS]),
        work_fingerprint=work_fp,
    )


__all__ = [
    "RENDER_WORK_SCHEMA_VERSION",
    "MAX_DATA_DEPENDENCIES",
    "MAX_LAYER_WORK",
    "MAX_COMPONENT_WORK",
    "MAX_DIAGNOSTICS",
    "DataDependency",
    "LayerWork",
    "ComponentWork",
    "InvalidationHints",
    "RenderWorkEnvelope",
    "project_render_work_envelope",
]
