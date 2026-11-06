"""TURN-ID: prompt() must sign the same turn_id it publishes as active context."""
import asyncio
import re

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, _mint_turn_id
from app.api.routes.pi_tools import get_bridge_secret
from app.services.chat.pi_turn_context import TURN_CONTEXT_MARKER, verify_turn_token


@pytest.mark.asyncio
@pytest.mark.parametrize("other_active", [False, True])
async def test_prompt_signed_token_shares_single_mint(monkeypatch, other_active):
    """A reminted turn_id after issue_turn_token makes /pi-tools/execute 409.

    Drive the shipped prompt() path: incrementing mint + captured RPC payload
    must share one id with the actual session-keyed active turn entry.
    """
    minted: list[str] = []

    def counting_mint() -> str:
        tid = f"turn-{len(minted) + 1:04d}"
        minted.append(tid)
        return tid

    monkeypatch.setattr(bridge_mod, "_mint_turn_id", counting_mint)
    if other_active:
        monkeypatch.setitem(bridge_mod._active_turns, "unrelated-session", bridge_mod._ActiveTurnEntry(
            "unrelated-session", "unrelated-turn", None, context=("unrelated-session", "unrelated-turn"),
        ))

    captured: dict = {}

    class _Rpc:
        def __init__(self) -> None:
            self.events: asyncio.Queue = asyncio.Queue()

        async def request(self, command: str, data=None):
            captured["data"] = data
            entry = bridge_mod.get_active_turn_entry(data["sessionId"])
            assert entry is not None
            captured["context"] = entry.context
            captured["turn_id"] = entry.turn_id
            await self.events.put({"type": "agent_end", "willRetry": False})
            await self.events.put({"type": "agent_settled"})
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
    assert captured["turn_id"] == payload["turn_id"]
    if other_active:
        assert bridge_mod.get_active_turn_entry("unrelated-session").turn_id == "unrelated-turn"
    # mint helper itself still works after the patch is local to this test
    assert _mint_turn_id().startswith("turn-")
