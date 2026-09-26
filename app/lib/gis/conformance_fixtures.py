"""Conformance fixtures — core/extension 共用的确定性合成 registry（F06,
ADR-0215 候选 D8）.

#1482 的 conformance 校验器只有真实 registry canary 与内联测试构造；扩展
包认证（ADR-0201）与 provider 接入方需要一个**可复用、确定性、有界**的
fixture 构造器来对齐声明面契约。本模块提供：

- :func:`make_conformance_fixture`：一个覆盖全部 4 个 conformance 码、
  v2 关系词、凭证/权限声明面与 execution_policy 变体的合成世界。
- :class:`FixtureToolRegistry`：duck-typed tool registry（``metadata`` /
  ``all_metadata`` / ``list_tools``）—— 可直接喂
  :func:`app.lib.gis.runtime_manifest.compile_runtime_manifest` 与能力图。

纯内存、零 I/O、同输入同输出（确定性 golden 测试的基座）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

CONFORMANCE_FIXTURE_VERSION = "conformance_fixture.v1"


class FixtureToolRegistry:
    """最小 duck-typed tool registry（manifest/图/闸消费面全兼容）。"""

    def __init__(self, metadata_by_name: Dict[str, Dict[str, Any]]) -> None:
        self._meta: Dict[str, Dict[str, Any]] = {
            str(k): dict(v or {}) for k, v in metadata_by_name.items()
        }

    def list_tools(self) -> List[str]:
        return sorted(self._meta.keys())

    def all_metadata(self) -> Dict[str, Dict[str, Any]]:
        return {name: dict(meta) for name, meta in self._meta.items()}

    def metadata(self, name: str) -> Optional[Dict[str, Any]]:
        meta = self._meta.get(str(name))
        return dict(meta) if meta is not None else None


@dataclass
class ConformanceFixture:
    """合成 conformance 世界（期望 issue 码表随行，供 golden 断言）。"""

    version: str = CONFORMANCE_FIXTURE_VERSION
    tool_registry: FixtureToolRegistry = None  # type: ignore[assignment]
    capability_ids: List[str] = field(default_factory=list)
    algorithms: List[Any] = field(default_factory=list)
    expected_codes: List[Tuple[str, str, str]] = field(default_factory=list)  # (code, tool, capability)
    #: 期望的治理分类（tool → classification）
    expected_classifications: Dict[str, str] = field(default_factory=dict)


def _complete_meta(**overrides: Any) -> Dict[str, Any]:
    """ABI 元数据完备的工具 descriptor（无 _metadata_gaps）。"""
    meta = {
        "tier": 1,
        "status": "stable",
        "network": False,
        "deterministic": True,
        "side_effect": "none",
        "result_size_policy": "bounded",
        "output_semantic_type": "",
    }
    meta.update(overrides)
    return meta


def make_conformance_fixture() -> ConformanceFixture:
    """确定性合成世界 —— 覆盖 4 conformance 码 + v2 关系 + 安全声明面。

    场景（capability 词表 5 个，算法 2 个，工具 7 个）：

    - ``ok_algo_backed``：算法链 provider（无声明面分歧，基准面）。
    - ``declared_legal``：声明 ``cap_a``（算法链已覆盖该能力的其他
      provider）+ 元数据完备 + 输出契约一致 → legal_multi_provider。
    - ``declared_metadata_gaps``：声明 ``cap_b`` + 元数据欠缺 →
      metadata_missing（descriptor_metadata_incomplete 同步产出）。
    - ``declared_out_conflict``：声明 ``cap_a`` 但 output_semantic_type
      与派生面冲突 → suspected_misdeclaration。
    - ``declared_dangling``：声明不存在的 ``cap_ghost`` → fatal 悬空。
    - ``cred_tool``：声明 ``requires_credentials``/``required_permission``
      + execution_policy=celery（durable 诚实性披露面）。
    - ``io_tool``：accepts_ref_types/produced_refs（consumes/produces
      v2 边投影面）。
    """
    capability_ids = ["cap_a", "cap_b", "cap_c", "cap_dep", "cap_alt"]

    algorithms = [
        SimpleNamespace(
            capabilities=["cap_a"],
            tool_candidates=["ok_algo_backed"],
        ),
        SimpleNamespace(
            capabilities=["cap_b"],
            tool_candidates=["b_algo_backed"],
        ),
    ]
    # cap_b 的算法链 provider 也注册（declared_metadata_gaps 的对照面）。
    algorithms[1].tool_candidates = ["ok_algo_backed"]

    metadata: Dict[str, Dict[str, Any]] = {
        "ok_algo_backed": _complete_meta(
            capabilities=["cap_a", "cap_b"],
            output_semantic_type="geojson_fc",
            algorithms=["algo_a"],
        ),
        "declared_legal": _complete_meta(
            capabilities=["cap_a"],
            output_semantic_type="geojson_fc",
        ),
        "declared_metadata_gaps": {
            "tier": 1,
            "status": "stable",
            "capabilities": ["cap_b"],
            "output_semantic_type": "geojson_fc",
            # network/deterministic/side_effect/result_size_policy 缺席
        },
        "declared_out_conflict": _complete_meta(
            capabilities=["cap_a"],
            output_semantic_type="raster_tile",
        ),
        "declared_dangling": _complete_meta(
            capabilities=["cap_ghost"],
            output_semantic_type="geojson_fc",
        ),
        "cred_tool": _complete_meta(
            network=True,
            requires_credentials=["smtp", "upstream_tile"],
            required_permission="admin:publish",
            execution_policy="celery",
        ),
        "io_tool": _complete_meta(
            accepts_ref_types=["data", "style"],
            produced_refs=["map_product"],
        ),
    }

    expected_codes = [
        ("capability_id_dangling", "declared_dangling", "cap_ghost"),
        ("capability_binding_unbacked", "declared_legal", "cap_a"),
        ("capability_binding_unbacked", "declared_metadata_gaps", "cap_b"),
        ("capability_binding_unbacked", "declared_out_conflict", "cap_a"),
    ]
    expected_classifications = {
        "declared_legal": "legal_multi_provider",
        "declared_metadata_gaps": "metadata_missing",
        "declared_out_conflict": "suspected_misdeclaration",
    }
    return ConformanceFixture(
        tool_registry=FixtureToolRegistry(metadata),
        capability_ids=capability_ids,
        algorithms=algorithms,
        expected_codes=expected_codes,
        expected_classifications=expected_classifications,
    )


__all__ = [
    "CONFORMANCE_FIXTURE_VERSION",
    "ConformanceFixture",
    "FixtureToolRegistry",
    "make_conformance_fixture",
]
