# Architectural Decisions & Technical Log (docs/DECISIONS.md)

Project: **Voice Gate for Alexa+**  
Hackathon Track: **Alexa+ (Amazon Developer Hackathon 2026)**  
Target: **Self-Hosted MCP Server with Streamable HTTP & Biometric Voice Verification**

---

## 1. Context & Motivation

Alexa+ introduces agentic abilities powered by external tools, skills, and Model Context Protocol (MCP) servers. However, voice assistants in shared physical spaces (living rooms, shared offices, kitchen smart displays) suffer from a critical vulnerability: **ambient voice access**. Anyone physically in proximity—guests, children, television audio, or strangers—can issue commands to read private reminders, inspect personal lists, trigger smart home devices, or alter thermostats.

`Voice Gate for Alexa+` solves this by introducing a cryptographic speaker-verification gate in front of the assistant and its underlying MCP server.

---

## 2. Key Decisions & Rationale

### Decision 1: Self-Hosted MCP Server with Streamable HTTP Transport
- **Choice**: Implemented using the official MCP Python SDK (`mcp==2.3.0`) with `StreamableHTTPTransport`.
- **Protocol Version**: The hackathon spec requires protocol version `2025-11-25` or later. We verified that `mcp==2.3.0` advertises and supports `2025-11-25` (and forward compatibility up to `2026-07-28`). The negotiated protocol version is verified dynamically via our unit tests (`tests/test_mcp_auth_and_protocol.py`).
- **Transport & Security**: FastMCP `streamable_http_app` natively includes DNS rebinding protections. To allow seamless local development and Docker deployment without host mismatches, we configured `TransportSecuritySettings(enable_dns_rebinding_protection=False)` within the ASGI factory.

### Decision 2: Zero-Trust Bearer Token Authorization for MCP Tools
- **Choice**: The MCP server is mounted with a Starlette ASGI middleware (`BearerTokenMiddleware`) that inspects the `Authorization: Bearer <token>` header on every incoming tool-bearing request.
- **Token Format & TTL**: Compact HMAC-SHA256 tokens minted using a dedicated secret (`VOICEGATE_SECRET`) with a strict 60-second time-to-live (`VOICEGATE_TOKEN_TTL`).
- **Identity ContextVar**: User identity is extracted by the middleware and injected into a thread-safe `contextvars.ContextVar` (`current_user`). MCP tool definitions (`mcp_server/tools.py`) read the caller's identity strictly from `current_user.get()`.
- **Rationale**: User identity is **never** accepted as a tool argument from the LLM or client, preventing prompt injection or client-side impersonation attacks. Unauthenticated, expired, or tampered calls immediately terminate with HTTP `401 Unauthorized`.

### Decision 3: Biometric Privacy & Storage Isolation
- **Choice**: Store strictly 192-dimensional L2-normalized speaker embedding centroids as `.npy` arrays. **Raw audio is never written to disk or recorded in logs.**
- **Enrollment Validation**: Requires a minimum of 3 speech samples (recommended 4) and user names validated against `^[A-Za-z0-9_-]{1,32}$`. Consent is strictly mandatory via both UI checkbox and `/api/enroll` validation (`consent=true`).
- **Data Location**: Configurable via `VOICEGATE_DATA` (defaults to `voiceprints/`). Re-enrollment completely overwrites existing embeddings, and complete deletion is supported via `DELETE /api/users/{name}`.

### Decision 4: Deterministic vs. LLM Dual-Routing Brain
- **Choice**: The default planner in `app/assistant.py` is a deterministic regex intent router supporting messy natural language ("uh remind me to call Priya tomorrow", "put milk on my shopping list", "turn off the bedroom light").
- **Optional LLM Planner**: An optional LLM planner is pluggable via `VOICEGATE_LLM=openai|anthropic|none` (default `none`).
- **Rationale**: Ensures judges and developers can clone and execute 100% of the functionality for free without requiring proprietary API keys or cloud credentials.

### Decision 5: Test Suite Independence from Heavy ML Weights
- **Choice**: Created a modular mock embedder (`FakeSpeakerEmbedder`) that simulates 192-d unit vectors with deterministic acoustic tone mapping and frequency separation.
- **Rationale**: Allows the complete 26-test suite (`pytest`) to run in under 3 seconds in CI/CD or resource-constrained environments without downloading 400MB+ PyTorch/SpeechBrain weights, while thoroughly exercising enrollment, cosine similarity threshold gating, token signing, protocol negotiation, and tool isolation.

### Decision 6: Explicit Simulation Notice
- **Choice**: The application is explicitly architected and presented as a **simulated Alexa+ experience**.
- **Rationale**: Production Alexa hardware does on-device acoustic preprocessing and cloud-side ASR/TTS. Injecting raw PCM WAV buffers into a custom speaker-recognition gate requires audio pipeline access that the real proprietary Alexa OS does not currently expose to third-party skills. A full-fidelity web simulation with real MCP tooling accurately demonstrates the end-to-end architecture.

### Decision 7: Per-User Personal Data Isolation & Relative Date Resolving (Feature A)
- **Choice**: Store schedule events, reminders, shopping lists, custom routines, and profiles in isolated per-user JSON documents (`data/<user>.json`), with directory gitignored and zero raw audio persistence.
- **Identity Integrity**: Tool invocations strictly retrieve identity from the authenticated `current_user` ContextVar set by `BearerTokenMiddleware`. Identity is never accepted as an untrusted tool parameter from the LLM or client.
- **Dynamic Relative Dates**: Schedule queries resolve messy relative dates ("today", "tomorrow", day names like "Monday") dynamically against local time so demo data remains fresh across timezones and dates.

### Decision 8: Cross-User Sharing Policy Engine & Single `authorize()` Choke Point (Feature B)
- **Choice**: Centralized default-deny cross-user sharing engine (`mcp_server/sharing.py`) governed by a single `authorize(actor, owner, category, write)` choke point.
- **Default-Deny Model**: Sharing is strictly OFF for all categories unless an enrolled owner explicitly grants access.
- **Audience & Level Granularity**: Owners can grant `read` or `read_write` permissions to `all_enrolled` family members or a specific subset of user IDs.
- **Tier 4 Absolute Protection**: Sensitive categories (Tier 4 Admin, Banking, Payments, Voice PINs, Device Settings) can **never** be shared across users; attempts to configure or authorize cross-user access fail unconditionally.
- **Zero-Data-Leak Audit Logging**: Every cross-user access attempt—whether permitted or rejected—is logged to an append-only JSONL audit trail (`data/_sharing_audit.jsonl`), recording actor, owner, category, action, and decision reason.

