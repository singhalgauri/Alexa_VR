"""
tests/test_mcp_auth_and_protocol.py – Verification of MCP server auth and protocol negotiation.
Tests:
- Rejection of missing/expired/tampered tokens with 401.
- Streamable HTTP protocol negotiation >= 2025-11-25.
"""

import time
import pytest
import uvicorn
import httpx2
from starlette.testclient import TestClient
from mcp.server.transport_security import TransportSecuritySettings
from mcp_server.server import mcp, build_app
from mcp_server.auth import BearerTokenMiddleware
from app.tokens import mint_token, TOKEN_TTL
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession


def test_mcp_rejects_missing_token_with_401():
    app = build_app()
    client = TestClient(app)
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    assert "Authorization" in resp.json().get("error", "")


def test_mcp_rejects_tampered_token_with_401():
    app = build_app()
    client = TestClient(app)
    token = mint_token("alice")
    tampered = token[:-4] + "ABCD"
    resp = client.post("/mcp", headers={"Authorization": f"Bearer {tampered}"}, json={})
    assert resp.status_code == 401


def test_mcp_rejects_expired_token_with_401(monkeypatch):
    app = build_app()
    client = TestClient(app)
    token = mint_token("alice")

    future_time = time.time() + TOKEN_TTL + 10
    monkeypatch.setattr(time, "time", lambda: future_time)

    resp = client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={})
    assert resp.status_code == 401
    assert "expired" in resp.json().get("error", "").lower()


@pytest.mark.asyncio
async def test_mcp_negotiates_protocol_2025_11_25_or_later():
    """Start an in-process server and verify protocol version negotiation >= 2025-11-25."""
    import threading
    import socket

    # Find free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to bind
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    try:
        token = mint_token("test_user")
        url = f"http://127.0.0.1:{port}/mcp"
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    init_res = await session.initialize()
                    assert init_res.protocol_version >= "2025-11-25", (
                        f"Negotiated protocol version {init_res.protocol_version} is older than required 2025-11-25"
                    )
                    tools_resp = await session.list_tools()
                    tool_names = [t.name for t in tools_resp.tools]
                    assert "get_time" in tool_names
                    assert "add_reminder" in tool_names
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)
