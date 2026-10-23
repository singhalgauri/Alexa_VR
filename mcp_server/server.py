"""
mcp_server/server.py – Self-hosted MCP server over Streamable HTTP.

Uses the official MCP Python SDK (package "mcp") with MCPServer + Starlette
to serve at /mcp on port 8765 (env MCP_PORT).

Auth: BearerTokenMiddleware wraps the entire ASGI app, requiring a valid
short-lived token from the voice gate.

Protocol version: The SDK negotiates the protocol version during the
initialize handshake. As of mcp==2.3.0 the server advertises and accepts
protocol versions up to 2025-11-25.

Run:
    python mcp_server/server.py
"""

from __future__ import annotations

import os
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mcp.server import MCPServer  # type: ignore  # noqa: E402
from mcp_server.auth import BearerTokenMiddleware  # noqa: E402
from mcp_server import tools  # noqa: E402

MCP_PORT: int = int(os.environ.get("MCP_PORT", "8765"))
MCP_HOST: str = os.environ.get("MCP_HOST", "127.0.0.1")

# ---------------------------------------------------------------------------
# Build the MCPServer and register tools
# ---------------------------------------------------------------------------

mcp = MCPServer("VoiceGate-Alexa-Plus")


@mcp.tool()
def get_time() -> str:
    """Tell the current time."""
    return tools.get_time()


@mcp.tool()
def greeting() -> str:
    """Return a personalized greeting for the verified user."""
    return tools.greeting()


# --- Schedule / Calendar tools ---

@mcp.tool()
def get_schedule(day: str = "", owner: str = "") -> str:
    """Get schedule events for a day (YYYY-MM-DD, default today). owner: optional other user's name."""
    return tools.get_schedule(day, owner)


@mcp.tool()
def get_next_event(owner: str = "") -> str:
    """Get the next upcoming calendar event. owner: optional other user's name."""
    return tools.get_next_event(owner)


@mcp.tool()
def add_event(title: str, when: str, time_str: str = "") -> str:
    """Add a calendar event. title: event name. when: YYYY-MM-DD. time_str: HH:MM (optional)."""
    return tools.add_event(title, when, time_str)


# --- Reminder tools ---

@mcp.tool()
def add_reminder(text: str) -> str:
    """Add a reminder. text: what to remember."""
    return tools.add_reminder(text)


@mcp.tool()
def list_reminders(owner: str = "") -> str:
    """List your saved reminders. owner: optional other user's name."""
    return tools.list_reminders(owner)


# --- Shopping list tools ---

@mcp.tool()
def add_to_shopping_list(item: str) -> str:
    """Add an item to the shopping list."""
    return tools.add_to_shopping_list(item)


@mcp.tool()
def read_shopping_list(owner: str = "") -> str:
    """Read the shopping list. owner: optional other user's name."""
    return tools.read_shopping_list(owner)


# --- Named list tools ---

@mcp.tool()
def add_to_list(list_name: str, item: str) -> str:
    """Add an item to a list. list_name: e.g. shopping. item: what to add."""
    return tools.add_to_list(list_name, item)


@mcp.tool()
def read_list(list_name: str, owner: str = "") -> str:
    """Read items from a named list. owner: optional other user's name."""
    return tools.read_list(list_name, owner)


# --- Timer and device tools ---

@mcp.tool()
def set_timer(minutes: float, label: str = "timer") -> str:
    """Set a timer. minutes: duration. label: optional name."""
    return tools.set_timer(minutes, label)


@mcp.tool()
def control_device(device: str, state: str) -> str:
    """Control a simulated smart home device. device: name. state: on/off/temp."""
    return tools.control_device(device, state)


@mcp.tool()
def get_weather_stub(city: str) -> str:
    """Get simulated weather for a city (clearly labeled simulated)."""
    return tools.get_weather_stub(city)


# --- Sharing management tools ---

@mcp.tool()
def get_my_sharing() -> str:
    """View your current data sharing settings."""
    return tools.get_my_sharing()


@mcp.tool()
def set_my_sharing(category: str, level: str, audience: str = "all_enrolled") -> str:
    """Update your sharing settings for a category. level: off/read/read_write. audience: all_enrolled or comma-separated usernames."""
    return tools.set_my_sharing(category, level, audience)


# ---------------------------------------------------------------------------
# Build Starlette ASGI app with auth middleware
# ---------------------------------------------------------------------------

def build_app() -> Starlette:
    """Build and return the ASGI application."""
    from mcp.server.transport_security import TransportSecuritySettings

    # Allow local testing and custom hosts without DNS rebinding rejection
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = mcp.streamable_http_app(transport_security=security)
    app.add_middleware(BearerTokenMiddleware)
    return app


app = build_app()


if __name__ == "__main__":
    uvicorn.run(
        "mcp_server.server:app",
        host=MCP_HOST,
        port=MCP_PORT,
        log_level="info",
    )
