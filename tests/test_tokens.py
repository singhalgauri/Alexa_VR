"""
tests/test_tokens.py – Unit tests for short-lived token minting and verification.
"""

import time
import pytest
from app.tokens import mint_token, verify_token, TOKEN_TTL


def test_token_mint_and_verify():
    token = mint_token("alice")
    assert isinstance(token, str)
    assert token.count(".") == 2

    user = verify_token(token)
    assert user == "alice"


def test_token_tampering_detected():
    token = mint_token("alice")
    parts = token.split(".")

    # Tamper with payload (modify first char of base64 payload)
    corrupted_char = "B" if parts[1][0] == "A" else "A"
    tampered_payload = corrupted_char + parts[1][1:]
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    with pytest.raises(ValueError, match="signature invalid|Malformed"):
        verify_token(tampered_token)

    # Tamper with signature
    tampered_sig = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    tampered_sig_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"
    with pytest.raises(ValueError, match="Token signature invalid|Malformed"):
        verify_token(tampered_sig_token)


def test_token_expiry(monkeypatch):
    token = mint_token("alice")

    # Fast forward time beyond TOKEN_TTL
    future_time = time.time() + TOKEN_TTL + 5
    monkeypatch.setattr(time, "time", lambda: future_time)

    with pytest.raises(ValueError, match="Token expired"):
        verify_token(token)


def test_malformed_tokens():
    with pytest.raises(ValueError, match="expected 3 parts"):
        verify_token("invalid.token")

    with pytest.raises(ValueError, match="expected 3 parts"):
        verify_token("")
