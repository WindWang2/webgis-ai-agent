# Design Doc: Spatial Analysis Library Interface Standardization (Phase 1)

## Goal
Standardize the tool interface to explicitly support LLM interaction (dual output of data vs. narration) and add self-healing error handling to the tool registry.

## Architecture

### 1. GeoAnalysisResult Interface
A new interface will be introduced in `app/lib/geoprocessing/interface.py` to encapsulate tool results.

```python
from dataclasses import dataclass, asdict
from typing import Any, Optional

@dataclass
class GeoAnalysisResult:
    success: bool
    data: Any
    summary: str
    error_type: Optional[str] = None
    correction_hint: Optional[str] = None

    def to_llm_response(self) -> dict:
        """Converts result to a format ChatEngine can digest."""
        # Standard format for LLM consumption
        # If data is too large, it might be truncated or summarized in a real implementation,
        # but for Phase 1 we will return it as is or handle it simply.
        return {
            "success": self.success,
            "summary": self.summary,
            "data": self.data,
            "error_type": self.error_type,
            "correction_hint": self.correction_hint
        }
```

### 2. Tool Registry Self-Healing
Update `ToolRegistry.dispatch` in `app/tools/registry.py` to:
- Detect if a tool returns `GeoAnalysisResult`.
- If so, call `to_llm_response()`.
- Wrap exceptions into "correction prompts" to help the LLM self-heal.

#### Error Handling Strategy
When a tool raises common errors (`ValueError`, `KeyError`):
- `ValueError`: Often means invalid parameters.
- `KeyError`: Often means missing fields in datasets.
The registry will catch these and return a `GeoAnalysisResult` with `success=False` and a `correction_hint`.

## Testing Plan
- **Unit Tests for `GeoAnalysisResult`**: Verify dataclass behavior and `to_llm_response`.
- **Integration Tests for `ToolRegistry.dispatch`**:
  - Test tool returning `GeoAnalysisResult`.
  - Test tool returning raw data (backwards compatibility).
  - Test tool raising `ValueError` and verifying the correction hint in the response.
  - Test tool raising `KeyError`.

## Proposed Changes
1. Create `app/lib/geoprocessing/interface.py`.
2. Update `app/tools/registry.py`.
3. Add tests in `tests/test_geoprocessing_interface.py` and update `tests/test_tool_registry.py` (if it exists) or create `tests/test_registry_self_healing.py`.
