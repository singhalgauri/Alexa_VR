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
from typing import Optional

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
from access_control import (  # noqa: E402
    PolicyEnforcementGate,
    Role,
    SensitivityTier,
    SpeakerIdentificationResult,
    CATEGORY_TIER_MAPPING,
)

policy_gate = PolicyEnforcementGate()

# Sync voiceprint files with policy store enrolled users on startup
try:
    from pathlib import Path as _P
    _vp_dir = _P(os.environ.get("VOICEGATE_DATA", os.environ.get("VOICEPRINTS_DIR", "voiceprints")))
    if _vp_dir.exists():
        for _npy in _vp_dir.glob("*.npy"):
            _uname = _npy.stem.lower()
            policy_gate.policy_store._enrolled_users.add(_uname)
        policy_gate.policy_store.save()
except Exception:
    pass

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
    "voice is key",
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
    pin: Optional[str] = Form(default=None),
    clips: list[UploadFile] = File(...),
):
    """Enroll a voice: requires consent, ≥3 WAV clips, and a valid user name.
    
    Optionally accepts a 4-8 digit numeric PIN that is stored in PolicyStore
    and used as a second-factor for sensitive operations and guest permission
    grants.
    """
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
        policy_gate.policy_store._enrolled_users.add(name.lower())
        try:
            from app.assistant import register_enrolled_users
            register_enrolled_users({name.lower()})
        except Exception:
            pass
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Enrollment error: {exc}")

    # Register the user in PolicyStore so downstream policy checks recognise them
    policy_gate.policy_store.enroll_user(name)

    # Persist the PIN if one was provided
    pin_status: Optional[str] = None
    if pin is not None:
        ok, msg = policy_gate.policy_store.set_user_pin(name, pin)
        pin_status = msg if ok else f"PIN not saved: {msg}"

    result: dict = {"status": "enrolled", "user": name, "clips": len(wav_bytes_list)}
    if pin_status:
        result["pin_status"] = pin_status
    else:
        result["pin_status"] = "No PIN provided — you can set one from the Access Control panel."
    return result


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
    pin: Optional[str] = Form(default=None),
    confirmed: bool = Form(default=False),
):
    """
    Verify the speaker, evaluate access control policy, mint token if permitted, return reply.

    Returns:
      { granted, user, effective_role, tier, tier_name, category, score, reply, mcp_trace, status, reason }
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

    # 1. Voice verification against enrolled centroids
    gate = get_gate()
    result = gate.verify_any(audio_data)

    speaker_id = result.user if result.granted else None
    confidence = float(result.score)

    if speaker_id:
        policy_gate.policy_store._enrolled_users.add(speaker_id)

    # 2. Package into SpeakerIdentificationResult
    speaker_res = SpeakerIdentificationResult(
        user_id=speaker_id,
        confidence=confidence,
        detected_speakers=[speaker_id] if speaker_id else ["guest"],
        is_ambiguous=False,
        is_live=True,
    )

    # 3. Policy Enforcement Gate evaluation
    decision = policy_gate.process_request(
        utterance=transcript,
        speaker_result=speaker_res,
        pin_attempt=pin,
        confirmation=confirmed,
    )

    log_event(
        granted=result.granted,
        ip=ip,
        user=result.user,
        score=confidence,
        query=transcript if result.granted else None,
    )

    if not result.granted:
        record_failure(ip)

    # 4. If voice verified AND allowed by policy -> mint token and call MCP brain
    if result.granted and decision.allowed:
        record_success(ip)
        token = mint_token(decision.speaker_id)
        try:
            import inspect
            from app.assistant import ask as brain_ask
            res = brain_ask(transcript, token)
            if inspect.isawaitable(res):
                brain_result = await res
            else:
                brain_result = res
            final_reply = brain_result["reply"]
            mcp_trace = brain_result.get("mcp_trace", [])
        except Exception as exc:
            final_reply = decision.response_message or f"Assistant error: {exc}"
            mcp_trace = []

        return JSONResponse({
            "granted": True,
            "policy_allowed": True,
            "user": decision.speaker_id,
            "effective_role": decision.effective_role.value,
            "tier": decision.tier.value,
            "tier_name": decision.tier.name,
            "category": decision.category,
            "score": confidence,
            "reply": final_reply,
            "mcp_trace": mcp_trace,
            "status": decision.status.value,
            "requires_pin": decision.requires_second_factor,
            "requires_confirm": decision.requires_confirmation,
            "reason": decision.reason,
        })

    # 5. If voice NOT recognized but category is permitted for guests (e.g. Tier 0 emergency, Tier 1 weather/music)
    if not result.granted and decision.allowed:
        return JSONResponse({
            "granted": False,
            "policy_allowed": True,
            "user": None,
            "effective_role": "guest",
            "tier": decision.tier.value,
            "tier_name": decision.tier.name,
            "category": decision.category,
            "score": confidence,
            "reply": decision.response_message,
            "mcp_trace": [],
            "status": decision.status.value,
            "requires_pin": False,
            "requires_confirm": False,
            "reason": decision.reason,
        })

    # 6. Blocked by policy (or guest blocked from enrolled-only / personal data)
    reply = decision.response_message
    if not result.granted and "not recognized" not in reply.lower():
        reply = f"Voice not recognized. {reply}"

    return JSONResponse({
        "granted": False,
        "policy_allowed": False,
        "user": decision.speaker_id,
        "effective_role": decision.effective_role.value,
        "tier": decision.tier.value,
        "tier_name": decision.tier.name,
        "category": decision.category,
        "score": confidence,
        "reply": reply,
        "mcp_trace": [],
        "status": decision.status.value,
        "requires_pin": decision.requires_second_factor,
        "requires_confirm": decision.requires_confirmation,
        "reason": decision.reason,
    })


# ---------------------------------------------------------------------------
# Access Control Management API
# ---------------------------------------------------------------------------

@app.get("/api/access-control/status")
async def get_access_control_status():
    """Returns current sensitivity tiers and guest permissions."""
    summary = policy_gate.policy_store.get_guest_permissions_summary()
    tiers = {cat: tier.value for cat, tier in CATEGORY_TIER_MAPPING.items()}
    return {
        "guest_permissions": summary,
        "tiers": tiers,
        "enrolled_users": list(policy_gate.policy_store._enrolled_users),
        "thresholds": {
            "normal": policy_gate.policy_store.threshold_normal,
            "high": policy_gate.policy_store.threshold_high,
            "very_high": policy_gate.policy_store.threshold_very_high,
        },
    }


@app.post("/api/access-control/permission")
async def update_guest_permission(
    category: str = Form(...),
    enabled: bool = Form(...),
    user: str = Form(default=""),
    pin: str = Form(default=""),
    confirmed: bool = Form(default=False),
):
    """Updates guest access for a category (requires Tier 4 voice PIN)."""
    if not user or not pin:
        raise HTTPException(401, "Enrolled user identity and voice PIN are required to change permissions.")
    if not policy_gate.second_factor_verifier.verify_pin(user, pin):
        raise HTTPException(403, "Invalid voice PIN.")

    success, msg = policy_gate.policy_store.set_guest_permission(category, enabled, confirmed=confirmed)
    if not success:
        raise HTTPException(400, msg)
    return {
        "status": "updated",
        "message": msg,
        "permissions": policy_gate.policy_store.get_guest_permissions_summary(),
    }


# ---------------------------------------------------------------------------
# Cross-User Sharing Policy endpoints (Feature B)
# ---------------------------------------------------------------------------

from mcp_server.sharing import (  # noqa: E402
    get_all_sharing_policies,
    get_sharing_policy,
    set_sharing,
    recent_sharing_audit,
)


@app.get("/api/sharing")
async def get_sharing(user: str = ""):
    """Return sharing policy for a specific user, or all policies."""
    if user:
        return {"owner": user.lower(), "policies": get_sharing_policy(user)}
    return {"policies": get_all_sharing_policies()}


@app.post("/api/sharing")
async def update_sharing(
    owner: str = Form(...),
    category: str = Form(...),
    level: str = Form(default="off"),
    audience: str = Form(default="all_enrolled"),
):
    """
    Set sharing policy for owner/category.
    level: "off" | "read" | "read_write"
    audience: "all_enrolled" | comma-separated user names
    """
    aud: str | list[str] = audience.strip()
    if "," in aud:
        aud = [u.strip() for u in aud.split(",") if u.strip()]
    elif aud not in ("all_enrolled", ""):
        aud = [aud]
    if not aud:
        aud = "all_enrolled"

    success, msg = set_sharing(owner=owner, category=category, level=level, audience=aud)
    if not success:
        raise HTTPException(400, msg)
    return {
        "status": "updated",
        "message": msg,
        "policy": get_sharing_policy(owner),
    }


@app.get("/api/sharing/audit")
async def sharing_audit_log(n: int = 20):
    """Return recent cross-user sharing audit events."""
    return {"events": recent_sharing_audit(min(n, 100))}


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
