# Challenge diagnostics and access boundary

The private hosted browser reached normal Cart and Purchases pages, but the
complete All/All Purchases route subsequently displayed a repeating verification
challenge. A single user-requested new tab in the same session also displayed
that challenge. One later user-requested restart with the same profile and
settings, followed by one ordinary navigation to the same complete Purchases
page, also displayed the challenge. A verified quiet blank interval beginning
16:04:21 UTC was followed by one normal All/All check at 17:08:53 UTC; security
verification still appeared, and the browser was returned to blank at 17:09:11.
The owner's ordinary local Chrome session loaded complete All/All Purchases with
zero orders at 17:11:34 UTC. This is a comparison, not a transfer of cloud-session
clearance, a resolved cloud transaction fence or an identified failure cause.
None of the hosted tests resolved access. The exact
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
Schema `sniper_browser_request_diagnostics_v2` retains only fixed error categories,
fixed status buckets and coarse 100 ms relative local timing, both globally and
within exactly four resource groups:

| Resource group | Fixed in-memory classification |
| --- | --- |
| `challenge_bootstrap` | HTTPS Discogs/Cloudflare challenge-platform `orchestrate` path, or Cloudflare apex `/turnstile/v0/api.js` |
| `pat` | HTTPS Discogs/Cloudflare challenge-platform `pat` path |
| `diagnostic_dns_probe` | HTTPS subdomain of `challenges.cloudflare.com` or `dnstest.dev`; a namespace candidate, not proof of purpose or a fatal failure |
| `other` | All unmatched, missing, malformed, oversized or unreadable URL metadata |

Only `discogs.com`, `www.discogs.com` and `challenges.cloudflare.com` are accepted
for the first two groups. Credentials, non-HTTPS schemes, non-default ports,
whitespace/backslashes and suffix lookalikes are not qualified. Query/fragment
are removed before classification; a category name appearing only there cannot
qualify a request. The transient URL metadata is at most 8 KiB and never stored
in a counter, retained event, summary, exception log or report. Only the fixed
group name survives. Per-group status/error counts and first/last coarse event
times let a future pass separate PAT 401 or diagnostic-namespace DNS failures
from bootstrap failures without preserving a resource identifier. These are
aggregate event patterns, not unique requests or a guarantee of root cause.

The collector never reads headers, cookies, request/response bodies, input
fields, page content or addresses. Raw error text is mapped in memory to a
fixed category and is never retained or logged. Closed
handlers stay inert even if detachment fails. These are aggregate events, not
unique requests. A URL/payload callback that cancels observation or crosses the
deadline cannot admit another payload read or record; the shared event cap
applies across all four groups, not separately to each one.

Keep this collector off throughout secure card entry. Its narrow design does
not authorize screenshots, DevTools, HAR capture or page logging during entry.
The existing shared-lease privacy pause and cooperative native-inspection freeze
remain required. No automatic navigation, retry, challenge solve, restart,
payment, or transaction-state change is performed by this utility.

## Proposed activation scope — not executed

This change does not add a production import, a browser-control endpoint or a
live attachment. Before activation, review the minimal existing-resident-context
hook and whether it needs one profile-preserving service activation; do not
launch another controller or transfer cookies. Upload/build/restart scope, if
needed, must be explicitly included in that activation review, using only the
already established private host and preserving its stop and audit records.

The eventual separately approved diagnostic is one ordinary same-profile access
check, with this observer attached only outside secure entry and under the
existing shared lease/privacy rules. Stop at the first challenge/restriction;
do not click/solve it, retry, change identity/settings or open checkout. Return
to a verified blank tab afterward. Stop/detach at 60 seconds or 128 total events,
whichever comes first, and retain only the fixed summary. Do not run a raw HAR,
console/network export or another collection alongside it. Both the specific
live test and any necessary code activation remain pending review/authorization;
the existing dormant module is not itself permission to perform either.

No live categorized trace currently exists. The old aggregate-only collector
was not attached, so it cannot retrospectively identify a failed resource,
PAT response, DNS probe or error code. A future category-specific error can
support a narrower hypothesis; it cannot by itself establish why Cloudflare
issued or repeated a challenge, nor justify bypassing it.
