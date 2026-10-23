"""
tests/test_assistant_schedule.py – Tests for assistant schedule routing and creation.
"""

from datetime import date, timedelta
import pytest
from app.assistant import _route, _parse_add_event
from mcp_server import tools, user_data
from mcp_server.tools import current_user


def test_schedule_ask_routing():
    """Verify natural ways to ask for schedule are routed to get_schedule."""
    queries = [
        "schedule",
        "schedules",
        "my schedule",
        "what is my schedule",
        "what's on my schedule",
        "what is on my schedule today",
        "schedule today",
        "schedule for today",
        "schedule tomorrow",
        "today's schedule",
        "tell me my schedule",
        "check my schedule",
        "read my schedule",
        "show my schedule",
        "what are my schedules",
        "what is scheduled for today",
        "what do i have scheduled",
        "what do i have today",
        "calendar",
        "my calendar",
        "check calendar",
        "show rahul's schedule",
    ]
    for q in queries:
        tool, args = _route(q)
        assert tool == "get_schedule", f"Query '{q}' routed to '{tool}' instead of 'get_schedule'"


def test_cross_user_schedule_routing():
    """Verify natural ways to ask for another user's schedule identify the target user."""
    queries = [
        ("what is rahul's schedule", "rahul"),
        ("what's on rahul's schedule", "rahul"),
        ("what is the schedule of rahul", "rahul"),
        ("what is the schedule for rahul", "rahul"),
        ("schedule of rahul", "rahul"),
        ("schedule for rahul", "rahul"),
        ("what is rahul schedule", "rahul"),
        ("check rahul schedule", "rahul"),
        ("what does rahul have scheduled", "rahul"),
        ("what is scheduled for rahul", "rahul"),
        ("rahul schedule", "rahul"),
        ("tell me the schedule of rahul", "rahul"),
        ("show schedule of rahul", "rahul"),
        ("show rahul schedule", "rahul"),
        ("show me rahul's schedule", "rahul"),
        ("rahul's schedule", "rahul"),
        ("what is priya's schedule", "priya"),
        ("what is on priya's calendar", "priya"),
        ("what is alice's schedule", "alice"),
        ("what is bob's schedule", "bob"),
    ]
    for q, expected_owner in queries:
        tool, args = _route(q)
        assert tool == "get_schedule", f"Query '{q}' routed to '{tool}' instead of 'get_schedule'"
        assert args.get("owner") == expected_owner, f"Query '{q}' got owner {args.get('owner')} instead of {expected_owner}"


def test_schedule_add_routing():
    """Verify natural ways to set/add schedule events are routed to add_event."""
    queries = [
        "schedule a meeting called team sync tomorrow at 3pm",
        "schedule an event tomorrow at 3pm",
        "schedule a meeting tomorrow at 10am",
        "schedule doctor appointment tomorrow at 10am",
        "schedule lunch tomorrow at 1pm",
        "schedule team sync tomorrow at 3pm",
        "add an event team standup tomorrow at 9am",
        "add meeting standup tomorrow at 9am",
        "add to my schedule team meeting tomorrow at 2pm",
        "add team meeting to my schedule tomorrow at 2pm",
        "put design review on my schedule on Friday at 4pm",
        "set a schedule for client demo today at 5pm",
        "create an appointment with dentist tomorrow at 11am",
        "schedule a call with Alice at 3pm",
        "schedule a meeting",
        "schedule an event",
        "book a meeting with Bob at 4pm",
        "add event standup tomorrow at 9am",
        "set schedule for design review tomorrow at 2pm",
    ]
    for q in queries:
        tool, args = _route(q)
        assert tool == "add_event", f"Query '{q}' routed to '{tool}' instead of 'add_event'"
        assert "title" in args and args["title"], f"Missing title for '{q}': {args}"
        assert "when" in args and args["when"], f"Missing when for '{q}': {args}"


def test_parse_add_event_details():
    """Verify date, time, and title parsing."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    title, when, time_str = _parse_add_event("schedule a meeting called team sync tomorrow at 3pm")
    assert title == "Team Sync"
    assert when == tomorrow
    assert time_str == "15:00"

    title2, when2, time_str2 = _parse_add_event("schedule doctor appointment tomorrow at 10am")
    assert title2 == "Doctor Appointment"
    assert when2 == tomorrow
    assert time_str2 == "10:00"

    title3, when3, time_str3 = _parse_add_event("add team standup to my schedule today at 9:30am")
    assert title3 == "Team Standup"
    assert when3 == today
    assert time_str3 == "09:30"


def test_add_and_query_schedule_integration(tmp_path, monkeypatch):
    """End-to-end test: add event via tools and verify it appears in get_schedule."""
    monkeypatch.setattr(user_data, "DATA_DIR", tmp_path)
    today = date.today().isoformat()

    t_alice = current_user.set("alice")
    try:
        # Alice adds a meeting
        reply = tools.add_event(title="Sprint Planning", when=today, time_str="11:00")
        assert "Sprint Planning" in reply
        assert "Event added" in reply

        # Alice checks schedule
        sched = tools.get_schedule(day=today)
        assert "Sprint Planning" in sched
        assert "11:00" in sched
    finally:
        current_user.reset(t_alice)
