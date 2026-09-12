"""Visual Judge（ADR-0158）—— L5 goal_satisfaction 的结构化视觉裁判 seam。

定位（docs/cartographic-closed-loop.md 的证据阶梯）：

- 只有确定性规则无法回答的图面问题（可读性 / 色彩可分辨 / 构图平衡 /
  信息密度 / 整饰完整性）交给视觉裁判；确定性检查永不经此路径。
- 严格 fail-closed：judge 未配置 / 无截图 / 超时 / 抛错 / 输出不合法 ⇒
  ``VisualJudgeReport.status = "not_evaluated"``（沿用
  ``not_evaluated_policy_fail`` 语义）——绝不伪造 pass，也不因缺席而 fail。
- 证据层级：``evidence_class: "visual"`` 的结论**不得单独**判 L4/L5 PASS
  （只能降级或在 L4 锚点上补充）；deterministic > heuristic > visual。
- record-only 默认：视觉结论只落入 ``CartographicReviewEvidence.visual_evidence``
  与 checks 证据行（``degradation_only``），不改写既有三态 verdict。阻断模式
  是 10 线 ratchet 基线稳定后的显式切换（见 ADR-0158 切换条件）。
- 限流：每份证据状态至多一次 VLM 调用 —— (session, fingerprint, 截图摘要)
  记忆化 + 单次调用无重试；重复评估命中记忆，不再外呼。
- 与 ``app/services/gis_harness/visual_evaluator.py``（W9 seam，01/02 线域）
  的关系：本模块是 **生产 judge + harness 接线**（该 seam 的一个可用实现
  形态），不修改那个文件；注入 vocabulary（``"module:callable"``）保持一致。
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 视觉裁判维度（P2 最低集）。评审判据输出之外的维度一律丢弃（严格白名单）。
VISUAL_DIMENSIONS: Tuple[str, ...] = (
    "readability",
    "color_discriminability",
    "composition_balance",
    "information_density",
    "polish_completeness",
)
_SEVERITIES = {"info", "warning", "error"}
_MAX_CRITIQUES = 12
_MAX_TEXT = 300
_MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
_SCREENSHOT_NAME = "map.png"
#: headless runtime validator（webgis_runtime_validate）结果里的截图目录键。
_RUNTIME_DIR_KEY = "runtime_dir"

_FORBIDDEN_KEYS = {
    "mutation", "mutations", "intent", "intents", "mapspec",
    "spec", "patch", "layers", "components_patch", "apply",
}

_RECORD_ONLY_MODE = "record_only"
#: record-only 是出厂默认；阻断模式由 10 线 ratchet 基线稳定后显式切换。
VISUAL_JUDGE_MODE = (_RECORD_ONLY_MODE, "block")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def visual_judge_mode() -> str:
    mode = _env("CARTO_VISUAL_JUDGE_MODE")
    return mode if mode in VISUAL_JUDGE_MODE else _RECORD_ONLY_MODE


@dataclass
class VisualCritique:
    """一条结构化视觉批评（evidence_class = visual）。"""
    dimension: str
    severity: str = "warning"
    suggestion: str = ""
    evidence_class: str = "visual"
    confidence: Optional[float] = None
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "severity": self.severity,
            "suggestion": self.suggestion,
            "evidence_class": self.evidence_class,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class VisualJudgeReport:
    """一次视觉裁判的诚实结论（evaluated 或 not_evaluated + 原因）。"""
    status: str = "not_evaluated"          # evaluated | not_evaluated
    reason: str = "visual_judge_disabled"  # not_evaluated 时的机器可读原因
    critiques: List[VisualCritique] = field(default_factory=list)
    source: str = "visual_judge"
    mode: str = _RECORD_ONLY_MODE
    fingerprint: str = ""
    screenshot_digest: str = ""
    duration_ms: int = 0
    model: str = ""

    @property
    def evaluated(self) -> bool:
        return self.status == "evaluated"

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.critiques if c.severity == "error")

    def to_summary(self) -> Dict[str, Any]:
        """有界摘要（进 ``CartographicReviewEvidence.visual_evidence``）。"""
        return {
            "evidence_class": "visual",
            "source": self.source,
            "status": self.status,
            "reason": self.reason if not self.evaluated else "",
            "mode": self.mode,
            "critiques": [c.to_dict() for c in self.critiques],
            "error_count": self.error_count,
            "warning_count": sum(
                1 for c in self.critiques if c.severity == "warning"
            ),
            "fingerprint": self.fingerprint[:80],
            "screenshot_digest": self.screenshot_digest[:32],
            "duration_ms": self.duration_ms,
            "model": self.model[:80],
        }


def resolve_injected_judge() -> Optional[Callable[..., Any]]:
    """加载 ``CARTO_VISUAL_JUDGE="module:callable"``；未配置/加载失败 → None。"""
    spec = _env("CARTO_VISUAL_JUDGE")
    if not spec or ":" not in spec:
        return None
    module_name, func_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        fn = getattr(module, func_name, None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 — 注入失败 = 特性缺席（诚实降级）
        logger.warning("[VisualJudge] load failed for %r", spec, exc_info=True)
        return None


# ── 内置 VLM judge（OpenAI 兼容 chat completions + image_url） ──────────

_JUDGE_SYSTEM_PROMPT = (
    "You are a cartographic visual judge. Look at the rendered map screenshot "
    "and report ONLY what you can actually see, as strict JSON: "
    '{"critiques": [{"dimension", "severity", "suggestion"}]}. '
    f'dimension must be exactly one of {list(VISUAL_DIMENSIONS)}; severity one of '
    '{"info", "warning", "error"}; suggestion is one short actionable sentence. '
    "Report at most 10 items. Never invent layers or data you cannot see."
)


def _resolve_vlm_config() -> Tuple[str, str, str]:
    """(base_url, api_key, model)。键缺失返回占位符空串由调用方 fail-closed。"""
    from app.core.config import settings

    base_url = _env("CARTO_VISUAL_JUDGE_BASE_URL") or settings.LLM_BASE_URL
    api_key = _env("CARTO_VISUAL_JUDGE_API_KEY") or settings.LLM_API_KEY
    model = _env("CARTO_VISUAL_JUDGE_MODEL") or settings.LLM_MODEL
    return base_url, api_key, model


def _is_placeholder_key(api_key: str) -> bool:
    return (not api_key) or api_key in {"your-api-key-here", "sk-...", "CHANGE_ME"}


def vlm_judge_callable():
    """返回内置 VLM judge 协程；未启用/无 key → None（fail-closed 由调用方落账）。"""
    if _env("CARTO_VISUAL_JUDGE_VLM").lower() not in ("1", "true", "yes"):
        return None
    _, api_key, _ = _resolve_vlm_config()
    if _is_placeholder_key(api_key):
        return None
    return _vlm_judge


async def _vlm_judge(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一次 VLM 调用（无重试）。任何失败向上抛出 → not_evaluated。"""
    import httpx

    base_url, api_key, model = _resolve_vlm_config()
    screenshot = snapshot.get("screenshot") or {}
    data_url = screenshot.get("data_url") or ""
    if not data_url:
        raise ValueError("snapshot has no screenshot data_url")
    timeout_s = float(_env("CARTO_VISUAL_JUDGE_TIMEOUT_S") or "20")
    payload = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Judge this rendered map image."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        response = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    content = str(
        ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    )
    return _parse_judge_json(content)


def _parse_judge_json(content: str) -> List[Dict[str, Any]]:
    """从模型输出提取 critiques 数组；不合法形状抛 ValueError（→ not_evaluated）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge output has no JSON object")
    parsed = json.loads(text[start:end + 1])
    critiques = parsed.get("critiques") if isinstance(parsed, dict) else None
    if not isinstance(critiques, list):
        raise ValueError("judge output missing critiques list")
    return [item for item in critiques if isinstance(item, dict)]


# ── 快照组装与记忆化 ─────────────────────────────────────────────────────

def _find_screenshot_path(
    cartography, results_by_id: Dict[str, Dict[str, Any]]
) -> Optional[Path]:
    """从与当前指纹匹配的 headless runtime 证据目录里定位 map.png。"""
    fingerprint = cartography.mapspec_fingerprint
    if not fingerprint:
        return None
    for call in reversed(list(results_by_id.values())):
        result = call.get("result") if isinstance(call.get("result"), dict) else {}
        if result.get("mapspec_fingerprint") != fingerprint:
            continue
        runtime_dir = str(result.get(_RUNTIME_DIR_KEY) or "")
        if not runtime_dir:
            continue
        candidate = Path(runtime_dir) / _SCREENSHOT_NAME
        if candidate.is_file():
            return candidate
    return None


# 进程级记忆化：(session_id, fingerprint, 截图内容摘要) → report。评估面
# 高频重入（观察/ACK 双入口 + 缓存失效），记忆化保证每份证据状态至多一次
# 真实外呼（§0.4 限流纪律），LRU 有界。
_judge_memo: "OrderedDict[Tuple[str, str, str], VisualJudgeReport]" = OrderedDict()
_JUDGE_MEMO_LIMIT = 64


def _memo_get(key: Tuple[str, str, str]) -> Optional[VisualJudgeReport]:
    report = _judge_memo.get(key)
    if report is not None:
        _judge_memo.move_to_end(key)
    return report


def _memo_put(key: Tuple[str, str, str], report: VisualJudgeReport) -> None:
    _judge_memo[key] = report
    _judge_memo.move_to_end(key)
    while len(_judge_memo) > _JUDGE_MEMO_LIMIT:
        _judge_memo.popitem(last=False)


def sanitize_critiques(raw_items: List[Any]) -> List[VisualCritique]:
    """judge 输出白名单校验（结构对齐 W9 seam 的 _sanitize_finding 纪律）。"""
    out: List[VisualCritique] = []
    for item in raw_items[:_MAX_CRITIQUES]:
        if not isinstance(item, dict):
            continue
        if any(key in item for key in _FORBIDDEN_KEYS):
            continue  # 任何改图意图字段 ⇒ 整条判废
        dimension = str(item.get("dimension") or "")
        if dimension not in VISUAL_DIMENSIONS:
            continue
        severity = str(item.get("severity") or "warning")
        if severity not in _SEVERITIES:
            severity = "warning"
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        out.append(VisualCritique(
            dimension=dimension,
            severity=severity,
            suggestion=str(item.get("suggestion") or "")[:_MAX_TEXT],
            confidence=confidence,
            evidence=str(item.get("evidence") or "")[:_MAX_TEXT],
        ))
    return out


# ── harness 接线入口 ─────────────────────────────────────────────────────

async def attach_visual_judgement(
    session_id: str,
    cartography,  # CartographicReviewEvidence
    results_by_id: Dict[str, Dict[str, Any]],
) -> None:
    """评审信任边界内的视觉裁判接线（P2/P3）。

    截图来自既有头照设施（headless validator 的 ``runtime_dir/map.png``）或
    测试注入的 judge；结论只追加 ``evidence_class: visual`` 的证据行与
    ``visual_evidence`` 摘要，不改写既有三态 verdict（record-only）。
    """
    if cartography.mapspec_fingerprint is None:
        return  # 无可信世代 ⇒ 无视觉判定对象（not_evaluated 由 L5 推导披露）
    judge = resolve_injected_judge()
    if judge is None:
        if _env("CARTO_VISUAL_JUDGE"):
            # 注入 spec 存在但加载失败 —— resolve 内部已告警，这里诚实落账。
            unavailable_reason = "judge_load_failed"
        elif _env("CARTO_VISUAL_JUDGE_VLM").lower() in ("1", "true", "yes"):
            unavailable_reason = "no_api_key"
        else:
            unavailable_reason = "visual_judge_disabled"
        judge = vlm_judge_callable()
        if judge is None:
            cartography.visual_evidence.append({
                "evidence_class": "visual",
                "source": "visual_judge",
                "status": "not_evaluated",
                "reason": unavailable_reason,
                "mode": visual_judge_mode(),
                "fingerprint": cartography.mapspec_fingerprint[:80],
            })
            return

    screenshot_path = _find_screenshot_path(cartography, results_by_id)
    if screenshot_path is None:
        # 测试/离线注入点：生产路径未设置该 env 时保持 no_screenshot。
        screenshot_path = _fixture_screenshot_path()
    reason = "no_screenshot"
    data_url = ""
    digest = ""
    if screenshot_path is not None:
        try:
            screenshot_bytes = screenshot_path.read_bytes()
        except OSError:
            screenshot_bytes = b""
        if screenshot_bytes and len(screenshot_bytes) <= _MAX_SCREENSHOT_BYTES:
            data_url = (
                "data:image/png;base64,"
                + base64.b64encode(screenshot_bytes).decode("ascii")
            )
            digest = hashlib.sha256(screenshot_bytes).hexdigest()
        elif screenshot_bytes:
            reason = "screenshot_oversized"
        else:
            reason = "screenshot_unreadable"
    memo_key = (session_id, cartography.mapspec_fingerprint, digest)
    if digest:
        cached = _memo_get(memo_key)
        if cached is not None:
            _apply_report(cartography, cached)
            return

    report = VisualJudgeReport(
        mode=visual_judge_mode(),
        fingerprint=cartography.mapspec_fingerprint,
        screenshot_digest=digest,
    )
    if not data_url:
        report.reason = reason
        _apply_report(cartography, report)
        return
    start = time.monotonic()
    try:
        snapshot = {
            "session_id": session_id,
            "fingerprint": cartography.mapspec_fingerprint,
            "screenshot": {"kind": "png_data_url", "data_url": data_url},
            "deterministic_summary": {
                "status": cartography.status,
                "desired_status": cartography.desired_status,
                "failed_rules": [
                    check.get("rule")
                    for check in cartography.checks
                    if isinstance(check, dict) and check.get("status") == "fail"
                ][:12],
            },
        }
        raw = judge(snapshot)
        if hasattr(raw, "__await__"):
            raw = await raw
        critiques = sanitize_critiques(list(raw or []))
        report.status = "evaluated"
        report.reason = ""
        report.critiques = critiques
        if getattr(judge, "__name__", "") == "_vlm_judge":
            _, _, model_name = _resolve_vlm_config()
            report.model = model_name
    except Exception as exc:  # noqa: BLE001 — 任何失败 = not_evaluated（fail-closed）
        logger.warning(
            "[VisualJudge] evaluation failed session=%s: %s",
            session_id, type(exc).__name__,
        )
        report.status = "not_evaluated"
        report.reason = "provider_error"
    finally:
        report.duration_ms = int((time.monotonic() - start) * 1000)
    if digest:
        _memo_put(memo_key, report)
    _apply_report(cartography, report)


def _fixture_screenshot_path() -> Optional[Path]:
    """测试/离线场景的显式截图注入点（生产路径不设置该 env）。"""
    value = _env("CARTO_VISUAL_JUDGE_SCREENSHOT")
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def _apply_report(cartography, report: VisualJudgeReport) -> None:
    """把 judge 结论落为证据行（不改写三态 verdict —— record-only）。"""
    cartography.visual_evidence.append(report.to_summary())
    if not report.evaluated:
        # 不可评估也要显式可见：一条 not_evaluated 的 visual 检查行。
        cartography.checks.append({
            "rule": "VISUAL_ORACLE",
            "status": "not_evaluated",
            "evidence_class": "visual",
            "severity": "info",
            "repairability": "not_repairable",
            "evidence": {
                "reason": report.reason,
                "mode": report.mode,
                "fingerprint": report.fingerprint[:80],
            },
            "message": "Visual judgement was not available for this generation.",
        })
        return
    for critique in report.critiques:
        cartography.checks.append({
            "rule": f"VISUAL_{critique.dimension.upper()}",
            "status": (
                "fail" if critique.severity == "error"
                else "warning" if critique.severity == "warning"
                else "pass"
            ),
            "evidence_class": "visual",
            "severity": critique.severity,
            # visual 证据默认只降级披露；仅色彩可分辨性映射到 AUTO_SAFE
            # 运行时修复动作（change_palette 轮换，见 runtime_repair 扩展）。
            "repairability": (
                "auto_safe" if critique.dimension == "color_discriminability"
                and critique.severity == "error"
                else "not_repairable"
            ),
            "suggested_fix": (
                {"operation": "rotate_palette", "dimension": critique.dimension}
                if critique.dimension == "color_discriminability"
                and critique.severity == "error"
                else None
            ),
            "evidence": {
                "suggestion": critique.suggestion,
                "confidence": critique.confidence,
                "detail": critique.evidence,
                "mode": report.mode,
                "screenshot_digest": report.screenshot_digest[:32],
            },
            "message": f"Visual critique ({critique.dimension}): {critique.suggestion}",
        })


# ── L5 推导（P3） ────────────────────────────────────────────────────────

def derive_goal_satisfaction(cartography) -> Dict[str, Any]:
    """从可信制图证据推导 L5 ``goal_satisfaction``（fail-closed）。

    - 无已评估的视觉结论 ⇒ ``not_evaluated``（携带机器可读原因）；
    - 有视觉结论但 L4 锚点未过 ⇒ ``not_evaluated``（visual 不得替 L4 背书）；
    - L4 通过 ∧ 视觉 error 级批评 ⇒ ``fail``（诚实降级证据）；
    - L4 通过 ∧ 无 error 级批评 ⇒ ``pass``。
    """
    summaries = [
        item for item in cartography.visual_evidence
        if isinstance(item, dict) and item.get("source") == "visual_judge"
    ]
    # 同代多次评估会累积多条摘要 —— 取最新一条（旧 not_evaluated 不得遮蔽
    # 新评估结论；visual_evidence 追加序即时间序）。
    summary = summaries[-1] if summaries else None
    if not isinstance(summary, dict) or summary.get("status") != "evaluated":
        return {
            "status": "not_evaluated",
            "reason": str(
                (summary or {}).get("reason") or "no_visual_judgement"
            ),
        }
    if not cartography.passed:
        return {"status": "not_evaluated", "reason": "l4_anchor_not_passed"}
    if summary.get("error_count"):
        return {
            "status": "fail",
            "reason": "visual_error_critique",
            "dimensions": [
                c.get("dimension")
                for c in summary.get("critiques", [])
                if isinstance(c, dict) and c.get("severity") == "error"
            ],
        }
    return {"status": "pass", "reason": "visual_judge_concurred"}


__all__ = [
    "VISUAL_DIMENSIONS",
    "VisualCritique",
    "VisualJudgeReport",
    "attach_visual_judgement",
    "derive_goal_satisfaction",
    "resolve_injected_judge",
    "sanitize_critiques",
    "visual_judge_mode",
    "vlm_judge_callable",
]
