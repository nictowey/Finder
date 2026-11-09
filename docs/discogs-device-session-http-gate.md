# Isolated remembered-device HTTP and IPC gate

Run from the repository root as an existing unprivileged user after installing the
project with its `dev` dependencies:

```sh
python scripts/discogs_device_session_ipc_selftest.py
python scripts/discogs_device_session_ipc_selftest.py --ip-origin
```

This runs the existing watch HTTP/Unix IPC gate first, then exercises a second
HTTP listener with remembered devices enabled only for a temporary synthetic
fixture. Both listeners and the broker use private Unix sockets. There is no TCP
listener, production path, real credential, provider request, notifier or worker.
The fixture monitor remains disabled even when synthetic watches are enabled.

Run both commands in CI/operator verification. The default uses a DNS-shaped Host;
`--ip-origin` uses a well-known public IPv4 syntax fixture with the explicit IP
opt-in. Both use Unix sockets only: neither resolves DNS nor sends TCP requests
to the fixture address. HTTP proxy environment settings are disabled. Both verify
wrong Host rejection and exact HTTPS Origin matching (HTTP, explicit `:443`, and
a different host are rejected). The unit gate is parameterized for both variants
and fails immediately if any socket creation or DNS resolution is attempted.

Coverage includes:

- Trusted-proxy identity assertion on bootstrap GET and CSRF POST; missing/wrong
  identity, wrong origin and bootstrap replay rejection.
- Secure, HttpOnly, SameSite=Strict, host-only, root-path cookies with a fixed
  30-day Max-Age and matching Expires, and no bearer token stored in the database.
- Two remembered devices, authenticated watch state/add/edit/pause through the
  real broker, device-bound watch CSRF, replay rejection and unchanged prior rules.
- Device-bound logout and revoke-all, cookie clearing, invalidated-device denial
  and successful recovery through a fresh bootstrap.
- Shutdown/recreation of the HTTP listener, app and SessionStore, retaining valid
  sessions and revocations but rejecting pre-restart CSRF challenges.
- Exact expiry with an injected session clock; no sliding extension on reads.
- Unchanged global monitor control, empty notification outbox and zero new-watch
  notification counters.

The preauth rate limiter uses a separate synthetic monotonic clock in this gate
so requests do not need real-minute sleeps. Its unchanged production limits are
covered by the existing unit suite, not tested here.

## Limits and separate required checks

The fixture **asserts** identity as the trusted Caddy side; it does not validate a
Basic password. It manually relays the Secure cookie over a trusted synthetic
non-TLS Unix transport, rather than relying on a browser cookie jar. Consequently
this is **not** proof of Caddy Basic validation, correct production proxy/header
configuration, HTTPS/TLS, actual browser cookie handling, iOS home-screen
persistence, or distinct production service-user filesystem permissions. Those
remain separate gates. This script does not authorize deployment or live tests.

The socket-free unit test `tests/test_discogs_device_session_ipc_gate.py` runs the
same session assertions using WSGI transport and a direct synthetic backend. It
checks the gate's assertion logic but is **not a real HTTP or IPC result**. Where
local socket creation is denied, run that unit test and lint only; do not bypass
the restriction or mark the real-IPC gate passed. Execute the script in authorized
CI or an operator environment that allows isolated local Unix sockets.
