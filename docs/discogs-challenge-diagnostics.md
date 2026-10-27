# Challenge diagnostics and access boundary

The private hosted browser reached normal Cart and Purchases pages, but the
complete All/All Purchases route subsequently displayed a repeating verification
challenge. A single user-requested new tab in the same session also displayed
that challenge. One later user-requested restart with the same profile and
settings, followed by one ordinary navigation to the same complete Purchases
page, also displayed the challenge. Neither test resolved access. The exact
cause remains unknown; these observations do not establish successful clearance.

Read-only checks on October 27, 2026 found Firefox 157.0 / Playwright 1.64.1, a healthy
2 GiB container with no current OOM/restart, synchronized clock, no JavaScript
disable override, no explicit proxy/UA override, and only bundled active addons.
The recent launcher changes added controlled blank startup and disabled session
restoration; they did not change JavaScript, cookie or proxy preferences. This
comparison does not prove that every effective site/storage setting is compatible.

Cloudflare's [supported-browser guidance](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/)
explicitly says automated browsers and automation frameworks, including
Playwright, are unsupported for solving production challenges. Playwright also
documents that its Firefox build [requires browser patches](https://playwright.dev/docs/browsers#firefox).
This is a verified operational limitation, not proof of the exact loop cause.
The [challenge troubleshooting guide](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/)
lists several possible causes. A PAT 401 or certain diagnostic DNS failures can
be expected; a status/error count alone must not be called a fatal root cause.

Stop dependent checkout when normal access is unavailable. Resolution must use
legitimate site access and applicable user verification. Do not automate a
bypass, change identity/IP/profile to evade a restriction, repeat tabs/refreshes,
disable protections, or fabricate clearance. No contact with Discogs is part of
this work. Reconciliation remains incomplete until the complete normal order
history is actually observed; zero visible non-archived orders is insufficient.

## Dormant collector — not deployed or attached

`scripts/discogs_browser_request_diagnostics.mjs` is a standalone opt-in utility.
No hosted-browser launcher imports it, and the hosted-browser image recipe does
not package it. It is off by default;
even its context is untouched unless `enabled: true` is explicitly supplied.
Future attachment needs a separately reviewed permitted diagnostic pass. The
module itself provides no permission or mechanism to request/retry a page.

When enabled, it counts existing `requestfailed` and `response` events for at
most 60 seconds or 128 events, then stops, attempts to detach, and emits at most
one fixed JSON summary. Reentrant callbacks are ignored before metadata reads.
It retains only fixed error categories, fixed status buckets, and coarse 100 ms
relative local timing. It never reads URLs, headers, cookies, request/response
bodies, input fields, page content, addresses, or session tokens. Raw error text
is mapped in memory to a fixed category and is never retained or logged. Closed
handlers stay inert even if detachment fails. These are aggregate events, not
unique requests, and do not associate failures with a merchant/provider origin.

Keep this collector off throughout secure card entry. Its narrow design does
not authorize screenshots, DevTools, HAR capture or page logging during entry.
The existing shared-lease privacy pause and cooperative native-inspection freeze
remain required. No automatic navigation, retry, challenge solve, restart,
payment, or transaction-state change is performed by this utility.
