"""System prompt harness — re-exports SYSTEM_PROMPT from app/services/chat/prompt.py.

This thin wrapper maintains the single-direction dependency:
app/agent/harness/ → app/services/chat/

Usage:
    from app.agent.harness.system_prompt import SYSTEM_PROMPT, construct_self_healing_message
"""

from app.services.chat.prompt import SYSTEM_PROMPT, construct_self_healing_message

__all__ = ["SYSTEM_PROMPT", "construct_self_healing_message"]