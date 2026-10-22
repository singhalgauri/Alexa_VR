# Tool & SDK Feedback (docs/FEEDBACK.md)

This document contains factual observations and developer feedback based on building **Voice Gate for Alexa+** during the Amazon Developer Hackathon 2026.

---

## 1. MCP Python SDK (`mcp==2.3.0`)

### What We Used It For
- Creating a self-hosted Model Context Protocol server over Streamable HTTP (`/mcp` on port 8765).
- Registering 8 domain tools (`get_time`, `add_reminder`, `list_reminders`, etc.) with FastMCP decorators.
- Connecting as an MCP client in `app/assistant.py` to negotiate protocol version `2025-11-25+`, list tools, and execute tool calls with Bearer token authentication.

### What Worked Well
- **Specification Compliance**: The SDK successfully negotiated protocol version `2025-11-25` (and up to `2026-07-28`) out of the box.
- **FastMCP Tool Ergonomics**: Registering tools with docstrings and type annotations automatically generates JSON-Schema compliant tool definitions without boilerplate.
- **Streamable HTTP Transport**: Modern bi-directional streaming over HTTP/SSE simplifies firewall traversal compared to raw WebSockets or STDIO pipes.

### What Needs Work / Friction Points
- **Client Header Configuration**: `streamable_http_client` does not accept a direct `headers` dictionary; it requires passing an `httpx2.AsyncClient(headers=...)` instance. This was not immediately obvious from top-level docstrings and required source inspection.
- **DNS Rebinding Protection Defaults**: FastMCP's `streamable_http_app` defaults `TransportSecuritySettings(enable_dns_rebinding_protection=True)`. When executing under `TestClient` or reverse proxies where `Host` is `testserver` or container aliases, requests return HTTP `421 Misdirected Request` without an explanatory log message.
- **ASGI Mounting Ambiguity**: `mcp.streamable_http_app()` defaults internal routing to `/mcp`. If a developer mounts it in Starlette via `Mount("/mcp", app=mcp_asgi)`, the effective route inadvertently becomes `/mcp/mcp`. Adding middleware directly to `mcp.streamable_http_app()` resolves this cleanly.

### Onboarding & Verdict
- **Onboarding Rating**: 7.5/10. The concept is powerful, but documentation for HTTP/SSE transport client usage lags behind STDIO examples.
- **Would we build with it again?**: **Yes**. The tool ecosystem and protocol standardization make it the superior choice for agent tool integration.

---

## 2. SpeechBrain ECAPA-TDNN (`speechbrain==1.0.2`)

### What We Used It For
- Computing 192-dimensional speaker embeddings from 16 kHz WAV audio buffers.
- Calculating user centroid voiceprints and computing cosine similarity during speaker verification.

### What Worked Well
- **Acoustic Accuracy**: ECAPA-TDNN provides strong speaker separation even on brief (1-3 second) conversational utterances.
- **Clean In-Memory Processing**: Works seamlessly with PyTorch tensors converted directly from NumPy arrays without writing temporary files to disk.

### What Needs Work / Friction Points
- **Import Path Instability**: SpeechBrain v0.5 used `speechbrain.pretrained`, while SpeechBrain v1.0+ introduced `speechbrain.inference.speaker`. Code targeting both requires defensive `try/except ImportError` handling.
- **Heavy Dependency Footprint**: Installing SpeechBrain pulls in PyTorch, TorchAudio, HuggingFace Hub, and CUDA packages totaling ~2.5 GB. For lightweight edge gateways or quick testing, a distilled ONNX runtime option would be substantially faster to deploy.

### Onboarding & Verdict
- **Onboarding Rating**: 8/10. Pretrained weights on HuggingFace are easily loaded with `from_hparams`.
- **Would we build with it again?**: **Yes**, but in production we would export the ECAPA-TDNN graph to ONNX to eliminate PyTorch runtime overhead.

---

## 3. FastAPI & Starlette

### What We Used It For
- Serving the main REST API (`/api/enroll`, `/api/users`, `/api/ask`, `/api/challenge`, `/api/audit`) and static assets.
- Writing the ASGI `BearerTokenMiddleware` to enforce zero-trust authentication on the MCP server.

### What Worked Well
- **Asynchronous Throughput**: Handled concurrent multipart WAV uploads and MCP client requests with minimal latency (< 15ms overhead).
- **ContextVars Integration**: Starlette middleware coupled with Python's standard `contextvars.ContextVar` provided foolproof identity propagation to MCP tools without polluting function signatures.

### What Needs Work
- **BaseHTTPMiddleware Streaming Limitations**: Starlette's `BaseHTTPMiddleware` buffers certain request bodies. When streaming SSE chunks, standard ASGI middleware functions or Starlette mounts must be carefully managed to avoid breaking long-lived generator connections.

### Onboarding & Verdict
- **Onboarding Rating**: 10/10. Gold standard for modern Python web services.
- **Would we build with it again?**: **Absolutely**.

---

## 4. Browser Web Speech API (`SpeechRecognition` & `SpeechSynthesis`)

### What We Used It For
- Client-side parallel transcription in Google Chrome while simultaneously recording raw WAV PCM audio.
- Text-to-speech spoken playback of assistant responses.

### What Worked Well
- **Zero API Key Requirement**: Gives the user a live voice assistant feeling directly in the browser without requiring third-party cloud speech transcription credits.
- **Instant Response Synthesis**: `window.speechSynthesis` provides immediate spoken feedback without audio download buffering.

### What Needs Work
- **Browser Inconsistency**: Requires vendor prefixing (`webkitSpeechRecognition`) and is primarily reliable only on Chromium browsers. Firefox and Safari require manual typing fallback.
- **Microphone Contention**: Simultaneous audio recording via `AudioContext` and `SpeechRecognition` can cause audio glitches on certain operating system audio drivers if sample rates differ.

### Onboarding & Verdict
- **Onboarding Rating**: 7/10. Great for demonstrations and prototypes; production would use an open-source server-side STT (such as Whisper).
- **Would we build with it again?**: **Yes** for rapid browser prototyping and hackathons.
