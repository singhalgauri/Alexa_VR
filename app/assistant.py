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

Extended for Feature A & B:
  - Schedule/calendar queries with relative date handling
  - Cross-user reference detection ("what's Rahul's schedule")
  - Personalized greetings
  - Shopping list, reminders, routines (per-user)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import date, timedelta
from typing import Any, Optional

MCP_URL: str = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")
VOICEGATE_LLM: str = os.environ.get("VOICEGATE_LLM", "none").lower()

# ---------------------------------------------------------------------------
# Enrolled user names registry (populated from the voice gate)
# ---------------------------------------------------------------------------
_ENROLLED_NAMES: set[str] = set()


def register_enrolled_users(names: set[str]) -> None:
    """Register known enrolled user names for cross-user detection."""
    global _ENROLLED_NAMES
    _ENROLLED_NAMES = {n.lower() for n in names}


def _get_enrolled_names() -> set[str]:
    """Get enrolled user names, refreshing from policy store if empty."""
    if not _ENROLLED_NAMES:
        try:
            from access_control.policy_store import PolicyStore
            store = PolicyStore()
            for u in store._enrolled_users:
                _ENROLLED_NAMES.add(u.lower())
        except Exception:
            pass
    return _ENROLLED_NAMES


# ---------------------------------------------------------------------------
# Relative date resolution
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_date(text: str) -> str:
    """
    Resolve relative date references to YYYY-MM-DD.
    Handles: today, tomorrow, yesterday, this week, next week, day names.
    Returns empty string if no date reference found.
    """
    t = text.lower().strip()
    today = date.today()

    if "today" in t or "this morning" in t or "this afternoon" in t or "this evening" in t:
        return today.isoformat()

    if "tomorrow" in t:
        return (today + timedelta(days=1)).isoformat()

    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()

    # "on Monday", "next Tuesday", "this Friday"
    for day_name, day_num in _DAY_NAMES.items():
        if re.search(rf"\b(on\s+|next\s+|this\s+)?{day_name}\b", t):
            current_day = today.weekday()
            days_ahead = day_num - current_day
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    # Explicit date: "October 25", "Oct 25", "10/25"
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return m.group(1)

    return ""


# ---------------------------------------------------------------------------
# Cross-user name detection
# ---------------------------------------------------------------------------

def _detect_other_user(text: str) -> Optional[str]:
    """
    Detect if the transcript references another enrolled user's data.
    Returns the user name (lowercase) or None.
    Examples: "what's Rahul's schedule", "add milk to Priya's shopping list"
    """
    t = text.lower()
    enrolled = _get_enrolled_names()

    # Pattern: <name>'s <data_type>
    m = re.search(r"\b([a-z]+)(?:'s|s')\s+(schedule|calendar|shopping|reminders?|list|events?|routines?)", t)
    if m:
        candidate = m.group(1)
        if candidate in enrolled and candidate not in {"my", "the", "today", "tomorrow", "your"}:
            return candidate

    # Pattern: "to <name>'s" (e.g. "add milk to priya's list")
    m = re.search(r"\bto\s+([a-z]+)(?:'s|s')\s+(shopping|list|reminders?)", t)
    if m:
        candidate = m.group(1)
        if candidate in enrolled:
            return candidate

    return None


# ---------------------------------------------------------------------------
# Regex intent router
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(r"^(hello|hi|hey|greetings|good morning|good afternoon|good evening)\b", re.I)
_TIME_RE = re.compile(r"\b(what('?s| is) (the )?time|time is it|current time)\b", re.I)

# Schedule / calendar
_SCHEDULE_RE = re.compile(
    r"\b(what('?s| is)( on)?( my| \w+'s)?\s*(schedule|calendar|agenda|events?)|"
    r"show( me)?( my| \w+'s)?\s*(schedule|calendar|events?)|"
    r"(do i have|any) (events?|meetings?|appointments?)|"
    r"what('?s| is) (happening|planned|coming up))\b",
    re.I,
)
_NEXT_EVENT_RE = re.compile(
    r"\b(what('?s| is)( my)?\s*next (event|meeting|appointment)|"
    r"next (event|meeting|appointment|thing on my))\b",
    re.I,
)
_ADD_EVENT_RE = re.compile(
    r"\b(add|schedule|create|put)\s+(a\s+)?(event|meeting|appointment)\s+"
    r"(called |named |titled )?(?P<title>.+?)(\s+(on|for|at)\s+(?P<when>.+))?$",
    re.I,
)

# Reminders
_REMINDER_ADD_RE = re.compile(
    r"\b(remind me (to |about )?|add (a )?reminder (to |about )?|set (a )?reminder (to |about )?)(?P<text>.+)",
    re.I,
)
_REMINDER_LIST_RE = re.compile(r"\b(list|show|what are|read)( my| \w+'s)?\s*(reminders?)\b", re.I)

# Lists
_LIST_ADD_RE = re.compile(
    r"\b(add|put|append)\s+(?P<item>.+?)\s+(to|on|into)\s+(\w+'s\s+)?(?:my\s+)?(?P<list>\w+)\s+list\b",
    re.I,
)
_LIST_READ_RE = re.compile(
    r"\b(read|show|what('?s| is) on|list)\s+(\w+'s\s+)?(?:my\s+)?(?P<list>\w+)\s+list\b", re.I
)

# Shopping list specific
_SHOPPING_ADD_RE = re.compile(
    r"\b(add|put)\s+(?P<item>.+?)\s+(to|on)\s+(\w+'s\s+)?(?:my\s+)?shopping\s+list\b",
    re.I,
)
_SHOPPING_READ_RE = re.compile(
    r"\b(read|show|what('?s| is) on)\s+(\w+'s\s+)?(?:my\s+)?shopping\s+list\b",
    re.I,
)

# Timer
_TIMER_RE = re.compile(
    r"\b(set (a )?timer (for )?|start (a )?timer (for )?)"
    r"(?P<minutes>[\d\.]+)\s*(min(ute)?s?|m\b)"
    r"(\s+(called|labeled|named)\s+(?P<label>.+?))?$",
    re.I,
)

# Device control
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

# Weather
_WEATHER_RE = re.compile(
    r"\b(weather|forecast|temperature)\s+(in|for|at)\s+(?P<city>[a-z ]+)",
    re.I,
)

# Sharing
_SHARE_RE = re.compile(
    r"\bshare\s+(my\s+)?(?P<category>\w[\w\s]*?)\s+with\s+(?P<user>\w+)\b",
    re.I,
)
_SHARING_STATUS_RE = re.compile(
    r"\b(what('?s| is)( my)?\s*sharing|my sharing settings|show sharing)\b",
    re.I,
)


def _route(transcript: str) -> tuple[str, dict[str, Any]]:
    """
    Parse *transcript* and return (tool_name, args_dict).
    Returns ("unknown", {}) if nothing matches.
    """
    t = transcript.strip()
    other_user = _detect_other_user(t)

    # Greeting
    if _GREETING_RE.search(t):
        return "greeting", {}

    # Time
    if _TIME_RE.search(t):
        return "get_time", {}

    # Sharing status
    if _SHARING_STATUS_RE.search(t):
        return "get_my_sharing", {}

    # Schedule / calendar queries
    if _NEXT_EVENT_RE.search(t):
        args: dict[str, Any] = {}
        if other_user:
            args["owner"] = other_user
        return "get_next_event", args

    if _SCHEDULE_RE.search(t):
        day = _resolve_date(t)
        args = {}
        if day:
            args["day"] = day
        if other_user:
            args["owner"] = other_user
        return "get_schedule", args

    m = _ADD_EVENT_RE.search(t)
    if m:
        title = m.group("title").strip(".!? ")
        when_raw = m.group("when") or ""
        when = _resolve_date(when_raw) if when_raw else date.today().isoformat()
        # Try to extract time
        time_match = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", when_raw, re.I)
        time_str = time_match.group(1) if time_match else ""
        return "add_event", {"title": title, "when": when, "time_str": time_str}

    # Shopping list
    m = _SHOPPING_ADD_RE.search(t)
    if m:
        item = m.group("item").strip()
        args = {"item": item}
        if other_user:
            args["owner"] = other_user
        return "add_to_shopping_list", args

    if _SHOPPING_READ_RE.search(t):
        args = {}
        if other_user:
            args["owner"] = other_user
        return "read_shopping_list", args

    # Reminders
    m = _REMINDER_ADD_RE.search(t)
    if m:
        return "add_reminder", {"text": m.group("text").strip(".!? ")}

    if _REMINDER_LIST_RE.search(t):
        args = {}
        if other_user:
            args["owner"] = other_user
        return "list_reminders", args

    # Named lists
    m = _LIST_ADD_RE.search(t)
    if m:
        list_name = m.group("list").lower()
        if list_name == "shopping":
            args = {"item": m.group("item").strip()}
            if other_user:
                args["owner"] = other_user
            return "add_to_shopping_list", args
        return "add_to_list", {
            "list_name": list_name,
            "item": m.group("item").strip(),
        }

    m = _LIST_READ_RE.search(t)
    if m:
        list_name = m.group("list").lower()
        if list_name == "shopping":
            args = {}
            if other_user:
                args["owner"] = other_user
            return "read_shopping_list", args
        args = {"list_name": list_name}
        if other_user:
            args["owner"] = other_user
        return "read_list", args

    # Timer
    m = _TIMER_RE.search(t)
    if m:
        label = (m.group("label") or "timer").strip()
        return "set_timer", {"minutes": float(m.group("minutes")), "label": label}

    # Device control
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

    # Weather
    m = _WEATHER_RE.search(t)
    if m:
        return "get_weather_stub", {"city": m.group("city").strip(".!? ")}

    # Share command
    m = _SHARE_RE.search(t)
    if m:
        category = m.group("category").strip().lower().replace(" ", "_")
        user = m.group("user").strip().lower()
        return "set_my_sharing", {"category": category, "level": "read", "audience": user}

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
        # Personalized greeting via MCP tool
        result_text, latency_ms = await _call_mcp(token, "greeting", {})
        trace.append({
            "tool": "greeting",
            "args": {},
            "result": result_text,
            "latency_ms": round(latency_ms, 1),
        })
        return {"reply": result_text, "mcp_trace": trace}

    if tool_name == "unknown":
        return {
            "reply": (
                "I'm not sure how to help with that. "
                "Try asking about your schedule, setting a reminder, or controlling a device."
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
