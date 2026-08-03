"""CompileCoordinator (Compatibility Re-export).

Re-exports `compile_via_cli` and `validate` from `app.services.mapspec.coordinator`.
"""
from app.services.mapspec.coordinator import compile_via_cli, validate

__all__ = ["compile_via_cli", "validate"]
