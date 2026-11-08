# Private review credential and QA helpers

Prepared offline, not executed against the server. These helpers never install
configuration, start/reload a service, change firewall rules, move/init a database,
or submit a real listing. Their only network target is the explicitly approved
`https://vps-a0c5b746.vps.ovh.us:8443`, and only paths `/listing-review` and the
negative test `/_proxy_gate`. They never contact Discogs.

## Prerequisites and provenance

The helper is `scripts/discogs_review_ops.py`. It verifies the SHA256 of the source
Caddy template against the unchanged template in commit
`55a23a18406e42dcdd5551814b718cab53d51903`. Each preparation stores that provenance
and its own source SHA256 in a private manifest. Verify the final helper commit
and file hashes before transferring/running it; do not paste it into a generic
root interpreter or accept new template content without review.

Install the reviewed checkout root-owned at `/opt/sniper-review/source` and use
an isolated operator venv `/opt/sniper-review/ops-venv`, not the running page's
venv. Install the project's `review-ops` extra from the reviewed local checkout
using official package sources. This adds bcrypt; httpx is already a project
dependency. Offline verification used bcrypt 5.0.0 and httpx 0.28.1. The dev extra
also includes bcrypt so CI does not silently skip its tests. The command assumes
Ubuntu's verified `/usr/bin/curl` with `%{header_json}` support (curl >=7.83).

Run only in an authorized, private root terminal with tracing/recording off,
using Python `-I`. No secret may be passed as an argument, environment variable,
chat message, tool output, or copied into source. No credential is printed.
Root is required by CLI; test fixtures use temporary directories and no sockets.

## Prepare initial random credential

```sh
/opt/sniper-review/ops-venv/bin/python -I /opt/sniper-review/source/scripts/discogs_review_ops.py prepare
```

This generates 256 bits using `secrets.token_urlsafe(32)`, bcrypts it at cost 12,
and exclusively creates `/run/sniper-review-provision` (0700) containing:

- `password`: plaintext page-only credential, owner-only 0600, for private QA and
  an explicitly authorized secure handoff only. It is not notification auth.
- `site-loopback.caddy`: rendered literal bcrypt hash, `bind 127.0.0.1`, 0600.
- `site-public.caddy`: identical site without the temporary loopback binding, 0600.
- `provenance.json`: nonsecret source identity, 0600.

No file is activated. A preexisting directory/file stops preparation; no overwrite,
rotation, cleanup or blind retry occurs. Reconcile partial output privately after
any failure. `/run` is volatile and may be cleared on reboot; do not depend on it
as a durable password manager. Caddy needs only the hash, never the plaintext.
Do not change these files to world-readable. To activate, the authorized operator
must install the selected fragment into protected Caddy configuration readable
only by root/Caddy, validate privately, and perform the reviewed reload process.
Never print `caddy adapt` output or password hashes in shared logs/transcripts.

Initially install ONLY `site-loopback.caddy`. Verify the actual listener is
127.0.0.1:8443 and not public/wildcard or IPv6. Keep UFW 8443 closed. The existing
:443 ntfy site, certificate storage and credentials stay unchanged. The separate
admin-isolation runbook must be completed first on this BPF-less host.

## Loopback-only authenticated QA

```sh
/opt/sniper-review/ops-venv/bin/python -I /opt/sniper-review/source/scripts/discogs_review_ops.py qa --loopback
```

This internally reads the 0600 credential. Curl receives a private stdin config,
not secret argv or a file. Its fixed `--resolve` equivalent sends TCP to127.0.0.1
while keeping the approved hostname for TLS SNI, certificate verification and
Host. No hosts-file change, insecure TLS mode, redirects, netrc, environment proxy,
curlrc, inherited certificate override or TLS-key-log environment is used. The
non-loopback mode uses normal DNS for the same fixed origin and the same sanitized
curl transport. All request results are statuses/booleans and fixed error labels;
headers/bodies, authentication and exception payloads are never emitted.

QA checks missing/wrong credentials, another username, forged/repeated/underscore
identity headers, correct owner, no-store on successful GETs, wrong Host without
credentials, inaccessible private gate, wrong/missing Origin and a 2049-byte body.
It never extracts or submits a CSRF token or a valid listing form. A GET may mint
an unused token; it cannot initiate a provider read. A disposable correctly tagged
fixture database may be used for initial frontend QA without altering live guard
state. Passing that test does NOT prove the eventual real DB permissions/path.

Run this separately when temporarily exhausting the rate window is acceptable:

```sh
/opt/sniper-review/ops-venv/bin/python -I /opt/sniper-review/source/scripts/discogs_review_ops.py qa --loopback --exercise-rate-gate
```

The rate exercise sends at most 31 additional missing-auth probes, stops at 429,
then verifies that the owner is also blocked. It can temporarily deny legitimate
page requests. There is no automatic wait/retry/reset. Allow a new fixed window
before a separate operator-requested QA run; a preexisting busy window can cause
QA to stop early. Any unexpected response, redirect, challenge or transport error
stops the sequence without automatic retry or alternate destination. Inspect
privately and reconcile before another explicit run. This helper never checks
credentials against ntfy, changes notification settings or performs purchases.

After all local isolation/auth/TLS gates pass, the operator may replace ONLY the
loopback fragment with its matching public fragment and apply the separately
approved TCP8443 firewall change. Confirm real public reachability and run `qa`
without `--loopback` from an authorized external context. Normal DNS QA on the
VPS alone does not prove an external client can traverse the firewall. Do not
claim phone validation from an operator-host test. No helper performs this exposure.

## User-chosen memorable passphrase after initial QA

The random credential can remain an operator-only test credential, avoiding a
copy/paste burden for the user. Offer a trusted private terminal/secure computer
handoff where the user can type a memorable page-only passphrase directly, with
masking and no assistant observation of the value. Do not add a web reset endpoint
or ask for it in chat. Four or more unrelated words can be easier to type; require
20–72 printable ASCII characters and do not reuse an existing account password.

In that private interactive terminal, run:

```sh
/opt/sniper-review/ops-venv/bin/python -I /opt/sniper-review/source/scripts/discogs_review_ops.py prepare-rotation
```

It checks a controlling terminal, refuses getpass's echoing fallback, prompts
twice, and prepares the same artifacts under `/run/sniper-review-rotation`.
It does not change the live credential. The operator validates and activates the
matching protected fragment through the same reviewed process, then runs
`qa-rotation` (optionally `--loopback` only if the listener is still loopback).
The user already knows the new passphrase; no random password needs delivery.

If that secure handoff is unavailable, the parent must arrange an approved secure
credential-delivery mechanism rather than expose the random password in chat.
Do not claim a vault has saved it without confirmation. Keep the previous private
artifacts until activation and QA are confirmed; then use an authorized cleanup
of the exact temporary secret files. This helper intentionally has no deletion,
service-management or arbitrary-path command. Browser Basic auth may cache the
old credential; rotation revokes future requests but there is no reliable page
Logout button. Explain that behavior to the user.

## Verification limits

Offline mock QA is not real Caddy authentication. Required live gates include
Caddy2.6 adaptation, TLS/SNI, literal bcrypt parsing, spoofing/header normalization,
pre-auth rate-gate fail-closed behavior, private admin/socket ACLs, loopback-only
initial bind, no leaked secrets/logs, eventual shared DB isolation and phone UX.
No live credential, service, network, database or firewall action was performed
when preparing this package. One known-listing provider test requires a separate
explicit gate later; it is deliberately absent here.

### Offline verification record (2026-11-08)

- Helper suite: 24 passed. Combined helper/page/server suites: 97 passed.
- Full Python run: 4,575 passed and 41 subtests passed; 18 existing socket tests
  failed with `PermissionError: [Errno 1] Operation not permitted`. No sandbox
  bypass attempted. This is not a full-suite pass; authorized exact-commit CI
  still needs to verify those existing tests.
- Repository Ruff lint/format: passed. JavaScript: 110 passed. Typecheck: passed.
- Independent helper/admin review found no blocking code defect. No live Caddy,
  curl, credential, firewall, service or DB action was performed by this package.
