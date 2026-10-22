# Developer Friction Log (docs/FRICTION_LOG.md)

Project: **Voice Gate for Alexa+**  
Author: Hackathon Engineering Team  
Event: Amazon Developer Hackathon 2026 (Alexa+ Track)

This log records real technical friction encountered during development, the steps taken, workarounds implemented, and an important architectural feature request for the Alexa+ and MCP teams.

---

## 1. Real Friction Points Encountered

### Friction Point 1: MCP Client Streamable HTTP Signature & Header Injection
- **Task Attempted**: Connecting to the self-hosted MCP server from `app/assistant.py` with custom `Authorization: Bearer <token>` headers using the MCP Python SDK.
- **Steps**: Attempted `from mcp.client.streamable_http import streamable_http_client` and passed `headers={"Authorization": ...}`.
- **Expected**: A straightforward parameter `headers: dict` on `streamable_http_client(url, headers=...)` or context manager yielding `(read_stream, write_stream)`.
- **Actual**: `streamable_http_client` does not accept a `headers` parameter. It accepts `http_client: httpx2.AsyncClient | None`. Passing headers requires creating an explicit `httpx2.AsyncClient(headers=...)` context manager. Additionally, older documentation snippets referenced a 3-tuple return, whereas `mcp 2.3.0` yields a 2-tuple `TransportStreams` `(read_stream, write_stream)`.
- **Severity**: High (caused runtime exceptions on client connection).
- **Workaround**: Wrapped `streamable_http_client` with an outer `async with httpx2.AsyncClient(headers=headers) as http_client:` block.
- **Suggestion**: Add a convenience argument `headers: dict[str, str] | None = None` to `streamable_http_client` so developers don't have to manage a secondary `httpx2` client instance.

---

### Friction Point 2: FastMCP Transport Security & DNS Rebinding in Local Environments
- **Task Attempted**: Running automated integration tests with Starlette's `TestClient` and testing across container networks.
- **Steps**: Sent authenticated requests to `/mcp` using `TestClient(app)` or `curl http://localhost:8765/mcp`.
- **Expected**: HTTP 200/202 streaming JSON-RPC response.
- **Actual**: Server returned HTTP `421 Misdirected Request` with console log: `Invalid Host header: testserver` or `Invalid Host header: 127.0.0.1`.
- **Severity**: Medium-High.
- **Workaround**: Configured `TransportSecuritySettings(enable_dns_rebinding_protection=False)` in `build_app()`.
- **Suggestion**: When running in development mode (or when `host="127.0.0.1"` or `host="localhost"`), automatically whitelist common loopback hosts (`127.0.0.1`, `localhost`, `testserver`, `0.0.0.0`) or output a clear diagnostic pointing to `TransportSecuritySettings`.

---

### Friction Point 3: Double Route Prefix in FastMCP Streamable App Mounting
- **Task Attempted**: Mounting the FastMCP streamable HTTP application into a parent Starlette router.
- **Steps**: Executed `Starlette(routes=[Mount("/mcp", app=mcp.streamable_http_app())])`.
- **Expected**: Requests to `/mcp` reach the MCP protocol handler.
- **Actual**: `mcp.streamable_http_app()` already creates an internal route `/mcp`. Mounting it under `/mcp` resulted in the endpoint moving to `/mcp/mcp`, returning 404 on `/mcp`.
- **Severity**: Medium.
- **Workaround**: Used `mcp.streamable_http_app()` as the root application and attached middleware directly via `app.add_middleware(BearerTokenMiddleware)`.
- **Suggestion**: Document clearly whether `streamable_http_app` is intended to be a standalone root application or a mountable sub-application, and allow `streamable_http_path=""` or `"/"` when mounted under Starlette `Mount`.

---

### Friction Point 4: Browser Audio Recording vs. Speech Recognition Format Discrepancy
- **Task Attempted**: Capturing microphone audio in the browser suitable for SpeechBrain's ECAPA-TDNN embedding extractor while simultaneously running live transcription.
- **Steps**: Attempted using the browser's native `MediaRecorder` API.
- **Expected**: A standard uncompressed WAV stream.
- **Actual**: Browsers only produce containerized Opus/WebM via `MediaRecorder`. Passing Opus/WebM to the server required server-side FFmpeg transcoding, adding latency and dependency overhead.
- **Severity**: Medium.
- **Workaround**: Implemented an in-browser `AudioContext` with custom RIFF/WAVE header assembly (16-bit linear PCM mono) directly in JavaScript, streaming clean 16 kHz WAV blobs to the server.
- **Suggestion**: Provide an official lightweight client-side audio capture utility in the Alexa+/MCP developer documentation for web simulations.

---

### Friction Point 5: Windows Python App Execution Aliases
- **Task Attempted**: Invoking `python run.py` on Windows 11.
- **Steps**: Ran `python` from command prompt / PowerShell.
- **Expected**: The installed Python interpreter executes.
- **Actual**: Windows redirected to the Microsoft Store app execution alias ("Python was not found...").
- **Severity**: Low.
- **Workaround**: Used `py -3.10` or direct path to the Python binary in virtual environment `.venv\Scripts\python.exe`.
- **Suggestion**: Document `py -3.10 run.py` and provide both `run.bat` and `run.sh` entry points.

---

## 2. Feature Request for the Alexa+ & MCP Platform

> **Status:** IMPORTANT  
> **Target:** Alexa+ Platform & Model Context Protocol (MCP) Working Group  
> **Feature Request:** **Expose Verified Speaker Identity to MCP Tools / Agent Skills**

### Background & Problem Statement
Currently, Alexa skills and MCP servers operate on a **caller-agnostic** paradigm. When an agent tool is invoked (e.g., `add_reminder(text="Call doctor")`, `read_list(name="shopping")`, or `unlock_door(entry="front")`), the tool receives arguments generated solely by the LLM. 

Because the platform does not inject authenticated speaker identity metadata into the tool call context:
1. **Tool developers have no reliable way to isolate user data**: Tools must either share a single global state (leaking private reminders between family members or office colleagues) or rely on LLM-supplied parameters like `user="Alice"`, which can be easily spoofed or injected by ambient voices.
2. **High-consequence tools cannot enforce zero-trust permissions**: A smart lock, garage door opener, thermostat, or financial skill should only execute if the physical speaker has verified biometric authority.

### Proposed Solution
1. **Platform-Level Biometric Attestation**: Alexa+ already possesses acoustic speaker identification capabilities on Echo devices ("Alexa, who am I?"). The platform should attach a cryptographically signed attestation header (e.g., `X-MCP-Speaker-Identity: <JWT>` or signed MCP request metadata) to tool calls dispatched to self-hosted MCP servers.
2. **Contextual Identity Exposure**: In the MCP SDK, expose this verified speaker claim through an official context object (similar to how `current_user` was implemented in this project via `contextvars`), completely separate from the LLM tool arguments schema.
3. **Benefits**:
   - Zero risk of LLM prompt injection overriding user identity.
   - Per-speaker data compartmentalization for reminders, shopping lists, and media preferences.
   - Safe execution of sensitive smart-home actions in multi-occupant homes.
