"""
tests/test_api_server.py – End-to-end tests for FastAPI server and Voice Gate protection.
Verifies that unknown voices NEVER reach the MCP server.
"""

import io
import pytest
from starlette.testclient import TestClient
from app.server import app
from tests.conftest import make_synthetic_wav


@pytest.fixture
def client():
    return TestClient(app)


def test_api_users_and_threshold(client):
    resp = client.get("/api/users")
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert "threshold" in data
    assert isinstance(data["threshold"], float)


def test_enroll_requires_consent(client):
    wav = make_synthetic_wav(freq=440.0)
    files = [
        ("clips", ("clip1.wav", wav, "audio/wav")),
        ("clips", ("clip2.wav", wav, "audio/wav")),
        ("clips", ("clip3.wav", wav, "audio/wav")),
    ]
    # consent=false -> 400
    resp = client.post("/api/enroll", data={"name": "alice", "consent": "false"}, files=files)
    assert resp.status_code == 400
    assert "Consent is required" in resp.text

    # consent=true -> 200
    files2 = [
        ("clips", ("clip1.wav", wav, "audio/wav")),
        ("clips", ("clip2.wav", wav, "audio/wav")),
        ("clips", ("clip3.wav", wav, "audio/wav")),
    ]
    resp2 = client.post("/api/enroll", data={"name": "alice", "consent": "true"}, files=files2)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "enrolled"
    assert resp2.json()["user"] == "alice"


def test_unknown_voice_never_calls_mcp_server(client, monkeypatch):
    """Critical security requirement: an unknown voice must NEVER reach the MCP server."""
    # Enroll Alice
    wav_alice = make_synthetic_wav(freq=440.0)
    client.post(
        "/api/enroll",
        data={"name": "alice", "consent": "true"},
        files=[("clips", (f"c{i}.wav", wav_alice, "audio/wav")) for i in range(3)],
    )

    # Instrument app.assistant.ask to ensure it is NEVER called
    mcp_called = []
    def fake_ask(transcript, token):
        mcp_called.append((transcript, token))
        return {"reply": "SHOULD_NOT_BE_CALLED", "mcp_trace": []}

    monkeypatch.setattr("app.assistant.ask", fake_ask)

    # An unknown voice (different frequency 880 Hz) tries to ask a question
    wav_unknown = make_synthetic_wav(freq=880.0)
    resp = client.post(
        "/api/ask",
        data={"transcript": "turn on the living room light"},
        files={"audio": ("query.wav", wav_unknown, "audio/wav")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is False
    assert data["user"] is None
    assert data["score"] < 0.40
    assert "not recognized" in data["reply"].lower()
    assert data["mcp_trace"] == []

    # Assert MCP client was NEVER called!
    assert len(mcp_called) == 0, "Security violation: MCP client was called for an unverified voice!"


def test_known_voice_calls_assistant_and_mcp(client, monkeypatch):
    # Enroll Alice
    wav_alice = make_synthetic_wav(freq=440.0)
    client.post(
        "/api/enroll",
        data={"name": "alice", "consent": "true"},
        files=[("clips", (f"c{i}.wav", wav_alice, "audio/wav")) for i in range(3)],
    )

    # Mock assistant.ask to return a simulated MCP trace
    def fake_ask(transcript, token):
        return {
            "reply": "Living Room Light is now on.",
            "mcp_trace": [
                {
                    "tool": "control_device",
                    "args": {"device": "living room light", "state": "on"},
                    "result": "Living Room Light is now on.",
                    "latency_ms": 12.5,
                }
            ],
        }

    monkeypatch.setattr("app.assistant.ask", fake_ask)

    # Alice speaks
    resp = client.post(
        "/api/ask",
        data={"transcript": "turn on the living room light"},
        files={"audio": ("query.wav", wav_alice, "audio/wav")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["user"] == "alice"
    assert data["score"] >= 0.40
    assert "is now on" in data["reply"]
    assert len(data["mcp_trace"]) == 1
    assert data["mcp_trace"][0]["tool"] == "control_device"


def test_audit_log_records_events(client):
    # Perform an ask attempt
    wav = make_synthetic_wav(freq=440.0)
    client.post(
        "/api/ask",
        data={"transcript": "what time is it"},
        files={"audio": ("q.wav", wav, "audio/wav")},
    )

    audit_resp = client.get("/api/audit")
    assert audit_resp.status_code == 200
    events = audit_resp.json().get("events", [])
    assert len(events) >= 1
    last_event = events[-1]
    assert "ts" in last_event
    assert "granted" in last_event
    assert "ip" in last_event


def test_rate_limiting_after_failures(client, monkeypatch):
    from app.ratelimit import RateLimiter
    custom_limiter = RateLimiter(max_failures=3, window=60, block=300)
    monkeypatch.setattr("app.server.is_blocked", custom_limiter.is_blocked)
    monkeypatch.setattr("app.server.record_failure", custom_limiter.record_failure)
    monkeypatch.setattr("app.server.record_success", custom_limiter.record_success)

    # Probe with un-enrolled voice 3 times
    wav = make_synthetic_wav(freq=999.0)
    for _ in range(3):
        r = client.post(
            "/api/ask",
            data={"transcript": "hello"},
            files={"audio": ("q.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        assert r.json()["granted"] is False

    # 4th attempt should be blocked with 429
    r_blocked = client.post(
        "/api/ask",
        data={"transcript": "hello"},
        files={"audio": ("q.wav", wav, "audio/wav")},
    )
    assert r_blocked.status_code == 429
    assert "Too many failed attempts" in r_blocked.text


def test_challenge_phrase_endpoint(client):
    resp = client.get("/api/challenge")
    assert resp.status_code == 200
    data = resp.json()
    assert "phrase" in data
    assert len(data["phrase"].split()) == 3  # 3-word phrase
