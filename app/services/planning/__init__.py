"""Planning foundation package (design-v3 §"New package app/services/planning/").

The canonical planning model (``CanonicalPlan``) is the source of truth for
plan state, persisted per session via ``PlanStore`` over the existing
``session_data_manager``. This slice ships the foundation only — integration
with the orchestrator / plan_mode / execution engine lands in a later slice.
"""
from .capability import (
    PRODUCES_REF_DOMAINS,
    ToolCapability,
    capability_of,
    validate_plan_capabilities,
)
from .deps import (
    REF_PATTERN,
    MissingRefError,
    resolve_arg_refs,
    validate_static_refs,
)
from .followup import (
    CONTINUATION_KEYWORDS,
    REF_REUSE_KEYWORDS,
    STYLE_KEYWORDS,
    FollowUpKind,
    classify_followup,
)
from .models import (
    TERMINAL_STATUSES,
    CanonicalPlan,
    CanonicalStep,
    FailureClass,
    PlanStatus,
    RecoveryAction,
    StepError,
    StepStatus,
)
from .recovery import RECOVERY_POLICY, classify_error, recovery_action_for
from .store import CURRENT_PLAN_ALIAS, PlanStore, plan_store

__all__ = [
    # models
    "PlanStatus",
    "TERMINAL_STATUSES",
    "StepStatus",
    "FailureClass",
    "RecoveryAction",
    "StepError",
    "CanonicalStep",
    "CanonicalPlan",
    # store
    "PlanStore",
    "plan_store",
    "CURRENT_PLAN_ALIAS",
    # capability
    "ToolCapability",
    "capability_of",
    "validate_plan_capabilities",
    "PRODUCES_REF_DOMAINS",
    # deps
    "REF_PATTERN",
    "MissingRefError",
    "validate_static_refs",
    "resolve_arg_refs",
    # recovery
    "classify_error",
    "RECOVERY_POLICY",
    "recovery_action_for",
    # followup
    "FollowUpKind",
    "classify_followup",
    "STYLE_KEYWORDS",
    "CONTINUATION_KEYWORDS",
    "REF_REUSE_KEYWORDS",
]
