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
no-new-privileges and private shared memory. CI runs two closed resource profiles:
- standard: 2 logical CPUs, 2 GiB RAM, no additional swap, 120-second outer deadline;
- micro: 0.125 logical CPU, 1 GiB RAM, no additional swap, 600-second outer deadline.

Set FINDER_BROWSER_PROFILE=micro when invoking the same script. Unsupported profile names
fail before building. The micro launch/navigation/Python-bridge timeouts are 180/60/120
seconds, versus 30/10/30 seconds for standard. No criteria, freshness, completeness,
database or process-security assertions are relaxed. Only disposable
/tmp is writable. No ports or host browser/session mounts are used. Cleanup removes the
container/image, including on failure. Browser sandbox-disabling variables or flags are not
used. After actual fixture rendering, read-only Linux process evidence must show non-root Firefox
web-content descendants with NoNewPrivs, active seccomp filtering, and additional filters
beyond the measured browser parent and Node/container baseline. Unreadable or ambiguous
evidence fails the test instead of relaxing container security. Transient unavailable
process/thread evidence gets at most three strict reads, 50ms apart; failed identity or
security assertions are not retried. Fork-server command lines are normalized only when a
single canonical space-joined field still exactly identifies the pinned executable,
child marker and known role. Process names alone never qualify a child. This establishes observed
additional syscall filtering, not a runtime Firefox sandbox level or proof of every sandbox
boundary. Static inspection of this pinned Firefox build found its Linux content sandbox
default at level6, with no Playwright launch or preference override disabling it. Runtime
process evidence remains mandatory; defaults alone cannot establish working protection.
Navigation to the privileged about:support page did not succeed in this Playwright test:
both load and commit waits timed out, so that diagnostic method is not used.

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

Output contains browser/package versions, process-filter evidence, elapsed milliseconds, Node
maximum RSS, mandatory measured cgroup CPU/RAM/swap limits, memory peak/current and
headroom, memory-pressure/OOM counters, CPU usage/throttling counters, and pipeline results.
Docker stopped-container OOM/exit state is checked separately, including on test failure. These are one bounded
smoke's measurements, not 24/7 capacity or latency guarantees. No screenshots, real account
state, seller content or browser profiles are uploaded.

## Micro-profile interpretation

The micro profile is a conservative constrained-container experiment for a possible free
AMD VM, not an emulator of Oracle E2.1.Micro or its burst credits. A displayed 1 OCPU in a
console does not establish a sustained full CPU allocation. The test fixes a 0.125 logical
CPU quota and does not model bursting. Build/download resources are not constrained by
that runtime profile.

Measured container headroom excludes the host operating system, Docker daemon, remote
view/login services and long-lived session overhead. The tiny synthetic pages do not
establish real Discogs page memory, network latency, 24/7 stability or performance under
multiple watches. No artificial keepalive/resource load is added. A passing profile is
only evidence for this exact bounded workload; it is not approval to provision a VM or
a claim that the free host is adequate.

## Verification boundaries

Docker is unavailable in the development workspace. A sandbox-enabled local browser launch
was unavailable because required socket operations were prohibited; no weaker launch was
attempted. Source/unit checks are distinct from the dedicated GitHub CI container result.
The synthetic functional result is reported independently of process-security evidence.
A functional pass with missing or failed security evidence still exits nonzero and is not a
runtime acceptance. Only a successful `Isolated cloud browser pipeline` run demonstrates
this packaged smoke.
If that run fails, do not describe the browser as working or change security policy to pass.

Official references:
- https://playwright.dev/docs/docker
- https://playwright.dev/docs/browsers
- https://nodejs.org/download/release/latest-v24.x/
- https://github.com/mozilla-firefox/firefox/blob/main/toolkit/content/aboutSupport.js
- https://github.com/mozilla-firefox/firefox/blob/main/toolkit/modules/Troubleshoot.sys.mjs
