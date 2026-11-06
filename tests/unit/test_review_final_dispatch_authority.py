"""Real registered tools validate server-owned required fields after injection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.tools.registry import ToolRegistry
from app.tools.composition_tools import register_composition_tools


@pytest.mark.asyncio
async def test_registered_save_template_hidden_required_session_uses_only_trusted_context(monkeypatch):
    registry = ToolRegistry()
    register_composition_tools(registry)
    schema = next(row for row in registry.get_schemas()
                  if row['function']['name'] == 'webgis_save_composition_template')
    assert 'session_id' not in schema['function']['parameters']['properties']
    assert 'session_id' not in schema['function']['parameters'].get('required', [])
    verify = AsyncMock(return_value=SimpleNamespace())
    spec = AsyncMock(return_value={})
    async def db_session():
        yield SimpleNamespace()
    monkeypatch.setattr('app.core.database.get_async_db', db_session)
    monkeypatch.setattr('app.services.auth_history_bridge.verify_session_owner', verify)
    monkeypatch.setattr('app.tools.composition_tools._spec_of', spec)
    for arguments in ({'name': 'saved'}, {'name': 'saved', 'session_id': 'victim'}):
        result = await registry.dispatch('webgis_save_composition_template', arguments, session_id='trusted')
        assert result['reason_codes'] == ['asset_payload_error']
        assert verify.await_args.args[1] == 'trusted'
        assert spec.await_args.args[0] == 'trusted'
    verify.reset_mock()
    result = await registry.dispatch('webgis_save_composition_template', {'session_id': 'victim'})
    assert result['code'] == 'VALIDATION_ERROR'
    verify.assert_not_awaited()
