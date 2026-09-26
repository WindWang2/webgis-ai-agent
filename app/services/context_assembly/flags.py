"""F04 kill-switches and bounded knobs (repo convention: ``GIS_*`` env flags).

Default ON with a one-way kill to the legacy byte-equivalent path — the same
``default-on + killable`` discipline as ``gis_context/flags.py``.
"""
from __future__ import annotations

import os
from typing import Optional


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def typed_context_assembly_enabled() -> bool:
    """Master switch. ``GIS_TYPED_CONTEXT_ASSEMBLY=0`` restores the pre-F04
    ``bind_turn_prompt`` path — byte-identical on benign content (hostile
    marker shapes are neutralized on both paths; wrap-fenced domains are the
    typed path's declared hardening). Tested retirement boundary."""
    return _flag("GIS_TYPED_CONTEXT_ASSEMBLY", "1")


def harness_state_block_enabled() -> bool:
    """``GIS_HARNESS_STATE_BLOCK=0`` drops the ``[执行状态]`` projection
    (the only *new* prompt block F04 introduces) — used by the byte-equivalence
    regression and by operators who want the exact legacy prompt shape."""
    return _flag("GIS_HARNESS_STATE_BLOCK", "1")


def governor_link_enabled() -> bool:
    """``GIS_CONTEXT_GOVERNOR_LINK=0`` disables the governor plan/settle calls
    (ledger recording stays on — it is the actual-cost source of truth)."""
    return _flag("GIS_CONTEXT_GOVERNOR_LINK", "1")


def enforce_mode() -> bool:
    """``GIS_CONTEXT_BUDGET_ENFORCE=1``: allocator caps become hard (omit
    instead of warn-only). Default observe-first (ADR-0182 provisional
    discipline): caps are enforced for pools but total-window overrun is
    reported, not asserted."""
    return _flag("GIS_CONTEXT_BUDGET_ENFORCE", "0")


def provider_parallelism() -> int:
    """Upper bound on concurrently-collecting providers (bounded fan-out)."""
    raw = os.getenv("GIS_CONTEXT_PROVIDER_PARALLELISM", "6")
    try:
        value = int(raw)
    except ValueError:
        return 6
    return max(1, min(8, value))


def provider_latency_budget_s() -> float:
    """Per-provider wall-clock budget; exceeded → provider skipped with a
    receipt reason (honest omission, never a blocked turn)."""
    raw = os.getenv("GIS_CONTEXT_PROVIDER_BUDGET_S", "2.5")
    try:
        return max(0.2, min(10.0, float(raw)))
    except ValueError:
        return 2.5


def max_total_context_chars() -> Optional[int]:
    """Absolute char ceiling on the rendered context blocks (safety net)."""
    raw = os.getenv("GIS_CONTEXT_MAX_CHARS", "")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None
