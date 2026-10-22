"""
app/assistant.py – MCP client "brain" for Voice Gate.

Takes a transcript string + a short-lived bearer token, connects to the
self-hosted MCP server, routes the intent to a tool, and returns:
  {
    "reply": str,
    "mcp_trace": [{"tool": str, "args": dict, "result": str, "latency_ms": float}]
  }

Intent routing:
  Default: deterministic regex router (no API key required).
  Optional: pluggable LLM planner via VOICEGATE_LLM env var (openai|anthropic|none).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

MCP_URL: str = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")
VOICEGATE_LLM: str = os.environ.get("VOICEGATE_LLM", "none").lower()

# ---------------------------------------------------------------------------
# Regex intent router
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(r"^(hello|hi|hey|greetings|good morning|good afternoon|good evening)\b", re.I)
_TIME_RE = re.compile(r"\b(what('?s| is) (the )?time|time is it|current time)\b", re.I)
_REMINDER_ADD_RE = re.compile(
    r"\b(remind me (to |about )?|add (a )?reminder (to |about )?|set (a )?reminder (to |about )?)(?P<text>.+)",
    re.I,
)
_REMINDER_LIST_RE = re.compile(r"\b(list|show|what are|read) (my )?(reminders?)\b", re.I)
_LIST_ADD_RE = re.compile(
    r"\b(add|put|append)\s+(?P<item>.+?)\s+(to|on|into)\s+(my\s+)?(?P<list>\w+)\s+list\b",
    re.I,
)
_LIST_READ_RE = re.compile(
    r"\b(read|show|what('?s| is) on|list)\s+(my\s+)?(?P<list>\w+)\s+list\b", re.I
)
_TIMER_RE = re.compile(
    r"\b(set (a )?timer (for )?|start (a )?timer (for )?)"
    r"(?P<minutes>[\d\.]+)\s*(min(ute)?s?|m\b)"
    r"(\s+(called|labeled|named)\s+(?P<label>.+?))?$",
    re.I,
)
_DEVICE_ON_RE = re.compile(
    r"\b(turn on|switch on|enable)\s+(the\s+)?(?P<device>[a-z ]+?(light|thermostat))\b",
    re.I,
)
_DEVICE_OFF_RE = re.compile(
    r"\b(turn off|switch off|disable)\s+(the\s+)?(?P<device>[a-z ]+?(light|thermostat))\b",
    re.I,
)
_DEVICE_STATE_RE = re.compile(
    r"\b(set|change)\s+(the\s+)?(?P<device>[a-z ]+?(light|thermostat))\s+to\s+(?P<state>.+)",
    re.I,
)
_WEATHER_RE = re.compile(
    r"\b(weather|forecast|temperature)\s+(in|for|at)\s+(?P<city>[a-z ]+)",
    re.I,
)


def _route(transcript: str) -> tuple[str, dict[str, Any]]:
    """
    Parse *transcript* and return (tool_name, args_dict).
    Returns ("unknown", {}) if nothing matches.
    """
    t = transcript.strip()

    if _GREETING_RE.search(t):
        return "greeting", {}

    if _TIME_RE.search(t):
        return "get_time", {}

    m = _REMINDER_ADD_RE.search(t)
    if m:
        return "add_reminder", {"text": m.group("text").strip(".!? ")}

    if _REMINDER_LIST_RE.search(t):
        return "list_reminders", {}

    m = _LIST_ADD_RE.search(t)
    if m:
        return "add_to_list", {
            "list_name": m.group("list").lower(),
            "item": m.group("item").strip(),
        }

    m = _LIST_READ_RE.search(t)
    if m:
        return "read_list", {"list_name": m.group("list").lower()}

    m = _TIMER_RE.search(t)
    if m:
        label = (m.group("label") or "timer").strip()
        return "set_timer", {"minutes": float(m.group("minutes")), "label": label}

    m = _DEVICE_ON_RE.search(t)
    if m:
        return "control_device", {
            "device": m.group("device").strip(),
            "state": "on",
        }

    m = _DEVICE_OFF_RE.search(t)
    if m:
        return "control_device", {
            "device": m.group("device").strip(),
            "state": "off",
        }

    m = _DEVICE_STATE_RE.search(t)
    if m:
        return "control_device", {
            "device": m.group("device").strip(),
            "state": m.group("state").strip(".!? "),
        }

    m = _WEATHER_RE.search(t)
    if m:
        return "get_weather_stub", {"city": m.group("city").strip(".!? ")}

    return "unknown", {}


# ---------------------------------------------------------------------------
# LLM planner (optional, behind env var)
# ---------------------------------------------------------------------------

def _llm_route(transcript: str, tool_schemas: list[dict]) -> tuple[str, dict]:
    """
    Pluggable LLM planner.  Only loaded when VOICEGATE_LLM != 'none'.
    Returns (tool_name, args_dict).
    """
    if VOICEGATE_LLM == "openai":
        return _openai_route(transcript, tool_schemas)
    if VOICEGATE_LLM == "anthropic":
        return _anthropic_route(transcript, tool_schemas)
    return "unknown", {}


def _openai_route(transcript: str, schemas: list[dict]) -> tuple[str, dict]:
    import openai  # type: ignore
    client = openai.OpenAI()
    tools_spec = [
        {"type": "function", "function": {
            "name": s["name"],
            "description": s.get("description", ""),
            "parameters": s.get("inputSchema", {}),
        }} for s in schemas
    ]
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": transcript}],
        tools=tools_spec,
        tool_choice="auto",
    )
    choice = resp.choices[0]
    if choice.finish_reason == "tool_calls":
        call = choice.message.tool_calls[0]
        import json
        return call.function.name, json.loads(call.function.arguments)
    return "unknown", {}


def _anthropic_route(transcript: str, schemas: list[dict]) -> tuple[str, dict]:
    import anthropic  # type: ignore
    client = anthropic.Anthropic()
    tools_spec = [
        {
            "name": s["name"],
            "description": s.get("description", ""),
            "input_schema": s.get("inputSchema", {"type": "object", "properties": {}}),
        } for s in schemas
    ]
    resp = client.messages.create(
        model="claude-haiku-20240307",
        max_tokens=256,
        messages=[{"role": "user", "content": transcript}],
        tools=tools_spec,
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.name, block.input
    return "unknown", {}


# ---------------------------------------------------------------------------
# MCP client  (async)
# ---------------------------------------------------------------------------

async def _call_mcp(token: str, tool_name: str, args: dict) -> tuple[str, float]:
    """
    Call *tool_name* on the MCP server with *args*.
    Returns (result_text, latency_ms).
    """
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.monotonic()
    try:
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, args)
                    latency_ms = (time.monotonic() - t0) * 1000
                    # Extract text from result
                    text = ""
                    for block in result.content:
                        if hasattr(block, "text"):
                            text += block.text
                    return text, latency_ms
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        return f"Error calling {tool_name}: {exc}", latency_ms


async def _list_mcp_tools(token: str) -> list[dict]:
    """List available tools from the MCP server."""
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    return [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "inputSchema": t.inputSchema.model_dump() if hasattr(t.inputSchema, 'model_dump') else {},
                        }
                        for t in tools_resp.tools
                    ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ask(transcript: str, token: str) -> dict:
    """
    Asynchronous entry point called from FastAPI.
    Returns {"reply": str, "mcp_trace": list}.
    """
    trace = []

    # Route intent
    if VOICEGATE_LLM != "none":
        schemas = await _list_mcp_tools(token)
        tool_name, args = _llm_route(transcript, schemas)
    else:
        tool_name, args = _route(transcript)

    if tool_name == "greeting":
        return {
            "reply": "Hello! Access granted. How can I assist you with your smart home or reminders?",
            "mcp_trace": [],
        }

    if tool_name == "unknown":
        return {
            "reply": (
                "I'm not sure how to help with that. "
                "Try asking for the time, setting a reminder, or controlling a device."
            ),
            "mcp_trace": [],
        }

    # Call MCP server
    result_text, latency_ms = await _call_mcp(token, tool_name, args)

    trace.append({
        "tool": tool_name,
        "args": args,
        "result": result_text,
        "latency_ms": round(latency_ms, 1),
    })

    return {"reply": result_text, "mcp_trace": trace}


def ask_sync(transcript: str, token: str) -> dict:
    """Synchronous entry point for external scripts."""
    return asyncio.run(ask(transcript, token))
