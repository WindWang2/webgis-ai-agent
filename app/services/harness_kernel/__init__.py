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
    "PatchResult": ("app.services.harness_kernel.models", "PatchResult"),
    "PlanDecision": ("app.services.harness_kernel.models", "PlanDecision"),
    "PlanPatch": ("app.services.harness_kernel.models", "PlanPatch"),
    "PlanPatchKind": ("app.services.harness_kernel.models", "PlanPatchKind"),
    "PlanRecoveryMetadata": ("app.services.harness_kernel.models", "PlanRecoveryMetadata"),
    "PlanStep": ("app.services.harness_kernel.models", "PlanStep"),
    "PlanTurnRecord": ("app.services.harness_kernel.models", "PlanTurnRecord"),
    "StepEvidence": ("app.services.harness_kernel.models", "StepEvidence"),
    "StepStatus": ("app.services.harness_kernel.models", "StepStatus"),
    "TurnStatus": ("app.services.harness_kernel.models", "TurnStatus"),
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    return getattr(module, target[1])
