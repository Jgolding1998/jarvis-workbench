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

## Request boundary

The server binds to IPv4 loopback only. It accepts an exact `127.0.0.1:<port>` Host header and rejects a foreign Origin. Mutations require a fresh per-process session token, a JSON content type, and a body no larger than 2 KiB. There are no CORS grants, arbitrary file routes, command execution endpoints, credential endpoints, telemetry, or cloud services.

The browser receives only state and bounded event messages. A restrictive Content Security Policy blocks external scripts, framing, and external connections. UI text uses `textContent`. Requests and session tokens are not logged.

This boundary protects against an unrelated website trying to operate the local dashboard. It does not protect against a malicious process already running as the same operating-system user or a compromised browser extension. Do not expose this development server through a proxy, port forward, or shared network interface.

## Data lifecycle

Camera frames and microphone blocks exist in process memory and are discarded. No screenshots, audio files, face embeddings, names, API keys, or user profiles are saved. The activity list holds the most recent 40 generic events in memory and resets on restart. Only an explicitly enabled workbook action writes a file, under the ignored `runtime/` directory.

Optional dependencies and downloaded voice models must be assessed independently. They are not included in the dependency-free demo or its security claims. The included HOG model is an estimate with substantial false-positive and false-negative risk.

## Verification scope

Automated tests exercise the policy with explicit timestamps, desktop-action isolation in demo mode, workbook structure, and actual local HTTP requests. They do not establish webcam accuracy, live voice recognition accuracy, physical Windows session locking, or Excel availability.
