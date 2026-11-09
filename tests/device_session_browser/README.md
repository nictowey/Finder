# Native remembered-device form regression

This test uses real Playwright Chromium and WebKit engines and the actual
`DeviceSessionApp`, with all HTTPS requests intercepted and dispatched to WSGI
through a private subprocess pipe. There is no HTTP listener, real password,
provider request, certificate-warning bypass or production endpoint. The browser
runtime is additionally placed in a non-root, read-only, network-disabled container.
Dependency downloads happen only while building the official pinned test image.

The test clicks HTML form buttons. It does not use `fetch`, construct a fake
Origin header, or replace form submission with JavaScript. The WSGI bridge records
only the Origin actually emitted by the browser. Three flows run in both engines:

1. Legacy `Referrer-Policy: no-referrer` is applied only to the synthetic response.
   A real native POST emits `Origin: null` and is rejected with 403, reproducing the
   browser failure that manual requests with an injected Origin did not reveal.
2. The current application policy is left untouched. A real native POST must emit
   the exact `https://watch.example` origin and receive an actual application
   decision of 303 with Location `/session`. Playwright interception only covers
   the first URL in a redirect chain, so the network-disabled fixture delivers
   that response to the browser as 200 with Location removed and Set-Cookie
   unchanged. Both engines must store the resulting Secure, HttpOnly,
   SameSite=Strict, host-only cookie with the intended expiry; JavaScript cannot
   read it. This verifies browser cookie **storage and properties**, not outgoing
   cookie transport, native 303 handling, real TLS or restart persistence. The
   native request and Origin are never changed. This check fails against the
   unfixed no-referrer application.
3. An actual native form on `https://attacker.example` submits a valid synthetic
   challenge to the bootstrap route and must receive 403. Simulated cached Basic
   identity cannot bypass the strict foreign-Origin check. The legacy flow also
   verifies that null Origin remains denied; the fix must never allow null.

The workflow `Device session native browser forms` builds
`deployment/DeviceSessionBrowser.Dockerfile` and runs this check. It reuses the
repository's locked official Playwright dependency, without changing dependencies
of the existing Firefox pipeline. Results print browser versions, observed origins,
application/fixture-delivery statuses and security-property booleans, never cookie
values or form tokens. Each engine is attempted even when the other fails; the
job fails if either fails or is unavailable. Logs explicitly mark synthetic
redirect normalization, `cookie_store_verified:true`,
`cookie_transport_verified:false`, `native_redirect_verified:false` and
`restart_persistence_verified:false`. There is no engine-specific skip.

Local contract checks can run without launching a browser:

    PYTHONPATH=src:. python -m pytest tests/test_device_session_native_browser_contract.py
    node --check tests/device_session_browser/native_form.mjs

These contract checks are not browser evidence. If the local execution environment
denies browser sockets/processes, do not bypass that restriction or claim a native
browser pass: run the network-isolated workflow on its authorized CI runner.

After the code fix is included, native browser CI must pass for both engines before
claiming this regression covered. Real iPhone reopening and production Caddy/TLS
behavior remain separate user/operator checks. There is no production session
creation or automatic login retry in this test.

## Why cookie transport is outside this fixture

[Official Playwright interception documentation](https://playwright.dev/docs/network#headers-owned-by-the-network-stack)
explains that routing precedes network-stack cookie attachment, so intercepted
headers can omit Cookie even when the browser cookie store contains it. A fulfilled
request never reaches that later transport stage. The previous protected-page
navigation assertion therefore tested an unsupported premise; waits and anchor
navigation do not repair it. It is removed for **both** engines, not silently
skipped for WebKit. No cookie is copied from the jar into a request.

The pinned [v1.64.1 WebKit implementation](https://github.com/microsoft/playwright/blob/v1.64.1/packages/playwright-core/src/server/webkit/wkInterceptableRequest.ts)
constructs intercepted requests from provisional event headers and receives raw
request headers later in response processing. It also rejects fulfilled 3xx
responses, which is why this fixture explicitly normalizes their delivery.

Browser cookie transport/native redirects need a separate non-intercepted HTTP(S)
integration harness. Existing server/IPC tests and real-device observations are
separate evidence, not a claimed result of this fixture. Neither the product's
cookie/Origin guards nor the browser's cookie settings are weakened here.

The fixture still waits for the native POST document's load/commit and polls the
cookie jar read-only for at most three seconds. Missing cookies or wrong security
properties fail in either engine. It never repeats a POST, injects cookies, or
falls back to real network access. Route callback failures are caught and reported
using only method/path/status and property booleans, without headers, bodies or
cookie values. Each engine is attempted even if the other fails.
