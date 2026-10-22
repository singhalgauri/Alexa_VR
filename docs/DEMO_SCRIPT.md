# Shot-by-Shot Demo Video Script (docs/DEMO_SCRIPT.md)

**Total Duration:** Under 3 minutes (02:45 target)  
**Language:** English  
**Audio Style:** Clear speaking voice; strictly **NO background music** or loud effects.  
**Screen Setup:** 1080p full-screen browser window (`http://localhost:8000`) on left, terminal on right.

---

## Shot 1: The Hook – Enrolled Voice Access & Live MCP Tool Execution
- **Time:** `0:00 - 0:25` (25 seconds)
- **Visual:** Web UI open with dark mode glowing orb. Enrolled user ("Alice") already listed.
- **Action:** Click "Hold to Speak" (or tap microphone). Say: *"Alexa, turn on the living room light."*
- **Screen Reaction:**
  1. Orb transitions from Blue (Listening) to Amber (Verifying) to Emerald Green (Access Granted).
  2. Access banner displays: **ACCESS GRANTED (Alice)** with similarity score `96.4%`.
  3. Spoken reply plays: *"Living Room Light is now on."*
  4. Right panel **MCP Activity Stream** populates with real-time trace:
     - Tool: `control_device`
     - Args: `{"device": "living room light", "state": "on"}`
     - Result: `Living Room Light is now on.`
     - Latency: `14.2 ms`
- **Narration (Spoken):**
  > *"Welcome to Voice Gate for Alexa+. In multi-person spaces, anyone can command your smart home. But watch what happens when an enrolled user speaks: my voice is verified in milliseconds, access is granted, and our self-hosted MCP server securely executes the device control tool."*

---

## Shot 2: The Refusal – Unknown Voice Blocked Cold
- **Time:** `0:25 - 0:50` (25 seconds)
- **Visual:** Same web UI.
- **Action:** A different speaker speaks (or use pitch-shifted sample/guest voice): *"Alexa, read my private reminders."*
- **Screen Reaction:**
  1. Orb pulses Red (Access Denied).
  2. Banner flashes: **ACCESS DENIED** (Score: `18.2%`, well below threshold `40.0%`).
  3. Assistant reply: *"Voice not recognized. Please enroll your voice first, or speak more clearly."*
  4. Notice the **MCP Activity Stream remains completely empty**!
- **Narration (Spoken):**
  > *"Now, an unknown voice attempts to read private reminders. The ECAPA-TDNN embedding gate compares the audio against enrolled centroids. The score is only 18%—below our 40% threshold. The assistant refuses, and crucially: zero requests reach the MCP server. Private data stays private."*

---

## Shot 3: Fast, Guided Biometric Enrollment Flow
- **Time:** `0:50 - 1:20` (30 seconds)
- **Visual:** Click "Enroll Voice" tab on the web app.
- **Action:**
  1. Enter name: `bob`.
  2. Check the mandatory biometric consent box: *"I consent to storing my voiceprint"*.
  3. Step through the 4 prompted phrases ("Hello Alexa...", "The quick brown fox...", etc.), recording 4 brief audio clips.
  4. Click "Submit Enrollment".
  5. User list updates instantly to show `alice` and `bob`.
- **Narration (Spoken):**
  > *"Enrolling a new voice takes seconds. We require explicit biometric consent and at least three speech samples to compute an accurate 192-dimensional centroid. Once enrolled, Bob can immediately access his own isolated lists and reminders."*

---

## Shot 4: Token-Protected MCP Server & Zero-Trust Auth
- **Time:** `1:20 - 1:55` (35 seconds)
- **Visual:** Switch focus to terminal.
- **Action:**
  1. Run `curl -X POST http://localhost:8765/mcp` without an authorization header:
     ```bash
     curl -s -X POST http://localhost:8765/mcp
     ```
     Response: `{"error": "Missing or malformed Authorization header"}` (HTTP 401).
  2. Explain the token lifecycle:
     ```bash
     # Token issued by Voice Gate only upon verified match:
     # Valid for 60 seconds, HMAC-SHA256 signed.
     ```
- **Narration (Spoken):**
  > *"Under the hood, our MCP server runs on port 8765 over Streamable HTTP, adhering to the 2025-11-25 specification. If an attacker tries to curl the MCP server directly, they are rejected with HTTP 401 Unauthorized. Tools can only be called with a short-lived, 60-second HMAC token minted exclusively after successful voice verification."*

---

## Shot 5: Code Walkthrough – Where Biometrics Meet MCP
- **Time:** `1:55 - 2:25` (30 seconds)
- **Visual:** Split screen in editor showing `app/server.py` and `mcp_server/auth.py`.
- **Action:** Highlight:
  1. `app/server.py` line 213: `result = gate.verify_any(audio_data)`. If not granted, returns immediately with no MCP call.
  2. `app/tokens.py`: `mint_token(result.user)`.
  3. `mcp_server/auth.py`: `current_user.set(user)` setting the Python `ContextVar`.
  4. `mcp_server/tools.py`: Tools read caller identity from `current_user.get()`, never from LLM arguments.
- **Narration (Spoken):**
  > *"Here's where the magic happens in code. In server.py, /api/ask verifies the speaker. If verification fails, it short-circuits. If it passes, it mints a short-lived token. The MCP middleware verifies the token and sets a thread-safe ContextVar. This guarantees that tools receive the true physical speaker's identity—making prompt injection impossible."*

---

## Shot 6: Privacy Safeguards, Limitations, & Wrap-up
- **Time:** `2:25 - 2:45` (20 seconds)
- **Visual:** Return to web UI audit log and README summary.
- **Action:** Highlight the live audit log showing timestamps and scores with zero audio saved, and the 1-click "Delete" button for voiceprints.
- **Narration (Spoken):**
  > *"Privacy is baked into the design: raw audio is never saved to disk, voiceprints can be deleted instantly, and the full test suite runs 100% offline. Voice Gate brings zero-trust security to Alexa+ and the MCP ecosystem. Thank you!"*
