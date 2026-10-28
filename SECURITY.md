# Security

The default demo cannot operate a camera, microphone, workbook application, or Windows lock. Live devices and desktop actions require separate launch options and local action arming. Keep Windows auto-lock enabled independently.

No person identity, face embeddings, account credentials, image uploads, or image persistence are included. Detection accuracy depends on framing and lighting. A successful lock API request does not verify that the operating system completed the lock.

The dashboard accepts requests only on loopback and checks Host, Origin, and an ephemeral session token. This protects the browser boundary; it does not defend against a hostile local process or a compromised operating system. Do not expose the service through a public reverse proxy.

Use synthetic inputs when reporting a bug. Do not attach camera images, private documents, or credentials. See the build guide for observation-only hardware checks before enabling real actions.
