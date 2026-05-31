"""F7: Skill upload AST validation must not be silently skipped on ImportError."""
import pytest
from unittest.mock import patch
from fastapi import HTTPException


def test_validate_or_reject_raises_on_import_error():
    """The extracted helper must raise HTTPException(500) when validator is unavailable."""
    from app.api.routes.config import _validate_or_reject_skill_code

    with patch.dict('sys.modules', {'app.tools.skills': None}):
        with pytest.raises(HTTPException) as exc_info:
            _validate_or_reject_skill_code("print('hello')")
        assert exc_info.value.status_code == 500
        assert "安全校验模块不可用" in exc_info.value.detail
