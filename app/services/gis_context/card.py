"""GIS context card — bounded LLM read model for the layered scopes
(ADR-0206 D5 / Direction 06 M5).

One ``[GIS_CONTEXT]`` block merging mission working context and project
reuse candidates. Discipline: hard char budget (1600) / item caps /
deterministic order / honest omission receipt / empty context = empty
string (never inject an empty block).

Fencing: the rendered body is wrapped in **one**
``<untrusted_gis_context>`` element with a single HTML-escape pass —
values come from user/session stores and are never trusted, but per-value
fence tags would spend most of the char budget on markup (review P2-6).

Stale facts are **filtered, not rendered** — only the stale-reason summary
lines appear, so a drifted conclusion can never masquerade as current.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.chat.context.formatters import _untrusted
from app.services.gis_context.working_context import GISWorkingContext

CHAR_BUDGET = 1600
MAX_ITEMS = 24
MAX_STALE_LINES = 3
MAX_DECISION_LINES = 3
MAX_FINDING_LINES = 3
MAX_REUSE_LINES = 3

_VERDICT_LABEL = {
    "exact": "✓可复用",
    "recompute_partial": "◐需部分重算",
    "not_reusable": "✗不可复用",
}


@dataclass
class ContextCardReceipt:
    """Bounded observability for the injection site (no goal text, no PII).

    ADR-0215 D8 adds reason-grade fields: the stale-reason kind
    distribution, why reuse candidates were rejected, and the turn's
    revalidation outcome counts (restored / rejected)."""

    hit: bool = False
    miss_reason: str = ""
    stale_fields: int = 0
    reuse_exact: int = 0
    reuse_partial: int = 0
    reuse_rejected: int = 0
    chars: int = 0
    truncated: bool = False
    skipped_reason: str = ""
    notes: List[str] = field(default_factory=list)
    #: Distribution of stale reasons by change kind, e.g.
    #: ["AOI_CHANGED", "CRS_CHANGED"] (bounded, deduped, insertion order).
    stale_reason_kinds: List[str] = field(default_factory=list)
    #: First stale-cause per non-exact reuse candidate (bounded) — why
    #: reuse did not happen this turn.
    reuse_reject_reasons: List[str] = field(default_factory=list)
    #: Revalidation receipts resolved this turn (engine-produced).
    rtv_restored: int = 0
    rtv_rejected: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "hit": self.hit,
            "miss_reason": self.miss_reason[:64],
            "stale_fields": self.stale_fields,
            "reuse_exact": self.reuse_exact,
            "reuse_partial": self.reuse_partial,
            "reuse_rejected": self.reuse_rejected,
            "chars": self.chars,
            "truncated": self.truncated,
            "skipped_reason": self.skipped_reason[:64],
            "notes": [n[:48] for n in self.notes[:6]],
            "stale_reason_kinds": [k[:24] for k in self.stale_reason_kinds[:6]],
            "reuse_reject_reasons": [r[:48] for r in self.reuse_reject_reasons[:4]],
            "rtv_restored": self.rtv_restored,
            "rtv_rejected": self.rtv_rejected,
        }


def _v(value: object, max_len: int = 64) -> str:
    """Bound a value for raw inclusion (whole body is fenced once)."""
    return str(value if value is not None else "")[:max_len]


def render_gis_context_card(
    wc: Optional[GISWorkingContext],
    *,
    reuse_candidates: Optional[List[Any]] = None,
    char_budget: int = CHAR_BUDGET,
    max_items: int = MAX_ITEMS,
    receipt: Optional[ContextCardReceipt] = None,
) -> str:
    """Render the bounded card; ``None``/empty context → "" (no block)."""
    rc = receipt if receipt is not None else ContextCardReceipt()
    if wc is None:
        return ""

    mission_header = f"mission={_v(wc.mission_id, 32)} rev={wc.revision}"
    lines: List[str] = []
    used = len(mission_header) + 1
    items = 0
    omitted = 0

    def try_line(line: str) -> bool:
        nonlocal used, items, omitted
        if items >= max_items or used + len(line) + 1 > char_budget:
            omitted += 1
            return False
        lines.append(line + "\n")
        used += len(line) + 1
        items += 1
        return True

    # 1) accepted basis (known fields only; stale ones annotated, not hidden)
    b = wc.basis
    basis_bits: List[str] = []
    if b.aoi_name:
        basis_bits.append(f"AOI={_v(b.aoi_name, 40)}")
    elif b.aoi_bbox:
        basis_bits.append("AOI=bounds✓")
    if b.time_period:
        basis_bits.append(f"T={_v(b.time_period, 32)}")
    if b.crs:
        basis_bits.append(f"CRS={_v(b.crs, 24)}")
    if b.measure_field:
        stat = f"/{_v(b.measure_statistic, 16)}" if b.measure_statistic else ""
        basis_bits.append(f"度量={_v(b.measure_field, 32)}{stat}")
    if b.recipe_id:
        basis_bits.append(f"recipe={_v(b.recipe_id, 40)}")
    if b.export_format:
        basis_bits.append(f"导出={_v(b.export_format, 16)}")
    if basis_bits:
        mark = " ⚠需复核" if wc.stale else ""
        try_line("基准: " + " · ".join(basis_bits) + mark + f"（数据集×{len(b.datasets)}）")

    # 2) stale reasons — the engine's verdicts surface verbatim (≤3)
    for fld in sorted(wc.stale)[:MAX_STALE_LINES]:
        try_line(f"⚠ 失效 {_v(fld, 32)}: {_v(wc.stale[fld], 64)}")
    # Reason-kind distribution for the receipt (bounded, deduped, order-stable).
    for reason in wc.stale.values():
        kind = str(reason or "").split(":", 1)[0][:24]
        if kind and kind not in rc.stale_reason_kinds:
            rc.stale_reason_kinds.append(kind)

    # 3) accepted assumptions / unresolved constraints (stale-marked, not dropped)
    for label, records in (
        ("已确认假设", wc.accepted_assumptions),
        ("未解决约束", wc.unresolved_constraints),
    ):
        for d in records[:MAX_DECISION_LINES]:
            mark = " ⚠需复核" if d.stale_basis else ""
            try_line(f"{label}: {_v(d.text, 64)}{mark}")

    # 4) user edits — user-wins notice (aggregate line)
    if wc.user_edits:
        kinds = sorted({e.kind for e in wc.user_edits})
        try_line(
            f"用户已手动编辑 ×{len(wc.user_edits)}（{_v('/'.join(kinds), 32)}）"
            "—— 用户操作优先，勿静默覆盖"
        )

    # 5) verified findings (live statuses only; staled ones already surfaced
    #    via §2 stale reasons)
    active_findings = [
        f for f in wc.findings
        if f.status not in ("stale", "contradicted", "unsupported")
    ][:MAX_FINDING_LINES]
    for f in active_findings:
        try_line(f"已核实: {_v(f.claim_id, 32)} [{_v(f.status, 16)}]")

    # 6) project reuse candidates (verdict + readable reason)
    for cand in (reuse_candidates or [])[:MAX_REUSE_LINES]:
        try:
            entry = cand.entry
            verdict = _VERDICT_LABEL.get(cand.verdict, cand.verdict)
            reason = ""
            causes = list(getattr(cand, "stale_causes", []) or []) + list(
                getattr(cand, "reasons", []) or [])
            if causes:
                reason = f"（{_v(causes[0], 40)}）"
            if cand.verdict == "exact":
                rc.reuse_exact += 1
            else:
                if cand.verdict == "recompute_partial":
                    rc.reuse_partial += 1
                else:
                    rc.reuse_rejected += 1
                causes = [c for c in causes if c]
                if causes and causes[0] not in rc.reuse_reject_reasons:
                    rc.reuse_reject_reasons.append(causes[0])
            try_line(
                f"项目复用 {verdict}: {_v(entry.subject, 40)}"
                f" → {_v(entry.authority_store, 16)}:{_v(entry.authority_id, 32)}{reason}"
            )
        except Exception:  # noqa: BLE001 — 单行失败不炸整块
            continue

    rc.stale_fields = len(wc.stale)
    rc.hit = True
    rc.truncated = omitted > 0

    if not lines:
        # Nothing renderable (all sections empty) — never inject an empty block.
        rc.hit = False
        rc.miss_reason = "empty_context"
        return ""
    if omitted:
        lines.append(f"…（{omitted} 条目超预算省略）\n")

    # Escape first, then hard-bound the escaped output — HTML-escaping can
    # inflate text up to 5x, so bounding the input alone would leave the
    # rendered block size soft (review follow-up).
    body = _untrusted(mission_header + "\n" + "".join(lines), char_budget * 6)
    body = body[:char_budget + 256]
    text = f"<gis_context>\n<untrusted_gis_context>{body}</untrusted_gis_context>\n</gis_context>\n"
    rc.chars = len(text)
    return text


__all__ = [
    "CHAR_BUDGET",
    "ContextCardReceipt",
    "render_gis_context_card",
]
