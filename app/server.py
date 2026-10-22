"""
app/server.py – Main FastAPI application for Voice Gate.

Endpoints:
  POST /api/enroll          – enroll a voice (multipart: name + 3-5 WAV clips)
  GET  /api/users           – list enrolled users
  DELETE /api/users/{name}  – delete a user
  POST /api/ask             – verify voice, mint token, call MCP, return reply
  GET  /api/audit           – last 10 audit log entries
  GET  /api/challenge       – get a random liveness challenge phrase
  GET  /static/*            – serve static files

Security:
  - 5 MB upload limit
  - WAV content-type validation
  - Rate limiting on failed verifications
  - No raw audio stored or logged
"""

from __future__ import annotations

import io
import os
import struct
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.audit import log_event, recent_events  # noqa: E402
from app.ratelimit import is_blocked, record_failure, record_success  # noqa: E402
from app.tokens import mint_token  # noqa: E402
from app.voice_gate import get_gate  # noqa: E402

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
STATIC_DIR = Path(__file__).parent.parent / "static"

# Challenge phrases for liveness mode (P1)
_CHALLENGE_PHRASES = [
    "blue mountain river",
    "silver cloud seven",
    "bright orange sky",
    "open sesame now",
    "purple rainy day",
    "echo delta foxtrot",
    "green light steady",
    "winter fox jumps",
    "lambda token gate",
    "voice is the key",
]

LIVENESS_ENABLED: bool = os.environ.get("VOICEGATE_LIVENESS", "0") == "1"

import random
import threading

_current_challenge: dict[str, str] = {}  # ip -> phrase
_challenge_lock = threading.Lock()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_wav(data: bytes) -> bool:
    """Minimal WAV header check: must start with RIFF....WAVE."""
    if len(data) < 12:
        return False
    try:
        riff, _, wave = struct.unpack_from("<4sI4s", data)
        return riff == b"RIFF" and wave == b"WAVE"
    except struct.error:
        return False


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Voice Gate for Alexa+",
    description="Speaker-recognition gate in front of an MCP-powered assistant.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

@app.post("/api/enroll")
async def enroll(
    name: str = Form(...),
    consent: bool = Form(default=True),
    clips: list[UploadFile] = File(...),
):
    """Enroll a voice: requires consent, ≥3 WAV clips, and a valid user name."""
    if not consent:
        raise HTTPException(400, "Consent is required to store voice biometric data")

    gate = get_gate()

    # Validate all clips
    wav_bytes_list: list[bytes] = []
    for clip in clips:
        data = await clip.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload too large (max 5 MB per clip)")
        if not _is_wav(data):
            raise HTTPException(400, "All clips must be WAV audio")
        wav_bytes_list.append(data)

    try:
        gate.enroll(name, wav_bytes_list)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Enrollment error: {exc}")

    return {"status": "enrolled", "user": name, "clips": len(wav_bytes_list)}


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@app.get("/api/users")
async def list_users():
    return {
        "users": get_gate().list_users(),
        "threshold": float(os.environ.get("VOICEGATE_THRESHOLD", "0.40")),
    }


@app.delete("/api/users/{name}")
async def delete_user(name: str):
    existed = get_gate().delete_user(name)
    if not existed:
        raise HTTPException(404, f"User '{name}' not found")
    return {"status": "deleted", "user": name}


# ---------------------------------------------------------------------------
# Challenge phrase (P1 liveness)
# ---------------------------------------------------------------------------

@app.get("/api/challenge")
async def get_challenge(request: Request):
    ip = _get_client_ip(request)
    phrase = random.choice(_CHALLENGE_PHRASES)
    with _challenge_lock:
        _current_challenge[ip] = phrase
    return {"phrase": phrase, "liveness_enabled": LIVENESS_ENABLED}


# ---------------------------------------------------------------------------
# Main ask endpoint
# ---------------------------------------------------------------------------

@app.post("/api/ask")
async def ask(
    request: Request,
    audio: UploadFile = File(...),
    transcript: str = Form(default=""),
):
    """
    Verify the speaker, mint a token, call the MCP brain, return reply.

    Returns:
      { granted, user, score, reply, mcp_trace }
    """
    ip = _get_client_ip(request)

    # Rate-limit check
    if is_blocked(ip):
        raise HTTPException(429, "Too many failed attempts. Please wait before retrying.")

    # Read and validate audio
    audio_data = await audio.read(MAX_UPLOAD_BYTES + 1)
    if len(audio_data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Audio upload too large (max 5 MB)")
    if not _is_wav(audio_data):
        raise HTTPException(400, "Audio must be WAV format")

    # Liveness check (P1)
    if LIVENESS_ENABLED:
        with _challenge_lock:
            expected = _current_challenge.get(ip, "")
        if expected and transcript:
            if expected.lower() not in transcript.lower():
                log_event(granted=False, ip=ip, user=None, score=0.0, query=transcript)
                return JSONResponse({
                    "granted": False,
                    "user": None,
                    "score": 0.0,
                    "reply": "Liveness check failed. Please say the challenge phrase.",
                    "mcp_trace": [],
                })

    # Voice verification
    gate = get_gate()
    result = gate.verify_any(audio_data)

    log_event(
        granted=result.granted,
        ip=ip,
        user=result.user,
        score=result.score,
        query=transcript if result.granted else None,
    )

    if not result.granted:
        record_failure(ip)
        return JSONResponse({
            "granted": False,
            "user": result.user,
            "score": result.score,
            "reply": (
                "Voice not recognized. "
                "Please enroll your voice first, or speak more clearly."
            ),
            "mcp_trace": [],
        })

    record_success(ip)

    # Mint a short-lived token for the MCP server
    token = mint_token(result.user)

    # Call the assistant brain (MCP client)
    try:
        import inspect
        from app.assistant import ask as brain_ask  # local import to allow monkeypatching
        res = brain_ask(transcript, token)
        if inspect.isawaitable(res):
            brain_result = await res
        else:
            brain_result = res
    except Exception as exc:
        brain_result = {
            "reply": f"Assistant error: {exc}",
            "mcp_trace": [],
        }

    return JSONResponse({
        "granted": True,
        "user": result.user,
        "score": result.score,
        "reply": brain_result["reply"],
        "mcp_trace": brain_result["mcp_trace"],
    })


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@app.get("/api/audit")
async def audit_log(n: int = 10):
    return {"events": recent_events(min(n, 50))}


# ---------------------------------------------------------------------------
# Root redirect to static UI
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse  # noqa: E402


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"message": "Voice Gate API – see /docs"})
