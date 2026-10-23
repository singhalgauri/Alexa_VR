"""
tests/test_sharing.py – Comprehensive unit tests for Feature A (Per-User Data)
and Feature B (Cross-User Sharing Policy Engine).

Covers:
- Per-user calendar/schedule tools and isolation
- Single authorize() choke point behavior
- Default-deny model (sharing OFF by default)
- Audience filtering ('all_enrolled' vs specific user list)
- Read vs read_write permission checks
- Rejection of Tier 4 unshareable categories
- Audit logging of all cross-user access attempts (allowed and denied)
- FastAPI sharing endpoints (/api/sharing, /api/sharing/audit)
"""

from datetime import date
import pytest
from fastapi.testclient import TestClient

from app.server import app
from mcp_server import tools
from mcp_server.auth import current_user
from mcp_server.sharing import (
    authorize,
    get_sharing_policy,
    recent_sharing_audit,
    revoke_sharing,
    set_sharing,
)
from mcp_server import user_data


@pytest.fixture(autouse=True)
def clean_test_sharing_state(tmp_path, monkeypatch):
    """Isolate user data and sharing policies to a clean temporary directory for each test."""
    data_dir = tmp_path / "test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOICEGATE_USER_DATA_DIR", str(data_dir))
    monkeypatch.setattr("mcp_server.user_data.DATA_DIR", data_dir)
    monkeypatch.setattr("mcp_server.sharing.DATA_DIR", data_dir)
    # Clear in-memory audit ring
    monkeypatch.setattr("mcp_server.sharing._audit_ring", [])


# ===========================================================================
# 1. Per-User Schedule Isolation (Feature A)
# ===========================================================================

def test_personal_schedule_isolation():
    """Alice and Bob maintain separate, isolated schedules."""
    today = date.today().isoformat()

    # Alice adds a meeting
    t_alice = current_user.set("alice")
    try:
        res = tools.add_event(
            title="Design Review",
            when=today,
            time_str="10:00",
        )
        assert "Design Review" in res

        alice_sched = tools.get_schedule(day=today)
        assert "Design Review" in alice_sched
    finally:
        current_user.reset(t_alice)

    # Bob's schedule does not show Alice's meeting
    t_bob = current_user.set("bob")
    try:
        bob_sched = tools.get_schedule(day=today)
        assert "Design Review" not in bob_sched
        assert "no events" in bob_sched.lower()
    finally:
        current_user.reset(t_bob)


def test_greeting_personalized():
    """greeting returns user-specific greeting."""
    t_alice = current_user.set("alice")
    try:
        greet = tools.greeting()
        assert "Alice" in greet
    finally:
        current_user.reset(t_alice)


# ===========================================================================
# 2. Sharing Policy Engine & authorize() Choke Point (Feature B)
# ===========================================================================

def test_authorize_self_access_always_allowed():
    """Self-access is always allowed for any category."""
    allowed, reason = authorize(actor="alice", owner="alice", category="calendar_schedule")
    assert allowed is True
    assert "Self-access" in reason

    allowed, reason = authorize(actor="alice", owner="alice", category="payments_banking", write=True)
    assert allowed is True


def test_default_deny_when_sharing_off():
    """By default, sharing is OFF for all categories; cross-user access is denied."""
    today = date.today().isoformat()
    allowed, reason = authorize(actor="bob", owner="alice", category="calendar_schedule")
    assert allowed is False
    assert "not shared" in reason.lower()

    # Calling tool cross-user when sharing is off raises RuntimeError refusal
    t_bob = current_user.set("bob")
    try:
        with pytest.raises(RuntimeError, match="hasn't shared"):
            tools.get_schedule(day=today, owner="alice")
    finally:
        current_user.reset(t_bob)


def test_grant_read_sharing_all_enrolled():
    """Alice shares calendar with all_enrolled at read level."""
    today = date.today().isoformat()
    ok, msg = set_sharing(owner="alice", category="calendar_schedule", level="read", audience="all_enrolled")
    assert ok is True

    # Bob can now read Alice's schedule
    allowed, reason = authorize(actor="bob", owner="alice", category="calendar_schedule", write=False)
    assert allowed is True

    # Alice creates an event
    t_alice = current_user.set("alice")
    try:
        tools.add_event("All-Hands", today, "14:00")
    finally:
        current_user.reset(t_alice)

    # Bob calling tools.get_schedule for Alice succeeds
    t_bob = current_user.set("bob")
    try:
        res = tools.get_schedule(day=today, owner="alice")
        assert "All-Hands" in res
    finally:
        current_user.reset(t_bob)


def test_specific_audience_sharing():
    """Alice shares with Bob only; Charlie is denied."""
    ok, _ = set_sharing(owner="alice", category="calendar_schedule", level="read", audience=["bob"])
    assert ok is True

    # Bob allowed
    allowed_bob, _ = authorize(actor="bob", owner="alice", category="calendar_schedule")
    assert allowed_bob is True

    # Charlie denied
    allowed_charlie, reason = authorize(actor="charlie", owner="alice", category="calendar_schedule")
    assert allowed_charlie is False
    assert "charlie" in reason.lower() or "not shared" in reason.lower()


def test_read_only_blocks_write_attempts():
    """When shared at read-only level, write attempts are rejected."""
    set_sharing(owner="alice", category="shopping", level="read", audience="all_enrolled")

    # Read allowed
    allowed_read, _ = authorize(actor="bob", owner="alice", category="shopping", write=False)
    assert allowed_read is True

    # Write denied
    allowed_write, reason = authorize(actor="bob", owner="alice", category="shopping", write=True)
    assert allowed_write is False
    assert "read-only" in reason.lower()

    # Tool execution reflects denial
    t_bob = current_user.set("bob")
    try:
        with pytest.raises(RuntimeError, match="hasn't shared"):
            tools.add_to_list("shopping", "Dark Chocolate", owner="alice")
    finally:
        current_user.reset(t_bob)


def test_read_write_allows_modifications():
    """When shared at read_write level, another user can add items."""
    set_sharing(owner="alice", category="shopping", level="read_write", audience=["bob"])

    t_bob = current_user.set("bob")
    try:
        res = tools.add_to_list("shopping", "Almond Milk", owner="alice")
        assert "Almond Milk" in res
    finally:
        current_user.reset(t_bob)

    # Alice sees the item
    t_alice = current_user.set("alice")
    try:
        items = tools.read_list("shopping")
        assert "Almond Milk" in items
    finally:
        current_user.reset(t_alice)


def test_revoke_sharing():
    """Revoking sharing immediately blocks subsequent access."""
    set_sharing(owner="alice", category="calendar_schedule", level="read")
    allowed, _ = authorize(actor="bob", owner="alice", category="calendar_schedule")
    assert allowed is True

    revoke_sharing(owner="alice", category="calendar_schedule")
    allowed_after, _ = authorize(actor="bob", owner="alice", category="calendar_schedule")
    assert allowed_after is False


def test_tier_4_unshareable_enforcement():
    """Tier 4 categories (banking, payments, account management) can never be shared."""
    ok, msg = set_sharing(owner="alice", category="payments_banking", level="read")
    assert ok is False
    assert "cannot be shared" in msg.lower()

    # Even if forcefully injected, authorize() rejects
    allowed, reason = authorize(actor="bob", owner="alice", category="payments_banking")
    assert allowed is False
    assert "cannot be shared" in reason.lower()


# ===========================================================================
# 3. Cross-User Audit Trail
# ===========================================================================

def test_audit_logs_cross_user_attempts():
    """All cross-user access attempts (allowed and denied) are logged to the sharing audit."""
    # Attempt 1: Denied
    authorize(actor="bob", owner="alice", category="calendar_schedule", write=False)

    # Attempt 2: Allowed
    set_sharing(owner="alice", category="calendar_schedule", level="read", audience="all_enrolled")
    authorize(actor="bob", owner="alice", category="calendar_schedule", write=False)

    entries = recent_sharing_audit(10)
    assert len(entries) >= 2

    # Check denial entry
    denied_entry = next(e for e in entries if e["actor"] == "bob" and not e["allowed"])
    assert denied_entry["owner"] == "alice"
    assert denied_entry["category"] == "calendar_schedule"

    # Check allowed entry
    allowed_entry = next(e for e in entries if e["actor"] == "bob" and e["allowed"])
    assert allowed_entry["owner"] == "alice"


# ===========================================================================
# 4. FastAPI Sharing Endpoints
# ===========================================================================

def test_api_get_and_post_sharing():
    """Test /api/sharing GET and POST endpoints."""
    client = TestClient(app)

    # Initially empty or default
    resp = client.get("/api/sharing?user=priya")
    assert resp.status_code == 200
    assert resp.json()["owner"] == "priya"

    # Set sharing via POST
    resp_post = client.post(
        "/api/sharing",
        data={
            "owner": "priya",
            "category": "calendar_schedule",
            "level": "read",
            "audience": "rahul,alice",
        },
    )
    assert resp_post.status_code == 200
    data = resp_post.json()
    assert data["status"] == "updated"
    assert "calendar_schedule" in data["policy"]

    # Verify via GET
    resp_get = client.get("/api/sharing?user=priya")
    assert resp_get.status_code == 200
    policies = resp_get.json()["policies"]
    assert policies["calendar_schedule"]["level"] == "read"
    assert policies["calendar_schedule"]["audience"] == ["rahul", "alice"]

    # Audit endpoint
    resp_audit = client.get("/api/sharing/audit")
    assert resp_audit.status_code == 200
    assert "events" in resp_audit.json()
