# Share the existing hostname certificate policy

Prepared patch, not a live validation or activation record. The authorized host
inspection confirmed the existing ntfy site uses:

```caddyfile
tls {
    issuer acme https://acme-v02.api.letsencrypt.org/directory
}
```

The review template now repeats exactly this block for the same hostname on8443.
It does not edit the ntfy site, add an issuer, change account/storage/environment,
copy/export private keys, introduce certificate files or change legal terms.
There must be one consolidated unchanged policy for this hostname, not one per port.

Caddy2.6.2 builds policies per site, consolidates equal issuer/settings, then rejects
repeated subjects when policies differ. The old review template omitted the
explicit issuer, causing the observed ambiguous-hostname validation failure.
Moreover, a site `bind 127.0.0.1` is copied to the ACME challenge `bind_host` when
otherwise empty, so copying the TLS text alone is insufficient for that variant.
See [Caddy2.6.2 policy construction/consolidation](https://github.com/caddyserver/caddy/blob/v2.6.2/caddyconfig/httpcaddyfile/tlsapp.go).

## Preserve the already-created credential

Do NOT rerun `prepare`, generate another password/hash, edit existing private
fragments, print a hash, or run a credential-recovery workaround. Install the
exact reviewed helper/template patch together. Then, in the private root terminal:

```sh
/opt/sniper-review/ops-venv/bin/python -I /opt/sniper-review/source/scripts/discogs_review_ops.py preserve-existing-tls
```

This fixed action reads only the original private public/loopback fragments and
manifest under `/run/sniper-review-provision`. It checks their ownership/modes,
rejects symlinks/hardlinks, verifies the exact old template, and preserves the
existing bcrypt string byte-for-byte. It never reads the plaintext password,
calls bcrypt, generates credentials or changes the old artifacts. It exclusively
creates `/run/sniper-review-tls-candidate` (0700), with `site-public.caddy` and
`provenance.json` (0600). No bind variant is written. No service or configuration
is activated. Preexisting output or unexpected source content stops the action;
reconcile privately rather than deleting/retrying blindly.

The legacy source template SHA256 is
`988a8f3d83a0dac4cd28dea464fb2d3f7e302779a40c173fc6c0d53c2968de3a`.
The new template SHA256 is
`dfa1df4929d9536459542c6168d29725a3c2db17466eae8a27c4c48530f9d452`.
The manifest records the source base commit and exact file hashes. Verify the
final patch commit separately; a base commit is not the modified artifact identity.
The existing `qa` action continues reading the unchanged initial credential.

## Full candidate comparison, before activation

Use the same Caddy2.6.2 binary, modules, working directory, service identity,
environment/imports and protected certificate storage as the existing service.
Keep private byte-identical baseline and rollback artifacts. Adapt the complete
baseline and complete candidate privately; never print adapted JSON or hashes.
The only candidate addition is the reviewed8443 site, with existing ntfy unchanged.

Require all these comparisons before validation/reload:

- Entire `apps.tls` structurally identical to baseline, including issuer order,
  CA URLs, account/email/EAB settings, challenge settings, renewal/key options,
  certificate loaders/managers and storage. No ignored fields or missing-versus-
  present normalization. Existing hostname appears exactly once across explicit
  automation subject arrays, in the same unchanged policy.
- Top-level storage/admin and other apps, including PKI, unchanged. No new managed
  hostname, issuer or certificate source. Preserve existing environment and UID.
- Existing HTTP servers identified by their listener sets retain their complete
  configurations, including TLS connection policies and ntfy routes/timeouts.
  Generated names such as srv0 are not a reliable matching key.
- Only the expected new8443 HTTP server is added, with the reviewed exact route,
  auth/rate gate and upstream socket. Review its TLS connection policy, frontend
  deadlines and listener protocols. Do not silently accept unrelated changes.

If equality or validation fails, stop. Do not remove/normalize policies until they
compare equal, alter ntfy, add a fallback CA, relax TLS verification or activate
an ambiguous candidate. Reconcile against the exact host evidence. Configuration
identity does not prove certificate byte identity or promise zero normal renewal/
ACME activity; retain existing operational certificate-management behavior.

## Explicit firewall-isolated fixture staging

Selected approach: wildcard8443 listener behind verified closed ingress, using the
already isolated disposable fixture database. This is NOT a loopback-only listener.
The `qa --loopback` option changes only the QA client's destination while retaining
TLS SNI/Host; it does not prevent other clients from reaching the server.

Before first activation, verify the effective ingress rules and default deny for
both IPv4 and IPv6, on every public address/interface, with no8443 allow, broad
allow, NAT/forwarding/container bypass, alternate path or existing exposure.
Inspect actual effective filtering, not just absence from `ufw status`. Cover
TCP and UDP8443: Caddy may enable HTTP/3/QUIC. Keep this filtering unchanged
throughout fixture QA. Bounded external negative reachability checks immediately
before and after activation supplement that rule inspection; they do not alone
prove denial from every source. Record any untested family/address/protocol.

Only then activate the validated credential-preserving candidate through the
already reviewed private admin path. Confirm existing ntfy behavior unchanged,
external denial still holds, and local `qa --loopback` succeeds. Optional rate
exercise remains explicitly disruptive to the short fixed window. Any unexpected
response stops without retries. Roll back the added site if containment or auth
fails; retain private admin isolation and the known-good ntfy-only config.

No production DB relocation/access expansion is included. A separate least-
privilege broker investigation is underway; fixture success does not resolve
that gate. Do not switch to live data or open ingress until the corresponding
isolation/auth/origin/rate/error gates are verified. The later explicitly approved
change is TCP8443 only; leave UDP closed unless separately authorized/reviewed.
No helper installs firewall rules, migrates a DB, reloads Caddy or reads Discogs.

## Verification limits

Offline tests verify exact hash preservation, unchanged original artifacts, no
plaintext read/regeneration, private modes/exclusive creation, rejection of
altered/unsafe inputs and updated fixed-target QA. Caddy is not installed in the
preparation environment and no socket attempt was made. Actual adaptation,
policy equality, validation, filtering, reload and phone QA remain operator gates.

### Offline patch verification (2026-11-08)

- 33 operational-helper tests passed; combined helper/page/server suites:106 passed.
- Full Python run:4,584 passed and41 subtests passed;18 existing socket tests failed
  with `PermissionError: [Errno 1] Operation not permitted`. No bypass attempted.
  This is not a full-suite pass; exact-commit authorized CI remains required.
- Repository lint/format, JavaScript110 tests and TypeScript checks passed.
- Independent code/source review found no blocking defect, conditional on the
  documented live comparison, containment and authentication gates.
