# Prepared app-only authentication for aggregate polling

This is an opt-in implementation, not activation or a claim of complete API Terms
compliance. No real credentials were read, generated, configured, or tested.
Existing anonymous polling is unchanged unless the new option is explicitly supplied.

## Evidence and scope

The [official authentication documentation](https://www.discogs.com/developers/#page:authentication)
describes HTTPS `Authorization: Discogs key=..., secret=...`. App consumer credentials
identify the application without authenticating as a user. Private collections,
orders, and private inventory require user authentication, which this change does
not implement. The public marketplace stats endpoint is the only allowed route in
the new reader. Its acceptance of an actual approved app identity remains untested.

The [official authentication announcement](https://www.discogs.com/forum/thread/1189875)
and developer-page banner announce mandatory authentication from December 1, 2026.
Read the current official documentation before activating; authentication alone
does not establish permission for every use of marketplace data.

## Configuration contract, pending separate approval

The aggregate worker accepts `--discogs-app-file /absolute/path/to/app.json` only
with `--poll`. The operator must separately authorize credential creation and
persistent configuration, then provision the file through an approved secret flow.
Do not paste credentials into chat, shell arguments, environment variables, logs,
source control, or query URLs. This feature does not create or repair secret files.

The file is a JSON object with exactly two string fields: `consumer_key` and
`consumer_secret`. The parser accepts bounded ASCII alphanumeric, underscore and
hyphen values, and rejects unsupported characters rather than trying to escape
arbitrary header syntax. Its maximum file size is 1024 bytes. It must be a regular,
single-link file owned by the worker's effective deployment user, mode 0400 or 0600.
Symlinks, group/other permissions, FIFOs, missing files, and invalid content fail
closed before opening the database or making a request. A systemd credential file
may be used if it meets those checks; there is no automatic environment discovery.
The worker loads credentials once at startup, so approved rotation needs a restart.

Credential values appear only in the HTTPS Authorization header to the fixed
`api.discogs.com` public stats route. Redirects, ambient proxy configuration and
ambient TLS certificate overrides are disabled for this reader. No user token,
OAuth flow, alternate endpoint, or anonymous retry is available. Keep standard
HTTP certificate verification enabled. Application code emits only fixed errors
and redacted credential representations; do not enable external HTTP header or
process-memory tracing in deployment.

The existing three-second shared request spacing, configured per-release interval,
429 cooldown and durable 401/403 stop remain unchanged. Supplying credentials does
not enable polling, delivery, purchasing or a stopped durable alert control.

## Remaining compliance gates, not changed here

The [API Terms of Use](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
classify marketplace inventory and prices as Restricted Data, constrain sharing
with third parties and commercial use, limit stale display to six hours, and require
necessary-only storage plus attribution and an affiliation disclaimer.

- Current ntfy messages transmit availability/price to a third-party notification
  provider. Do not treat a private topic or authentication as resolving that issue.
  Review a first-party-only owner view, or obtain Discogs permission for the intended
  delivery arrangement. Even a data-free triggered notification can reveal an
  availability signal through its timing and needs review before being called safe.
- Current price/count samples and terminal outbox prices have no bounded deletion
  policy. Alert delivery freshness is bounded, but storage retention needs a
  separate implementation and migration plan. Suppress stale data in every owner
  display as well as delivery; retaining episode/dedup metadata need not retain prices.
- Existing alert messages attribute Discogs and link to the release marketplace.
  Audit all application views for the required prominent affiliation disclaimer,
  attribution and source links. A documentation notice alone is insufficient.
- Keep this private/noncommercial; no resale of API access/data, other-marketplace
  targeting, undocumented endpoints, scraping, or rate-limit circumvention.

No notification payload, database retention, deployment unit or live runtime was
changed by the authentication preparation. These gates require resolution before
a broad compliance claim or activation decision.
