"""GIS Harness Kernel (ADR-0180) — host-neutral session/plan semantics.

Public surface (lazily resolved — see ``__getattr__``):
- ``GISSessionRuntime`` / ``get_runtime`` — session lifecycle owner
  (hydrate → begin_turn → evidence → checkpoint → end_turn) over the
  SessionPlan envelope store (``app.services.session_plan``).
- ``models`` — additive SessionPlan contract types (leaf module).
- ``projection`` — bounded read-only context lines.
- ``metrics`` — kernel counters.
- ``legacy_adapter`` — CanonicalPlan → SessionPlan projection (K4).

The kernel never hosts an agent loop: Pi (or the legacy engine) remains the
host; this layer owns only GIS session semantics.

Import-direction note: ``app.services.session_plan`` imports
``app.services.harness_kernel.models`` (additive field types), while
``runtime`` imports ``session_plan`` (composition). The package ``__init__``
therefore MUST stay lazy — eager re-exports would make
``import app.services.harness_kernel.models`` pull the full runtime graph and
re-enter the partially-initialized ``session_plan`` module (circular import).
"""

_LAZY = {
    "GISSessionRuntime": ("app.services.harness_kernel.runtime", "GISSessionRuntime"),
    "get_runtime": ("app.services.harness_kernel.runtime", "get_runtime"),
    "EVENT_KINDS": ("app.services.harness_kernel.models", "EVENT_KINDS"),
    "HarnessTurnContext": ("app.services.harness_kernel.models", "HarnessTurnContext"),
    "PHASE_TRANSITIONS": ("app.services.harness_kernel.models", "PHASE_TRANSITIONS"),
    # ── Settle coordinator（ADR-0223 / K9：host-neutral turn 结算编排）──
    "HarnessSettleInput": ("app.services.harness_kernel.settle", "HarnessSettleInput"),
    "HarnessSettleOutcome": ("app.services.harness_kernel.settle", "HarnessSettleOutcome"),
    "SETTLE_CAUSES": ("app.services.harness_kernel.settle", "SETTLE_CAUSES"),
    "SettleEffectReceipt": ("app.services.harness_kernel.settle", "SettleEffectReceipt"),
    "cause_from_host_signals": (
        "app.services.harness_kernel.settle", "cause_from_host_signals"
    ),
    "cause_from_legacy_outcome": (
        "app.services.harness_kernel.settle", "cause_from_legacy_outcome"
    ),
    "run_settle_effects": ("app.services.harness_kernel.settle", "run_settle_effects"),
    "settle_turn": ("app.services.harness_kernel.settle", "settle_turn"),
    "observe_lifecycle_parity": (
        "app.services.harness_kernel.settle", "observe_lifecycle_parity"
    ),
    "read_settle_snapshot": (
        "app.services.harness_kernel.settle", "read_settle_snapshot"
    ),
    "resolve_turn_refusal": (
        "app.services.harness_kernel.settle", "resolve_turn_refusal"
    ),
    "settle_class_for_status": (
        "app.services.harness_kernel.settle", "settle_class_for_status"
    ),
    "status_for_cause": ("app.services.harness_kernel.settle", "status_for_cause"),
    "PatchResult": ("app.services.harness_kernel.models", "PatchResult"),
    "PlanDecision": ("app.services.harness_kernel.models", "PlanDecision"),
    "PlanPatch": ("app.services.harness_kernel.models", "PlanPatch"),
    "PlanPatchKind": ("app.services.harness_kernel.models", "PlanPatchKind"),
    "PlanRecoveryMetadata": ("app.services.harness_kernel.models", "PlanRecoveryMetadata"),
    "PlanStep": ("app.services.harness_kernel.models", "PlanStep"),
    "PlanTurnRecord": ("app.services.harness_kernel.models", "PlanTurnRecord"),
    "StepEvidence": ("app.services.harness_kernel.models", "StepEvidence"),
    "StepStatus": ("app.services.harness_kernel.models", "StepStatus"),
    "TERMINAL_PHASES": ("app.services.harness_kernel.models", "TERMINAL_PHASES"),
    "TurnPhase": ("app.services.harness_kernel.models", "TurnPhase"),
    "TurnStatus": ("app.services.harness_kernel.models", "TurnStatus"),
    "project_runtime_phase": (
        "app.services.harness_kernel.phase_adapter", "project_runtime_phase"
    ),
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    return getattr(module, target[1])
