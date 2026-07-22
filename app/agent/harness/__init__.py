"""Harness modules for the new Agent system.

This package provides thin wrappers around existing functionality,
maintaining the single-direction dependency: app/agent/ → app/services/ → app/tools/

Modules:
- skills: Re-export skill system from app/tools/skills.py
- system_prompt: Re-export SYSTEM_PROMPT from app/services/chat/prompt.py
- session: Session persistence helpers
- compaction: Context window compression utilities
"""