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
    """
    Get enrolled user names across all sources:
      1. Explicitly registered names
      2. Voiceprints on disk (voiceprints/*.npy and voiceprints/*.profile.json)
      3. User data store on disk (data/*.json)
      4. Policy store enrolled users (access_control_policy.json)
    """
    global _ENROLLED_NAMES
    names = set(_ENROLLED_NAMES)

    # 1. Voiceprints directory
    try:
        from pathlib import Path
        vp_dir = Path(os.environ.get("VOICEGATE_DATA", os.environ.get("VOICEPRINTS_DIR", "voiceprints")))
        if vp_dir.exists():
            for p in vp_dir.glob("*.npy"):
                names.add(p.stem.lower())
            for p in vp_dir.glob("*.profile.json"):
                names.add(p.stem.split(".")[0].lower())
    except Exception:
        pass

    # 2. User data directory
    try:
        from pathlib import Path
        data_dir = Path(os.environ.get("VOICEGATE_USER_DATA_DIR", "data"))
        if data_dir.exists():
            for p in data_dir.glob("*.json"):
                stem = p.stem.lower()
                if not stem.startswith("_"):
                    names.add(stem)
    except Exception:
        pass

    # 3. Policy store
    try:
        from access_control.policy_store import PolicyStore
        store = PolicyStore()
        for u in store._enrolled_users:
            names.add(u.lower())
    except Exception:
        pass

    _ENROLLED_NAMES.update(names)
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
    Examples:
      - "what's Rahul's schedule" -> "rahul"
      - "what is the schedule of Rahul" -> "rahul"
      - "schedule for Rahul" -> "rahul"
      - "what is Rahul schedule" -> "rahul"
      - "what does Rahul have scheduled" -> "rahul"
      - "add milk to Priya's shopping list" -> "priya"
    """
    t = text.lower()
    enrolled = _get_enrolled_names()
    stopwords = {"my", "the", "today", "tomorrow", "yesterday", "your", "a", "an", "this", "next", "our", "their", "his", "her"}

    # Pattern 1: <name>'s <data_type> or <name>s' <data_type>
    m = re.search(r"\b([a-z0-9_-]+)(?:'s|s')\s+(schedule|calendar|shopping|reminders?|lists?|events?|routines?|agenda)", t)
    if m:
        cand = m.group(1).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    # Pattern 2: "to <name>'s" (e.g. "add milk to priya's list")
    m = re.search(r"\bto\s+([a-z0-9_-]+)(?:'s|s')\s+(shopping|list|reminders?|schedules?|calendar)", t)
    if m:
        cand = m.group(1).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    # Pattern 3: <data_type> of/for <name> (e.g. "schedule of rahul", "schedule for rahul", "calendar of priya")
    m = re.search(r"\b(schedules?|calendar|agenda|events?|reminders?|shopping\s+list|lists?)\s+(?:of|for)\s+([a-z0-9_-]+)\b", t)
    if m:
        cand = m.group(2).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    # Pattern 4: <name> <data_type> (e.g. "rahul schedule", "what is rahul schedule", "check priya calendar")
    m = re.search(r"\b([a-z0-9_-]+)\s+(schedules?|calendar|agenda|events?|reminders?|shopping\s+list|lists?)\b", t)
    if m:
        cand = m.group(1).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    # Pattern 5: "scheduled for <name>" or "planned for <name>"
    m = re.search(r"\b(?:scheduled|planned|happening)\s+for\s+([a-z0-9_-]+)\b", t)
    if m:
        cand = m.group(1).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    # Pattern 6: "does <name> have" (e.g. "what does rahul have scheduled")
    m = re.search(r"\b(?:does|do)\s+([a-z0-9_-]+)\s+have\b", t)
    if m:
        cand = m.group(1).lower()
        if cand in enrolled and cand not in stopwords:
            return cand

    return None


# ---------------------------------------------------------------------------
# Regex intent router
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(r"^(hello|hi|hey|greetings|good morning|good afternoon|good evening)\b", re.I)
_TIME_RE = re.compile(r"\b(what('?s| is) (the )?time|time is it|current time)\b", re.I)

# Schedule / calendar
_SCHEDULE_RE = re.compile(
    r"^\s*(my\s+|the\s+|\w+\s+)?(schedules?|calendar|agenda)(\s+(of|for)\s+\w+)?(\s+(for\s+)?(today|tomorrow|yesterday|this week|next week|[a-z]+day))?\s*$"
    r"|"
    r"^\s*\w+('s|s')?\s+(schedules?|calendar|agenda|events?)(\s+(for\s+)?(today|tomorrow|yesterday|[a-z]+day))?\s*$"
    r"|"
    r"\b(what('?s| is| are)\s+(on\s+)?(\w+'s\s+|my\s+|the\s+|\w+\s+)?(schedules?|calendar|agenda|events?|meetings?|appointments?|plans?))\b"
    r"|"
    r"\b(show|tell|check|read|get|view|give)( me)?\s+(\w+'s\s+|my\s+|the\s+|\w+\s+)?(schedules?|calendar|agenda|events?|meetings?|appointments?)\b"
    r"|"
    r"\b(what\s+(?:do\s+i|does\s+\w+|do\s+\w+)\s+have(\s+(?:scheduled|planned|going on|on\s+schedule|today|tomorrow))?)\b"
    r"|"
    r"\b(what'?s happening|what is happening|what'?s planned|what is planned|what'?s coming up|what is coming up|what'?s scheduled|what is scheduled)\b"
    r"|"
    r"\b(do i have|does\s+\w+\s+have|any|are there any)\s+(events?|meetings?|appointments?|plans?)\b",
    re.I,
)

_NEXT_EVENT_RE = re.compile(
    r"\b(what('?s| is)(\s+(my|\w+'s|\w+))?\s*next\s+(event|meeting|appointment|thing\s+on\s+my\s+schedule)|"
    r"next\s+(event|meeting|appointment|thing\s+on\s+my)|"
    r"(my|\w+'s|\w+)\s+next\s+(event|meeting|appointment))\b",
    re.I,
)

_ADD_EVENT_RE = re.compile(
    r"\b(add|put)\s+(.+?)\s+(to|on|in)\s+(\w+'s\s+|my\s+)?(schedules?|calendar)\b"
    r"|"
    r"\b(add|put)\s+(to|on|in)\s+(\w+'s\s+|my\s+)?(schedules?|calendar)\b"
    r"|"
    r"\b(schedule|book|set\s+up)\s+(an?\s+)?(event|meeting|appointment|call|session|interview|lunch|dinner|sync)\b"
    r"|"
    r"\b(schedule|book|set\s+up)\s+([a-z0-9_]+(?:\s+[a-z0-9_]+)*)\s+(on|at|for|tomorrow|today|this|next|[a-z]+day)\b"
    r"|"
    r"\b(add|create)\s+(an?\s+)?(event|meeting|appointment|call|session|interview)\b"
    r"|"
    r"\b(set\s+(a\s+)?(schedules?|calendar\s+entry)\s+for\b)",
    re.I,
)


def _extract_time(text: str) -> tuple[str, str]:
    """Extract time string and the matched substring from *text*."""
    t = text.lower()
    patterns = [
        r"\b(?:at|for)\s+(\d{1,2}:\d{2}\s*(?:am|pm)?)\b",
        r"\b(?:at|for)\s+(\d{1,2}\s*(?:am|pm))\b",
        r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b",
        r"\b(\d{1,2}\s*(?:am|pm))\b",
        r"\b(?:at|for)\s+(\d{1,2})\b",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            raw = m.group(1).strip()
            tm = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
            if tm:
                hr = int(tm.group(1))
                mn = int(tm.group(2) or 0)
                meridiem = tm.group(3)
                if meridiem == "pm" and hr < 12:
                    hr += 12
                elif meridiem == "am" and hr == 12:
                    hr = 0
                return f"{hr:02d}:{mn:02d}", m.group(0)
            return raw, m.group(0)
    return "", ""


def _parse_add_event(text: str, other_user: str = "") -> tuple[str, str, str]:
    """
    Parse an add-event/schedule request into (title, when, time_str).
    when: YYYY-MM-DD (defaults to today if no date in transcript)
    time_str: HH:MM or speakable time string (or empty)
    """
    t = text.strip()
    resolved_date = _resolve_date(t) or date.today().isoformat()
    time_str, time_match_text = _extract_time(t)

    work = t
    if time_match_text:
        work = re.sub(re.escape(time_match_text), "", work, flags=re.I)

    date_patterns = [
        r"\btomorrow\b", r"\btoday\b", r"\byesterday\b",
        r"\bthis morning\b", r"\bthis afternoon\b", r"\bthis evening\b",
        r"\b(on\s+|next\s+|this\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    for dp in date_patterns:
        work = re.sub(dp, "", work, flags=re.I)

    # Remove schedule destination e.g. 'to my schedule', 'on my schedule'
    work = re.sub(r"\b(to|on|in)\s+(\w+'s\s+|my\s+)?(schedules?|calendar)\b", "", work, flags=re.I)

    strip_triggers = [
        r"^\s*(please\s+)?(add|schedule|create|put|set|book)\b",
        r"^\s*(to|on|in)\s+(\w+'s\s+|my\s+)?(schedules?|calendar)\b",
        r"^\s*(an?|the)\s+(schedules?|calendar\s+entry)\s*(for)?\b",
        r"^\s*(schedules?|calendar\s+entry)\s*(for)?\b",
        r"^\s*(an?|the)\s+(event|meeting|appointment|call|session|interview)\s+(called|named|titled)\b",
        r"^\s*(event|meeting|appointment|call|session|interview)\s+(called|named|titled)\b",
        r"^\s*(called|named|titled)\b",
        r"^\s*(for|on|at)\s+",
    ]
    changed = True
    while changed:
        changed = False
        for st in strip_triggers:
            new_work = re.sub(st, "", work, flags=re.I).strip()
            if new_work != work:
                work = new_work
                changed = True

    m = re.match(r"^\s*(an?|the)\s+(event|meeting|appointment|session|interview)\s+(.+)$", work, flags=re.I)
    if m and not m.group(3).lower().startswith("with"):
        work = m.group(3)

    work = re.sub(r"^\s*(an?|the)\s+", "", work, flags=re.I)
    work = re.sub(r"\s+(for|on|at|to)\s*$", "", work, flags=re.I).strip(".!? ,")

    if not work or work.lower() in {"a", "an", "the"}:
        for word in ["meeting", "appointment", "call", "session", "interview", "lunch", "dinner", "event"]:
            if word in t.lower():
                work = word.capitalize()
                break
        else:
            work = "Event"

    return work.title(), resolved_date, time_str

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

    if _ADD_EVENT_RE.search(t):
        title, when, time_str = _parse_add_event(t, other_user or "")
        args = {"title": title, "when": when, "time_str": time_str}
        if other_user:
            args["owner"] = other_user
        return "add_event", args

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
