"""
tests/test_tools_isolation.py – Tests for all MCP tools and per-user data isolation.
Verifies that users cannot access or leak each other's reminders, lists, or device states.
"""

import pytest
from mcp_server.auth import current_user
from mcp_server import tools


def test_tool_requires_authenticated_context():
    """Tools must raise RuntimeError if called without authenticated context."""
    token = current_user.set("")
    try:
        with pytest.raises(RuntimeError, match="No authenticated user"):
            tools.add_reminder("test reminder")
    finally:
        current_user.reset(token)


def test_reminders_isolation():
    # Alice adds a reminder
    token_alice = current_user.set("alice")
    try:
        res1 = tools.add_reminder("Buy groceries")
        assert "Buy groceries" in res1
        alice_reminders = tools.list_reminders()
        assert "Buy groceries" in alice_reminders
    finally:
        current_user.reset(token_alice)

    # Bob should see no reminders from Alice
    token_bob = current_user.set("bob")
    try:
        bob_reminders = tools.list_reminders()
        assert "no reminders" in bob_reminders.lower()
        assert "Buy groceries" not in bob_reminders

        # Bob adds his own reminder
        tools.add_reminder("Prepare hackathon slides")
        bob_reminders2 = tools.list_reminders()
        assert "Prepare hackathon slides" in bob_reminders2
        assert "Buy groceries" not in bob_reminders2
    finally:
        current_user.reset(token_bob)

    # Verify Alice's list still contains only Alice's reminder
    token_alice2 = current_user.set("alice")
    try:
        alice_reminders2 = tools.list_reminders()
        assert "Buy groceries" in alice_reminders2
        assert "Prepare hackathon slides" not in alice_reminders2
    finally:
        current_user.reset(token_alice2)


def test_named_lists_isolation():
    token_alice = current_user.set("alice")
    try:
        tools.add_to_list("shopping", "Apples")
        tools.add_to_list("shopping", "Bananas")
        alice_list = tools.read_list("shopping")
        assert "Apples" in alice_list
        assert "Bananas" in alice_list
    finally:
        current_user.reset(token_alice)

    token_bob = current_user.set("bob")
    try:
        bob_list = tools.read_list("shopping")
        assert "empty" in bob_list.lower()
        assert "Apples" not in bob_list
    finally:
        current_user.reset(token_bob)


def test_device_control_and_timer():
    token_alice = current_user.set("alice")
    try:
        dev_res = tools.control_device("living room light", "on")
        assert "is now on" in dev_res.lower()

        timer_res = tools.set_timer(5.0, "pasta")
        assert "pasta" in timer_res
        assert "5" in timer_res

        # Unknown device
        unknown_dev = tools.control_device("flying car", "on")
        assert "Unknown device" in unknown_dev
    finally:
        current_user.reset(token_alice)


def test_get_time_and_weather_stub():
    token_alice = current_user.set("alice")
    try:
        time_res = tools.get_time()
        assert "UTC" in time_res

        weather_res = tools.get_weather_stub("Seattle")
        assert "[SIMULATED]" in weather_res
        assert "Seattle" in weather_res
    finally:
        current_user.reset(token_alice)
