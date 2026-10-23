# Devpost Submission: Voice Gate for Alexa+

**Tagline:** Cryptographic speaker-recognition gate protecting self-hosted MCP tools for Alexa+ with short-lived tokens and zero-trust biometric isolation.

---

## Inspiration

Voice assistants are shifting from simple weather forecasts to powerful agentic assistants via the **Model Context Protocol (MCP)**. With tools that can manipulate smart home devices, create financial reminders, control thermostats, or query personal databases, physical security becomes paramount.

In a shared home or office, traditional voice assistants suffer from **ambient voice vulnerability**: anyone within earshot—a guest, a curious child, a prankster, or television audio—can trigger powerful agent skills. Current Alexa skills and MCP servers cannot distinguish *who* is speaking at the tool boundary.

We built **Voice Gate for Alexa+** to bring true zero-trust biometric security to agentic assistants: **only enrolled voices can access tools, and unverified speakers are stopped cold at the gate before any MCP tool is ever reached.**

---

## What It Does

1. **Biometric Voice Gate**:
   - Compares the speaker's audio against pre-enrolled voiceprints using 192-dimensional ECAPA-TDNN embeddings.
   - Calculates cosine similarity against user centroids. If the similarity score falls below the security threshold (default 0.40), access is immediately denied.
   - **Zero Raw Audio Storage**: Raw audio is never saved to disk or logs; only mathematical embedding centroids are persisted.
2. **Short-Lived Token Minting**:
   - If and only if the speaker's voice matches an enrolled voiceprint, the gate mints a 60-second HMAC-SHA256 signed bearer token.
3. **Protected Self-Hosted MCP Server**:
   - Runs on port 8765 using the official MCP Python SDK (`mcp==2.3.0`) over Streamable HTTP (spec version `2025-11-25` or later).
   - An ASGI security middleware validates the Bearer token on every tool call and injects the verified identity into a secure `ContextVar`.
   - Identity is **never** accepted as an LLM argument, eliminating prompt injection and identity tampering.
   - Comprehensive suite of 17 per-user tools across schedules, reminders, shopping lists, device controls, and sharing controls.
4. **Feature A: Personalized Responses from Per-User Data**:
   - Isolated JSON stores for each enrolled household member (`data/<user>.json`).
   - Dynamic relative date parsing ("today", "tomorrow", day names) keeps agendas fresh.
   - Time-of-day greetings and personalized prefixes tailored to the authenticated speaker.
5. **Feature B: Fine-Grained Cross-User Sharing & Default-Deny Choke Point**:
   - Default-deny architecture governed by a single `authorize()` choke point.
   - Granular sharing levels (`off`, `read`, `read_write`) and audience targeting (`all_enrolled` or specific family members).
   - Absolute protection of Tier 4 categories (Payments, Banking, PINs, Admin Settings) which are permanently unshareable.
   - Immutable cross-user audit log (`/api/sharing/audit`) recording all access attempts with zero data leakage.
6. **Rich Alexa-Inspired Visual Interface**:
   - Dark-mode responsive UI with a pulsating listening orb (Idle, Listening, Verifying, Granted, Denied states).
   - Dedicated **Data Sharing & Privacy Controls** tab to view/toggle permissions in real time.
   - Real-time MCP activity stream showing tool name, input arguments, execution result, and millisecond latency.
   - Guided enrollment workflow with biometric consent disclosure and deletion controls.
   - Live audit logging feeds tracking authentication attempts and cross-user data accesses.

---

## How We Built It

- **Audio & Biometrics**: Browser PCM audio capture via `AudioContext` with custom RIFF/WAVE 16-bit header assembly; SpeechBrain ECAPA-TDNN neural speaker embeddings; cosine similarity centroid matching.
- **MCP Server**: FastMCP server with Streamable HTTP transport adhering to MCP specification version `2025-11-25`. Starlette ASGI middleware with HMAC-SHA256 bearer token validation.
- **Backend**: FastAPI orchestrating `/api/enroll`, `/api/users`, `/api/ask`, `/api/challenge`, and `/api/audit`. Sliding-window rate limiting on brute-force verification failures.
- **Frontend**: Vanilla HTML5, CSS3, and JavaScript utilizing Web Speech API for real-time speech transcription and synthesis, coupled with an interactive canvas-like reactive orb.
- **Testing & Tooling**: Comprehensive 26-test suite (`pytest`) executing without cloud credentials or heavy PyTorch downloads via an isolated deterministic mock embedder. Standalone threshold evaluation script (`scripts/eval_threshold.py`) measuring False Acceptance Rate (FAR), False Rejection Rate (FRR), and Equal Error Rate (EER).

---

## Challenges We Overcame

1. **Protocol Negotiation**: Ensuring the newly released MCP Streamable HTTP transport cleanly negotiated protocol version `2025-11-25+` while integrating custom Starlette authentication middleware without disrupting session management.
2. **Browser Audio Encoding**: Browsers natively record compressed WebM/Opus via MediaRecorder, which degrades speaker recognition acoustic models. We built a custom in-browser AudioBuffer to 16-bit linear PCM mono WAV encoder.
3. **Cross-User Data Leakage**: Enforcing strict tool-level isolation by coupling Starlette middleware with Python `contextvars`, guaranteeing that tool functions never read identity from user-supplied parameters.

---

## Accomplishments That We're Proud Of

- **100% Free & Open Source**: Zero paid API keys or AWS credentials required to run the full application and test suite out of the box.
- **Passed All 26 Pytest Suites in Under 3 Seconds**: Thorough automated verification spanning crypto tokens, threshold gating, protocol negotiation, and security isolation.
- **Complete Privacy Safeguards**: Biometric consent checkboxes, instant deletion APIs, and zero raw audio persistence.

---

## What We Learned

- How the MCP Streamable HTTP protocol establishes and manages stateful bi-directional JSON-RPC sessions over modern web transports.
- How ECAPA-TDNN speaker embedding centroids behave across varying acoustic conditions, and how to calibrate decision thresholds using FAR/FRR curves.

---

## Built During Hackathon Disclosure

All code, tests, documentation, and assets for this project were designed and implemented from scratch within the official Amazon Developer Hackathon 2026 window.

---

## Open Source Mini-Challenge

- **License**: MIT License (included in repository root [LICENSE](file:///f:/Alexa_VR/LICENSE))
- **GitHub Username**: `<YOUR_GITHUB_USERNAME>`
- **Repository URL**: `https://github.com/<YOUR_GITHUB_USERNAME>/voice-gate-alexa-plus`
- **Contribution / Submission URL**: `https://github.com/<YOUR_GITHUB_USERNAME>/voice-gate-alexa-plus`
- **Dependencies**: Exclusively open-source libraries (FastAPI, MCP SDK, PyJWT, SpeechBrain, SoundFile, Pytest). No proprietary or AWS-exclusive services required.
