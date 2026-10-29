# Security

The default demo cannot operate a camera, microphone, workbook application, or Windows lock. Live devices and desktop actions require separate launch options and local action arming. Keep Windows auto-lock enabled independently.

No person identification, face embeddings, image uploads, or image persistence are included. Detection accuracy depends on framing and lighting. A successful lock API request does not verify that the operating system completed the lock.

The dashboard accepts requests only on loopback and checks Host, Origin, and an ephemeral session token. This protects the browser boundary; it does not defend against a hostile local process or a compromised operating system. Do not expose the service through a public reverse proxy.

Optional AI chat uses each installer's own API key. The public website never accepts keys. The local app stores cloud keys in the current Windows user's Credential Manager, with no plaintext file, browser storage, environment variable, or command-line fallback. A program running as that same Windows user can access those credentials. Delete a saved key in setup and revoke it at the provider when it is no longer needed.

Only explicitly submitted chat text goes to the selected provider. Camera frames, files, desktop observations, and command history are not attached. Provider replies are plain text and cannot call desktop actions, read files, execute commands, install tools, or change permissions. Endpoints and models are fixed; redirects and environment proxy overrides are disabled. Requests have time and size limits and are never retried automatically. Model output can still be wrong or misleading.

Provider errors return fixed messages without raw response bodies or keys. The OpenAI connector requests `store: false`; this does not promise that all provider retention is disabled. Check your provider's data policy. The budget dropdown is a planning preference, not a billing cap. Set limits in the provider console.

The Windows ZIP includes a pinned Python runtime, dependencies, and a checksum-verified hand model. Their license notices are retained. It is unsigned application code. Verify the release checksum and source before running it. The source installer creates only the project's `.venv`; it never elevates privileges or disables Windows protections. Device use and real actions remain separate choices after installation.

Use synthetic inputs when reporting a bug. Do not attach camera images, private documents, or credentials. See the build guide for observation-only hardware checks before enabling real actions.
