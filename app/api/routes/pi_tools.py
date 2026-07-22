"""Pi tool execution endpoint.

Receives tool execution requests from Pi agent and dispatches to Python GIS tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pi-tools", tags=["pi-tools"])


class PiToolRequest(BaseModel):
    """Request from Pi agent to execute a tool."""
    toolCallId: str
    name: str
    arguments: dict[str, Any] = {}


class PiToolResponse(BaseModel):
    """Response to Pi agent from tool execution."""
    toolCallId: str
    content: list[dict[str, Any]]
    details: Any = None
    isError: bool = False


# Registry of available GIS tools for Pi
_PI_TOOL_REGISTRY: dict[str, Any] = {}


def register_pi_tool(name: str, func: Any) -> None:
    """Register a Python function as a Pi-callable tool."""
    _PI_TOOL_REGISTRY[name] = func


def get_pi_tools() -> dict[str, Any]:
    """Get all registered Pi tools."""
    return dict(_PI_TOOL_REGISTRY)


@router.post("/execute", response_model=PiToolResponse)
async def execute_tool(request: PiToolRequest) -> PiToolResponse:
    """Execute a GIS tool on behalf of Pi agent."""
    tool_name = request.name
    args = request.arguments

    if tool_name not in _PI_TOOL_REGISTRY:
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": f"Tool '{tool_name}' not found in GIS registry"}],
            isError=True,
        )

    try:
        func = _PI_TOOL_REGISTRY[tool_name]
        result = await func(**args) if asyncio.iscoroutinefunction(func) else func(**args)

        # Normalize result to Pi format
        if isinstance(result, dict):
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            details = result
        elif isinstance(result, str):
            content = [{"type": "text", "text": result}]
            details = result
        else:
            content = [{"type": "text", "text": str(result)}]
            details = result

        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=content,
            details=details,
            isError=False,
        )
    except Exception as e:
        logger.error(f"[PiTools] Tool {tool_name} failed: {e}")
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": f"Error: {str(e)}"}],
            isError=True,
        )
