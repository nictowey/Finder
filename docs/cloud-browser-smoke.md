# Isolated real-browser pipeline smoke

This is a test-only cloud execution proof, not a production browser deployment or Discogs
access adapter. It has no retained session, credentials, scheduler, cart or order operation.
The user's final goal remains independently hosted monitoring and eventually authorized
checkout with their computer off. This smoke does not establish that goal, 24/7 uptime,
provider permission, free hosting capacity or checkout readiness.

## What executes

`bash scripts/check_cloud_browser.sh` builds a separate image from the official Node
24.22.1 Debian image. An isolated npm lockfile pins Playwright 1.64.1 and its matching official
Firefox revision 1555 (157.0). The Playwright CLI installs its corresponding browser and OS
libraries during the build. Production dependencies and `deployment/Dockerfile` are unchanged.
The Dockerfile-specific context allowlist excludes credentials, local databases, git data
and node_modules.

Runtime is non-root UID 10001, read-only root filesystem, network:none, no added capabilities,
no-new-privileges, private shared memory, two CPUs and a 2 GiB memory ceiling. Only disposable
/tmp is writable. No ports or host browser/session mounts are used. Cleanup removes the
container/image, including on failure. Browser sandbox-disabling variables or flags are not
used. The browser's own about:support DOM must report effective content sandbox level >= 4 and
seccomp BPF support; absent evidence or launch failure fails the test instead of relaxing
container security.

Four synthetic marketplace documents are fulfilled in memory at the exact URL required by
the unchanged production collector. This is not a request to Discogs: every unmatched browser
request is aborted, and the container cannot reach external networks. The smoke additionally
checks that an unmatched fetch fails. The real browser renders HTML and runs
`collectReleaseMarketplaceDom`; collected snapshots feed the actual Python SQLite snapshot
diff and rules/completeness code, not a replacement implementation.

Assertions cover baseline suppression, same-count listing substitution, partial snapshot
preserving the last successful baseline, database reopen/replay deduplication, and missing
playable media overriding VG+ grades. Catalog counts in these fixtures are explicitly
synthetic. Ordinary descriptions do not need a complete-set claim. Purchases remain false.

Output contains browser/package versions, sandbox evidence, elapsed milliseconds, Node
maximum RSS, cgroup memory peak when available and pipeline results. These are one bounded
smoke's measurements, not 24/7 capacity or latency guarantees. No screenshots, real account
state, seller content or browser profiles are uploaded.

## Verification boundaries

Docker is unavailable in the development workspace. A sandbox-enabled local browser launch
was unavailable because required socket operations were prohibited; no weaker launch was
attempted. Source/unit checks are distinct from the dedicated GitHub CI container result.
Only a successful `Isolated cloud browser pipeline` run demonstrates this packaged smoke.
If that run fails, do not describe the browser as working or change security policy to pass.

Official references:
- https://playwright.dev/docs/docker
- https://playwright.dev/docs/browsers
- https://nodejs.org/download/release/latest-v24.x/
- https://github.com/mozilla-firefox/firefox/blob/main/toolkit/content/aboutSupport.js
- https://github.com/mozilla-firefox/firefox/blob/main/toolkit/modules/Troubleshoot.sys.mjs
