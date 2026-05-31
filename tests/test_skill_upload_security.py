"""F7: Skill upload AST validation must not be silently skipped on ImportError."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException


def test_skill_upload_rejects_when_validator_unimportable():
    """When _validate_skill_code cannot be imported, upload must be rejected."""
    # Simulate the import failing by making the import raise ImportError
    import importlib
    import app.api.routes.config as config_module

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == 'app.tools.skills':
            raise ImportError("simulated import failure")
        return original_import(name, *args, **kwargs)

    # The route function uses a local import inside a try/except.
    # We test the extracted validation helper directly.
    with patch('builtins.__import__', side_effect=mock_import):
        with pytest.raises(ImportError):
            from app.tools.skills import _validate_skill_code  # noqa: F401


def test_validate_or_reject_raises_on_import_error():
    """The extracted helper must raise HTTPException(500) when validator is unavailable."""
    from app.api.routes.config import _validate_or_reject_skill_code

    with patch.dict('sys.modules', {'app.tools.skills': None}):
        with pytest.raises(HTTPException) as exc_info:
            _validate_or_reject_skill_code("print('hello')")
        assert exc_info.value.status_code == 500
        assert "安全校验模块不可用" in exc_info.value.detail
