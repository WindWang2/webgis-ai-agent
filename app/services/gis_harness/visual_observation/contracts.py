"""Provider-neutral 视觉观察契约（F15/ADR-0214 决策一）。

``VisualObservationInput`` 是 finalizer seam 与生产 provider 之间的稳定
输入面（render revision / ref-only screenshot / viewport·component 框 /
MapSpec 投影 / deterministic findings refs）；``VisualObservationResult``
是诚实结论面（evaluated / not_evaluated + 机器可读 reason）。

**ref-only 纪律**：screenshot 只以 ``VisualScreenshotRef``（ref/sha/尺寸）
流动——字节只在 provider 评估瞬间解析进内存，trace/journal/map_product
只允许 ref+sha 摘要（测试锁定，键面白名单）。

全部有界、可 JSON 化、零 I/O；畸形输入诚实降级（不抛、不猜）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

#: snapshot dict 的合法键面（ref-only 纪律的可执行形态；to_snapshot 只发这些）。
_SNAPSHOT_KEYS = (
    "trigger", "session_id", "mapspec_revision", "mapspec_fingerprint",
    "mapspec_projection", "observation_summary", "deterministic_findings",
    "screenshot",
)

_MAX_COMPONENT_BOXES = 24
_MAX_DET_FINDINGS = 12
_MAX_TEXT = 64

#: 合法触发词（与 gis_harness.visual_evaluator 同源；此处只做防御性校验，
#: 不 import 以保持包零环依赖 —— 词漂移由 wiring 测试互锁）。
_KNOWN_TRIGGERS = frozenset({
    "finalization", "major_layout_change", "map_model_change",
    "visual_repair", "user_request",
})


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value or "")[:limit]


@dataclass(frozen=True)
class VisualScreenshotRef:
    """截图引用（永不携带字节）—— 内容寻址 blob 键 + sha 摘要。"""

    ref: str = ""
    sha256: str = ""
    size: int = 0
    width: int = 0
    height: int = 0
    mapspec_revision: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": _clip(self.ref, 96),
            "sha256": _clip(self.sha256, 64),
            "size": int(self.size),
            "width": int(self.width),
            "height": int(self.height),
            "mapspec_revision": int(self.mapspec_revision),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["VisualScreenshotRef"]:
        if not isinstance(raw, dict):
            return None
        ref = _clip(raw.get("ref"), 96)
        if not ref:
            return None
        try:
            return cls(
                ref=ref,
                sha256=_clip(raw.get("sha256"), 64),
                size=int(raw.get("size") or 0),
                width=int(raw.get("width") or 0),
                height=int(raw.get("height") or 0),
                mapspec_revision=int(raw.get("mapspec_revision") or 0),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class VisualObservationInput:
    """一次有界视觉观察的输入（provider-neutral）。"""

    trigger: str = ""
    session_id: str = ""
    mapspec_revision: int = 0
    mapspec_fingerprint: str = ""
    mapspec_projection: Mapping[str, Any] = field(default_factory=dict)
    observation_summary: Mapping[str, Any] = field(default_factory=dict)
    deterministic_findings: Tuple[Mapping[str, str], ...] = ()
    screenshot: Optional[VisualScreenshotRef] = None

    @property
    def trigger_known(self) -> bool:
        return self.trigger in _KNOWN_TRIGGERS

    def to_snapshot(self) -> Dict[str, Any]:
        """seam 传输形状（有界、JSON 化、ref-only；键面白名单锁定）。"""
        return {
            "trigger": _clip(self.trigger, 32),
            "session_id": _clip(self.session_id, 64),
            "mapspec_revision": int(self.mapspec_revision),
            "mapspec_fingerprint": _clip(self.mapspec_fingerprint, 96),
            "mapspec_projection": _bounded(self.mapspec_projection),
            "observation_summary": _bounded(self.observation_summary),
            "deterministic_findings": [
                {
                    "code": _clip(f.get("code") if isinstance(f, Mapping) else "", 64),
                    "severity": _clip(f.get("severity") if isinstance(f, Mapping) else "", 16),
                    "target": _clip(f.get("target") if isinstance(f, Mapping) else "", 64),
                }
                for f in tuple(self.deterministic_findings)[:_MAX_DET_FINDINGS]
                if isinstance(f, Mapping)
            ],
            "screenshot": self.screenshot.to_dict() if self.screenshot else None,
        }

    @classmethod
    def from_snapshot(cls, raw: Any) -> Optional["VisualObservationInput"]:
        """snapshot dict → 输入（非 dict / 全空 → None；缺键取默认）。"""
        if not isinstance(raw, dict):
            return None
        try:
            revision = int(raw.get("mapspec_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        det = tuple(
            f for f in (raw.get("deterministic_findings") or ())
            if isinstance(f, dict)
        )[:_MAX_DET_FINDINGS]
        projection = raw.get("mapspec_projection")
        summary = raw.get("observation_summary")
        return cls(
            trigger=_clip(raw.get("trigger"), 32),
            session_id=_clip(raw.get("session_id"), 64),
            mapspec_revision=revision,
            mapspec_fingerprint=_clip(raw.get("mapspec_fingerprint"), 96),
            mapspec_projection=projection if isinstance(projection, dict) else {},
            observation_summary=summary if isinstance(summary, dict) else {},
            deterministic_findings=det,
            screenshot=VisualScreenshotRef.from_dict(raw.get("screenshot")),
        )


@dataclass(frozen=True)
class VisualObservationResult:
    """一次视觉观察的诚实结论（fail-closed：not_evaluated 必携带 reason）。"""

    status: str                       # evaluated | not_evaluated
    reason: str = ""                  # not_evaluated 的机器可读原因
    findings: Tuple[Any, ...] = ()    # UnifiedFinding（domain=visual）
    provider: str = ""                # rules_only | vlm | hybrid
    taxonomy_counts: Mapping[str, int] = field(default_factory=dict)
    screenshot_sha256: str = ""       # 摘要回声（trace 允许的最高细节级）
    duration_ms: int = 0

    @property
    def evaluated(self) -> bool:
        return self.status == "evaluated"

    def to_bounded_dict(self) -> Dict[str, Any]:
        """map_product/trace 摘要面（ref+sha 级；不含 findings 全文）。"""
        out: Dict[str, Any] = {
            "status": self.status,
            "provider": _clip(self.provider, 32),
            "finding_count": len(self.findings),
            "taxonomy_counts": {
                str(k)[:24]: int(v)
                for k, v in dict(self.taxonomy_counts).items()
            },
            "duration_ms": int(self.duration_ms),
        }
        if not self.evaluated:
            out["reason"] = _clip(self.reason, 48)
        if self.screenshot_sha256:
            out["screenshot_sha256"] = _clip(self.screenshot_sha256, 32)
        return out

    @classmethod
    def not_evaluated(
        cls, reason: str, *, provider: str = "", duration_ms: int = 0,
        screenshot_sha256: str = "",
    ) -> "VisualObservationResult":
        return cls(
            status="not_evaluated", reason=str(reason or "")[:48],
            provider=provider, duration_ms=int(duration_ms),
            screenshot_sha256=screenshot_sha256,
        )


def _bounded(value: Any, depth: int = 0) -> Any:
    """递归有界化（宽度/深度/文本三重上限；防大 payload 混入 snapshot）。"""
    if depth >= 6:
        return "…" if value not in (None, "", {}, []) else value
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 48:
                out["__omitted__"] = True
                break
            out[_clip(k, 48)] = _bounded(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)[:48]
        out_list = [_bounded(v, depth + 1) for v in items]
        if len(value) > 48:
            out_list.append({"__omitted__": len(value) - 48})
        return out_list
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _clip(value, 48)


__all__ = [
    "VisualScreenshotRef",
    "VisualObservationInput",
    "VisualObservationResult",
]
