"""Enforce a webhook byte budget before JSON/Pydantic parsing."""
from typing import Awaitable, Callable

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from starlette.responses import Response
from starlette.types import Message

MAX_WEBHOOK_BYTES = 8192


class BoundedWebhookRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()
        if not self.path.endswith("/webhook"):
            return original

        async def bounded(request: Request) -> Response:
            length = request.headers.get("content-length")
            if length:
                try:
                    too_large = int(length) > MAX_WEBHOOK_BYTES
                except ValueError:
                    raise HTTPException(status_code=400, detail="invalid_content_length")
                if too_large:
                    raise HTTPException(status_code=413, detail="webhook_body_too_large")
            cached = getattr(request, "_body", None)
            if cached is not None:
                if len(cached) > MAX_WEBHOOK_BYTES:
                    raise HTTPException(status_code=413, detail="webhook_body_too_large")
                return await original(request)
            total = 0

            async def receive() -> Message:
                nonlocal total
                message = await request.receive()
                if message["type"] == "http.request":
                    total += len(message.get("body", b""))
                    if total > MAX_WEBHOOK_BYTES:
                        raise HTTPException(status_code=413, detail="webhook_body_too_large")
                return message

            return await original(Request(request.scope, receive=receive))

        return bounded
