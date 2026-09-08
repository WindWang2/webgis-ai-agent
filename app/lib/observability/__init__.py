"""Structured Observability 事件面（ADR-0104 Wave 6）。"""
from app.lib.observability.events import (
    EVENT_CATALOG,
    LoggingSink,
    RingSink,
    STATUS_VOCABULARY,
    Sink,
    emit_event,
    event_digest,
    register_sink,
    reset_sinks,
)

__all__ = [
    "EVENT_CATALOG",
    "LoggingSink",
    "RingSink",
    "STATUS_VOCABULARY",
    "Sink",
    "emit_event",
    "event_digest",
    "register_sink",
    "reset_sinks",
]
