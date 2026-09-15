"""Visual Critic 评审引擎（ADR-0185 D3/D6/D7）。

编排纪律：

- **fail-closed**：``evaluate`` 永不抛异常——快照失效 / provider 超时 /
  输出非法 / 未配置全部归因为 ``not_evaluated`` 报告（机器可读 reason），
  绝不伪造 evaluated，更绝不产出 pass。
- **限流**：``(session_id, mapspec_fingerprint, image_sha256)`` LRU 记忆化，
  单轮至多一次真实外呼（含 not_evaluated 结论，与 ADR-0158 legacy 记忆化
  同纪律：评估面高频重入不放大外呼）。
- **评分推导**：维度分由 critiques 确定性推导，不采信 VLM 自报分（D6）。
- **消毒**：维度白名单 + 改图意图判废 + 置信度/文本/ bbox 有界化（契约层
  extra=forbid 之上的第二道闸，legacy sanitize 纪律）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import ValidationError

from app.lib.harness.visual_judge.contracts import (
    VisualCritiqueItem,
    VisualDimension,
    VisualDimensionScore,
    VisualJudgeReport,
    safe_bbox,
)
from app.lib.harness.visual_judge.snapshot_extractor import (
    MapSnapshot,
    SnapshotError,
    SnapshotExtractor,
)
from app.lib.harness.visual_judge.vlm_provider import (
    PROVIDER_GEMINI,
    PROVIDER_OPENAI_COMPATIBLE,
    CriticOutputError,
    CriticTimeout,
    GeminiVLMClient,
    OpenAICompatVLMClient,
    VLMClient,
    VLMRequest,
    is_placeholder_key,
    parse_critic_output,
    resolve_provider_config,
)

logger = logging.getLogger(__name__)

#: 改图意图字段（任一命中 ⇒ 整条判废；legacy ``visual_evaluator`` 同词表）。
_FORBIDDEN_KEYS = {
    "mutation", "mutations", "intent", "intents", "mapspec",
    "spec", "patch", "layers", "components_patch", "apply",
}
_MAX_CRITIQUES = 12
_CACHE_LIMIT = 64

#: 评分罚分表（D6）：error=4 / warning=1.5 / info=0.5。
_SEVERITY_PENALTY = {"error": 4.0, "warning": 1.5, "info": 0.5}
_UNOBSERVED_RATIONALE = "no critique observed; score is provisional, not an endorsement"


def visual_critic_runtime_enabled() -> bool:
    """运行时总开关（默认关闭——不开 ⇒ 既有评审路径逐字节等价）。"""
    return os.getenv("CARTO_VISUAL_CRITIC_RUNTIME", "").strip().lower() in (
        "1", "true", "yes",
    )


class CriticMemoCache:
    """(session_id, mapspec_fingerprint, image_sha256) → report 的 LRU 记忆化。"""

    def __init__(self, limit: int = _CACHE_LIMIT):
        self.limit = limit
        self._entries: "OrderedDict[Tuple[str, str, str], VisualJudgeReport]" = OrderedDict()

    def get(self, key: Tuple[str, str, str]) -> Optional[VisualJudgeReport]:
        report = self._entries.get(key)
        if report is not None:
            self._entries.move_to_end(key)
        return report

    def put(self, key: Tuple[str, str, str], report: VisualJudgeReport) -> None:
        self._entries[key] = report
        self._entries.move_to_end(key)
        while len(self._entries) > self.limit:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


def _context_text(deterministic_summary: Optional[Dict[str, Any]]) -> str:
    """确定性摘要 → 有界参考文本（只作上下文，不替代看图）。"""
    if not deterministic_summary:
        return ""
    try:
        return json.dumps(deterministic_summary, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(deterministic_summary)[:512]


def _sanitize_critiques(raw_items: Sequence[Any]) -> List[VisualCritiqueItem]:
    """白名单消毒：维度白名单外/改图意图/形状不合法 ⇒ 逐条判废（不外抛）。"""
    out: List[VisualCritiqueItem] = []
    for item in list(raw_items)[:_MAX_CRITIQUES]:
        if not isinstance(item, dict):
            continue
        if any(key in item for key in _FORBIDDEN_KEYS):
            continue  # 任何改图意图字段 ⇒ 整条判废
        dimension = item.get("dimension")
        try:
            dimension = VisualDimension(dimension)
        except (ValueError, TypeError):
            continue
        severity = item.get("severity")
        if severity not in ("info", "warning", "error"):
            severity = "warning"  # legacy sanitize 同纪律：未知严重度降为 warning
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))
        try:
            out.append(VisualCritiqueItem(
                dimension=dimension,
                severity=severity,
                confidence=confidence,
                suggestion=str(item.get("suggestion") or ""),
                evidence=str(item.get("evidence") or ""),
                bbox=safe_bbox(item.get("bbox")),
                defect_type=str(item.get("defect_type") or ""),
            ))
        except ValidationError:
            continue
    return out


def _derive_dimension_scores(
    critiques: Sequence[VisualCritiqueItem],
) -> List[VisualDimensionScore]:
    """维度分确定性推导（D6）：10 − Σpenalty，置信度 = 维度内最大批评置信。"""
    scores: List[VisualDimensionScore] = []
    for dim in VisualDimension:
        items = [c for c in critiques if c.dimension is dim]
        if not items:
            scores.append(VisualDimensionScore(
                dimension=dim, score=10.0, confidence=0.0,
                rationale=_UNOBSERVED_RATIONALE))
            continue
        penalty = sum(_SEVERITY_PENALTY.get(c.severity, 1.5) for c in items)
        rationale = "; ".join(c.suggestion for c in items if c.suggestion)
        scores.append(VisualDimensionScore(
            dimension=dim,
            score=max(0.0, 10.0 - penalty),
            confidence=max(c.confidence for c in items),
            rationale=rationale,
        ))
    return scores


def _overall(dimension_scores: Sequence[VisualDimensionScore]) -> Tuple[float, float]:
    """置信度加权 overall（置信 0 的「未观察」维度不入权重，D6）。"""
    weight = sum(s.confidence for s in dimension_scores)
    if weight <= 0:
        return 10.0, 0.0
    score = sum(s.score * s.confidence for s in dimension_scores) / weight
    return score, min(1.0, weight / len(VisualDimension))


class VisualCriticEngine:
    """视觉评审引擎：快照提取 → 记忆化 → VLM 结构化评审 → 契约消毒 → 评分。"""

    def __init__(
        self,
        client: Optional[VLMClient] = None,
        *,
        extractor: Optional[SnapshotExtractor] = None,
        cache: Optional[CriticMemoCache] = None,
        client_absent_reason: str = "not_configured",
    ):
        self.client = client
        self.extractor = extractor or SnapshotExtractor()
        self.cache = cache or CriticMemoCache()
        #: client 缺席时的诚实归因（builder 依据配置态填写）。
        self.client_absent_reason = client_absent_reason

    async def evaluate(
        self,
        *,
        session_id: str,
        mapspec_fingerprint: str,
        image: Union[bytes, str],
        deterministic_summary: Optional[Dict[str, Any]] = None,
        mode: str = "record_only",
    ) -> VisualJudgeReport:
        """一次评审；永不抛异常（一切失效 = not_evaluated 报告）。"""
        start = time.monotonic()
        base = {
            "session_id": session_id,
            "mapspec_fingerprint": mapspec_fingerprint,
            "mode": mode,
        }
        try:
            snapshot = self.extractor.extract(image)
        except SnapshotError as exc:
            return self._finish(VisualJudgeReport.skipped(
                exc.reason, image_sha256="", provider=self._provider_name(),
                model=self._model_name(), **base), start)

        memo_key = (session_id, mapspec_fingerprint, snapshot.image_sha256)
        cached = self.cache.get(memo_key)
        if cached is not None:
            return cached

        report = await self._call_provider(
            snapshot, deterministic_summary, mode,
            session_id=session_id, mapspec_fingerprint=mapspec_fingerprint)
        return self._finish(report, start, memo_key)

    async def _call_provider(
        self,
        snapshot: MapSnapshot,
        deterministic_summary: Optional[Dict[str, Any]],
        mode: str,
        *,
        session_id: str,
        mapspec_fingerprint: str,
    ) -> VisualJudgeReport:
        base = {
            "session_id": session_id,
            "mapspec_fingerprint": mapspec_fingerprint,
            "image_sha256": snapshot.image_sha256,
            "image_width": snapshot.width,
            "image_height": snapshot.height,
            "pre_screen": snapshot.pre_screen,
            "provider": self._provider_name(),
            "model": self._model_name(),
            "mode": mode,
        }
        if self.client is None:
            return VisualJudgeReport.skipped(self.client_absent_reason, **base)
        request = VLMRequest(
            provider=self.client.provider,
            model=self.client.model,
            image_data_url=snapshot.data_url,
            user_context=_context_text(deterministic_summary),
            timeout_s=getattr(self.client, "timeout_s", 20.0),
        )
        try:
            raw_text = await self.client.critique(request)
        except CriticTimeout as exc:
            logger.warning("[VisualCritic] provider timeout: %s", type(exc).__name__)
            return VisualJudgeReport.skipped("provider_timeout", **base)
        except Exception as exc:  # noqa: BLE001 — 一切 provider 失败 fail-closed
            logger.warning(
                "[VisualCritic] provider error: %s", type(exc).__name__)
            return VisualJudgeReport.skipped("provider_error", **base)
        try:
            raw_items = parse_critic_output(raw_text)
        except CriticOutputError:
            logger.warning("[VisualCritic] invalid critic output shape")
            return VisualJudgeReport.skipped("invalid_output", **base)
        critiques = _sanitize_critiques(raw_items)
        dimension_scores = _derive_dimension_scores(critiques)
        overall, confidence = _overall(dimension_scores)
        return VisualJudgeReport(
            status="evaluated",
            critiques=critiques,
            dimension_scores=dimension_scores,
            overall_score=overall,
            overall_confidence=confidence,
            **base,
        )

    def _finish(
        self,
        report: VisualJudgeReport,
        start: float,
        memo_key: Optional[Tuple[str, str, str]] = None,
    ) -> VisualJudgeReport:
        report.duration_ms = int((time.monotonic() - start) * 1000)
        # 身份回填：快照失效路径没有 sha（键第三元空串），可缓存（确定性失效）。
        key = memo_key or (
            report.session_id, report.mapspec_fingerprint, report.image_sha256)
        self.cache.put(key, report)
        return report

    def _provider_name(self) -> str:
        return getattr(self.client, "provider", "") or ""

    def _model_name(self) -> str:
        return getattr(self.client, "model", "") or ""


def build_critic_engine() -> VisualCriticEngine:
    """从 env/settings 组装引擎（每评审按需构建；key 缺席 = 特性缺席）。"""
    config = resolve_provider_config()
    provider = config["provider"]
    if provider not in (PROVIDER_OPENAI_COMPATIBLE, PROVIDER_GEMINI):
        # 未知 provider = 配置缺席的诚实形态（不静默猜默认）。
        return VisualCriticEngine(client=None, client_absent_reason="not_configured")
    if is_placeholder_key(config["api_key"]):
        return VisualCriticEngine(client=None, client_absent_reason="no_api_key")
    timeout = float(config["timeout_s"])
    if provider == PROVIDER_GEMINI:
        client: VLMClient = GeminiVLMClient(
            config["base_url"], config["api_key"], config["model"], timeout)
    else:
        client = OpenAICompatVLMClient(
            config["base_url"], config["api_key"], config["model"], timeout)
    return VisualCriticEngine(client)


__all__ = [
    "CriticMemoCache",
    "VisualCriticEngine",
    "build_critic_engine",
    "visual_critic_runtime_enabled",
]
