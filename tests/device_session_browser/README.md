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
   unchanged. A native same-origin anchor click from the committed POST document
   to intercepted `/session` must authenticate using the retained cookie. This
   deliberately preserves the document initiator instead of using automation
   `page.goto`, which can differ in WebKit's site-for-cookies classification. The cookie is Secure, HttpOnly,
   SameSite=Strict and host-only; JavaScript cannot read it. This verifies only
   synthetic-response cookie storage/use, not native 303 handling, real TLS or
   persistence across browser restarts. The native request and Origin are never
   changed. This check fails against the unfixed no-referrer application.
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
redirect normalization and do not claim native redirect/restart verification.

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

The fixture waits for the native POST document's load/commit before inspecting
cookies, then polls the browser cookie jar read-only for at most three seconds
before native same-origin account navigation. It never injects cookies or repeats
any POST. Diagnostics report only route method/path/status and cookie-presence/
security booleans. Cookie presence from `request.allHeaders()` and `headerValue`
is observed separately to diagnose header-exposure discrepancies; the fixture
never substitutes a cookie from the browser cookie jar into a request. If WebKit cannot commit a cookie from a synthetic fulfilled response,
the test fails explicitly at that boundary rather than claiming retention or
attempting a network fallback. Route callback failures are caught and reported
without headers, bodies or cookie values so one engine cannot hide the other's result.
