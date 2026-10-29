# Architecture and security boundary

```text
Typed command / optional offline voice activation
                    |
                    v
HTTP boundary -> Service (serialized state) <- Local camera count
                    |
                    v
              Presence policy
                    |
                    v
          Named action allowlist
                    |
          +---------+---------+
          |                   |
    Windows lock       Synthetic workbook
```

## State and timing

The policy starts inactive and disarmed. A command `active` starts a new observation session only when inactive. Repeated activation does not extend deadlines. A first person observation establishes a baseline; a subsequent count of zero starts the absence timer. Any count of at least one resets that timer. The timer uses `time.monotonic`, so a wall-clock change does not delay a lock request.

A count of at least two starts a separate crowd timer. Two continuous seconds can trigger one action. Five continuous seconds below two people clears that action's latch. The privacy mode requests a lock; the optional workbook mode opens synthetic data. No identity, employer relationship, or emotion is inferred.

Five seconds without a fresh observation is a camera fault. An armed privacy lock fails closed after that grace period. A failed camera read never becomes a count of zero. A stop command disables evaluation and clears action permissions.

The policy emits at most one lock request for a continuous absence or fault episode. A desktop action error is recorded rather than reported as success. An operating-system lock request can still fail or be denied, and no session notification listener confirms completion. This is why Windows auto-lock remains the primary mechanism.

## Hand control and camera ownership

Hand control is a separate state machine (`jarvis/handcontrol.py`) with modes off, practice, and watching. It never changes the presence policy. Live `watch me` requires a gesture engine, the `--allow-hand-control` launch flag, and, for voice, an explicit dashboard opt-in. Real cursor output requires a second dashboard opt-in and is off at boot. Practice events only update a virtual cursor.

A `CameraBroker` gives the physical camera to one owner at a time: the presence guard or hand control. Sharing one stream between two detectors would need a frame broker with its own failure semantics, so this edition uses exclusive modes. Starting one mode while the other owns the camera is rejected with a clear message and leaves the running mode and its arming unchanged. A clap, Esc, `stop mouse`, the hand stop button, or a cursor failure stops hand control and releases its lease; none of them stops or disarms the presence guard. **Stop and disarm** stops both.

On Windows, an Esc watcher polls `GetAsyncKeyState` only while hand control runs. The first read after activation is discarded so a stale key press cannot stop a new session.

## Setup wizard boundary

`web/setup.html`, `setup.js`, `setup.css`, and `quickstart-catalog.json` provide role and tool dropdowns plus a monthly allowance slider. `jarvis/quickstart.py` mirrors the browser recommendation and parity tests compare plans at supported budget boundaries. Hosted mode fetches static assets beside the page and never calls a loopback address or collects an API key.

Local setup adds write-only key storage, explicit package installation, and a desktop launch route. `jarvis/onboarding.py` saves the six setup choices in `runtime/quickstart.json` using atomic replacement. The installer has one background job; consent is required and choices cannot change during installation. Complete portable packages use their included dependencies. Source installations use the existing reviewed package installer and a project `.venv`. A fixed subprocess command opens the selected desktop feature idle. Its arguments never come from arbitrary user text. Legacy planner endpoints remain validated for existing clients.

`Start-Jarvis.cmd` finds Python 3.11 or newer for source installations. `Start-Jarvis-Windows.cmd` uses the portable package's isolated Python runtime. Both open safe setup through `jarvis.launcher`; neither elevates privileges or enables devices.

## Request boundary

The server binds to IPv4 loopback only. It accepts an exact `127.0.0.1:<port>` Host header and rejects a foreign Origin. Mutations require a fresh per-process session token and JSON content type. Bodies are bounded at 2 KiB for desktop actions, 8 KiB for the legacy planner, and 20,000 bytes for the quick setup's bounded chat and key fields. Parsing rejects duplicate keys, non-finite numbers, excessive nesting, and unknown fields. There are no CORS grants, arbitrary file routes, shell command endpoints, or telemetry.

The browser receives only state and bounded event messages. A restrictive Content Security Policy blocks external scripts, framing, and external connections. UI text uses `textContent`. Requests and session tokens are not logged.

Dashboard snapshots carry a nonsecret instance identifier and an increasing sequence assigned under the service lock. Delayed responses cannot replace newer state. If the service restarts, the old page stops sending actions and asks for a reload to obtain the new session token.

This boundary protects against an unrelated website trying to operate the local dashboard. It does not protect against a malicious process already running as the same operating-system user or a compromised browser extension. Do not expose this development server through a proxy, port forward, or shared network interface.

## Data lifecycle

Camera frames and microphone blocks exist in memory and are discarded. The activity list holds the most recent 40 generic events and resets on restart. Choices and an explicitly enabled synthetic workbook use the ignored `runtime/` directory. API keys use the current user's Windows Credential Manager, with no plaintext file fallback. The public site has no credential fields.

AI requests form a separate text-only path. `provider_client.py` pins provider URLs and models, refuses redirects and proxy overrides, and bounds DNS work, response size, and total request time. `onboarding.py` permits one request at a time and requires explicit consent. No model output is parsed as a command or passed to `Actions`. The selected provider receives only the submitted prompt, without frames, files, or desktop state.

Before a paid attempt, setup re-reads the validated allowance from disk so a second local process cannot keep an old, higher setting. `budget.py` reserves $0.01 inside a SQLite transaction shared by processes. It stores only UTC month, reserved amount and attempt count; failures are not refunded. A $0 allowance, exhausted allowance, corrupt ledger, or expired pricing check prevents a paid request. This bounds the local reserved allowance, rather than guaranteeing an invoice or controlling other applications.

The source simulator uses only the standard library. The portable edition includes pinned third-party device packages and a verified hand model, with retained license notices; no Vosk voice model is included. The HOG people detector has substantial false-positive and false-negative risk.

## Verification scope

Automated tests exercise the policy with explicit timestamps, desktop-action isolation in demo mode, workbook structure, and actual local HTTP requests. They do not establish webcam accuracy, live voice recognition accuracy, physical Windows session locking, or Excel availability.
