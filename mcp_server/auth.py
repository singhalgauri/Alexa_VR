"""
mcp_server/auth.py – Bearer-token ASGI middleware for the MCP server.

Reads "Authorization: Bearer <token>" from every request.
Uses app/tokens.py verify_token() for validation.
Exposes the verified user name via a contextvars.ContextVar so tools can
read it without accepting it as a function argument (preventing model/client
injection).
"""

from __future__ import annotations

import sys
import os
from contextvars import ContextVar
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Add parent to path so we can import app.tokens
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.tokens import verify_token  # noqa: E402

# ContextVar holding the verified user name for the current request.
# Tools MUST read identity from here, never from tool arguments.
current_user: ContextVar[str] = ContextVar("current_user", default="")

# Paths that don't require auth (MCP initialization handshake probing is done
# before auth in some clients; we require auth on all tool-bearing paths).
_EXEMPT_METHODS = {"OPTIONS"}


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a valid Bearer token on every non-OPTIONS request."""

    async def dispatch(self, request: Request, call_next: Callable):
        if request.method in _EXEMPT_METHODS:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or malformed Authorization header"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth[len("Bearer "):]
        try:
            user = verify_token(token)
        except ValueError as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Set the context var so tools can read it
        token_ctx = current_user.set(user)
        try:
            response = await call_next(request)
        finally:
            current_user.reset(token_ctx)

        return response
