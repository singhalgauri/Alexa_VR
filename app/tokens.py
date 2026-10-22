"""
app/tokens.py – HMAC-signed short-lived token management.

Tokens are compact JWTs signed with HS256.
Payload: { "sub": "<user>", "exp": <unix_epoch_int> }
Expiry: 60 seconds (configurable via VOICEGATE_TOKEN_TTL env var).
Secret: VOICEGATE_SECRET env var, or random bytes per process start.
"""

from __future__ import annotations

import os
import time
import secrets
import hmac
import hashlib
import base64
import json

# ---------------------------------------------------------------------------
# Secret bootstrap
# ---------------------------------------------------------------------------
_SECRET: bytes = os.environ.get("VOICEGATE_SECRET", "").encode() or secrets.token_bytes(32)

TOKEN_TTL: int = int(os.environ.get("VOICEGATE_TOKEN_TTL", "60"))  # seconds


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def mint_token(user: str) -> str:
    """Mint a signed token for *user* valid for TOKEN_TTL seconds."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"sub": user, "exp": int(time.time()) + TOKEN_TTL}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(_SECRET, signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def verify_token(token: str) -> str:
    """
    Verify *token* and return the user name.
    Raises ValueError on invalid / expired / tampered tokens.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token: expected 3 parts")
    header_b64, payload_b64, sig_b64 = parts

    # Verify signature
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(_SECRET, signing_input, hashlib.sha256).digest()
    try:
        provided_sig = _b64url_decode(sig_b64)
    except Exception:
        raise ValueError("Malformed token: bad base64 in signature")
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Token signature invalid")

    # Decode payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise ValueError("Malformed token: bad payload")

    exp = payload.get("exp", 0)
    if time.time() > exp:
        raise ValueError("Token expired")

    user = payload.get("sub")
    if not user or not isinstance(user, str):
        raise ValueError("Token missing subject")

    return user
