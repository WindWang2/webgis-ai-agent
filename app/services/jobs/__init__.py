"""统一 durable job 运行时（ADR-0052）。

Agent Task → Tool Step → Durable Job → Worker → Artifact 一条链上的公共语义：
生命周期状态机、贯穿式取消、持久化事实源、进度契约、脱敏与体积上限。

    from app.services.jobs import JobStatus, DurableJobStore, checkpoint, durable_job
"""
from app.services.jobs.artifacts import atomic_output, discard_partial
from app.services.jobs.cancellation import (
    CancellationRegistry,
    CancellationToken,
    OperationCancelled,
    cancellable,
    checkpoint,
    current_token,
    is_cancelled,
    registry,
    use_token,
)
from app.services.jobs.context import (
    JobOrigin,
    current_origin,
    new_run_id,
    new_turn_id,
    use_origin,
)
from app.services.jobs.lifecycle import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    CANCELLABLE_STATUSES,
    IMMUTABLE_STATUSES,
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    InvalidJobTransition,
    JobKind,
    JobStatus,
    can_transition,
    coerce_status,
    is_active,
    is_cancellable,
    is_retryable_status,
    is_terminal,
    sources_for,
    validate_transition,
)
from app.services.jobs.progress import (
    JobProgress,
    ProgressReporter,
    ProgressThrottle,
)
from app.services.jobs.store import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_STALE_AFTER_S,
    MAX_LIST_LIMIT,
    DurableJobStore,
    JobNotFound,
    worker_identity,
)
from app.services.jobs.views import (
    ACTIVE_POLL_INTERVAL_MS,
    JobCancelResponse,
    JobListResponse,
    JobRetryResponse,
    JobView,
    build_list_response,
    classify_job_id,
    job_from_agent_task,
    job_from_record,
)
from app.services.jobs.worker import (
    AlreadyFinished,
    DurableJobHandle,
    durable_job,
    finish_job,
)

__all__ = [
    # lifecycle
    "JobStatus",
    "JobKind",
    "InvalidJobTransition",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "ACTIVE_STATUSES",
    "CANCELLABLE_STATUSES",
    "IMMUTABLE_STATUSES",
    "RETRYABLE_STATUSES",
    "can_transition",
    "validate_transition",
    "coerce_status",
    "is_terminal",
    "is_active",
    "is_cancellable",
    "is_retryable_status",
    "sources_for",
    # cancellation
    "CancellationToken",
    "CancellationRegistry",
    "OperationCancelled",
    "checkpoint",
    "cancellable",
    "current_token",
    "use_token",
    "is_cancelled",
    "registry",
    # context
    "JobOrigin",
    "current_origin",
    "use_origin",
    "new_run_id",
    "new_turn_id",
    # progress
    "JobProgress",
    "ProgressThrottle",
    "ProgressReporter",
    # store
    "DurableJobStore",
    "JobNotFound",
    "worker_identity",
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "DEFAULT_STALE_AFTER_S",
    # views
    "ACTIVE_POLL_INTERVAL_MS",
    "JobView",
    "JobListResponse",
    "JobCancelResponse",
    "JobRetryResponse",
    "job_from_record",
    "job_from_agent_task",
    "build_list_response",
    "classify_job_id",
    # worker
    "durable_job",
    "finish_job",
    "DurableJobHandle",
    "AlreadyFinished",
    # artifacts
    "atomic_output",
    "discard_partial",
]
