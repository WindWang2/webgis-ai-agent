"""TURN-ID: prompt() must sign the same turn_id it publishes as active context."""
import asyncio
import re

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, _mint_turn_id
from app.api.routes.pi_tools import get_bridge_secret
from app.services.chat.pi_turn_context import TURN_CONTEXT_MARKER, verify_turn_token


@pytest.mark.asyncio
async def test_prompt_signed_token_shares_single_mint(monkeypatch):
    """A reminted turn_id after issue_turn_token makes /pi-tools/execute 409.

    Drive the shipped prompt() path: incrementing mint + captured RPC payload
    must share one id with _active_turn_context.
    """
    minted: list[str] = []

    def counting_mint() -> str:
        tid = f"turn-{len(minted) + 1:04d}"
        minted.append(tid)
        return tid

    monkeypatch.setattr(bridge_mod, "_mint_turn_id", counting_mint)

    captured: dict = {}

    class _Rpc:
        def __init__(self) -> None:
            self.events: asyncio.Queue = asyncio.Queue()

        async def request(self, command: str, data=None):
            captured["data"] = data
            captured["context"] = bridge_mod._active_turn_context
            await self.events.put({"type": "agent_end"})
            return {}

    bridge = PiBridge(rpc=_Rpc())
    result = await bridge.prompt("analyze the layer", session_id="sess-turn")

    assert result["sessionId"] == "sess-turn"
    assert minted == ["turn-0001"], minted
    message = captured["data"]["message"]
    match = re.search(rf"\[{re.escape(TURN_CONTEXT_MARKER)}:([^\]]+)\]", message)
    assert match, message
    payload = verify_turn_token(get_bridge_secret(), match.group(1))
    assert payload is not None
    assert payload["turn_id"] == "turn-0001"
    assert captured["context"] == ("sess-turn", "turn-0001")
    # mint helper itself still works after the patch is local to this test
    assert _mint_turn_id().startswith("turn-")
