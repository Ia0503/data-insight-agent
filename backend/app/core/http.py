"""Bound actual upload bytes before multipart parsing and correlate failed requests."""

import logging
from time import perf_counter
from uuid import uuid4

from app.core.config import get_settings
from app.core.logging import exception_location
from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestBoundaryMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status = 500
        response_started = False
        upload = scope["method"] == "POST" and scope["path"].rstrip("/").endswith("/sources")
        limit = get_settings().max_upload_bytes + 1024 * 1024
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if upload and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Starlette closes parser temp files when reading the stream raises.
                    raise HTTPException(413, "上传请求过大。")
            return message

        async def correlated_send(message: Message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            content_length = dict(scope["headers"]).get(b"content-length", b"")
            if (
                upload
                and content_length.isdigit()
                and (len(content_length) > 18 or int(content_length) > limit)
            ):
                await JSONResponse({"detail": "上传请求过大。"}, status_code=413)(
                    scope, receive, correlated_send
                )
            else:
                await self.app(scope, bounded_receive, correlated_send)
        except Exception as exc:
            # Exception messages/SQL parameters can contain uploaded data: log only the type.
            logger.error(
                "event=request_exception request_id=%s exception_type=%s location=%s",
                request_id,
                type(exc).__name__,
                exception_location(exc),
            )
            if response_started:
                raise
            await JSONResponse({"detail": "服务处理失败，请重试或检查服务日志。"}, status_code=500)(
                scope, receive, correlated_send
            )
        finally:
            if status >= 400:
                route = getattr(scope.get("route"), "path", "unmatched")
                logger.warning(
                    "event=request_failed request_id=%s method=%s route=%s status=%s duration_ms=%.1f",
                    request_id,
                    scope["method"],
                    route,
                    status,
                    (perf_counter() - started) * 1000,
                )
