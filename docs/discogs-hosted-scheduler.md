# Hosted exact-release scheduler: disabled integration

`HostedReleaseScheduler` is an opt-in, single-read tick around the fixed
`observe_release` Unix-socket action and `ReleaseSnapshots.ingest`. The
`finder-monitor` entry point wires the private socket reader to a durable worker
loop, but is **not installed or running as a VPS service**. Without `--run` it
only prints health. Even with `--run` it exits if the schedule is paused, the
kill switch is active, or the reader is unavailable; it never resumes a schedule.
The web app does not start a worker or call `tick`. A new rules database
starts with this schedule paused, the cloud kill switch stopped, and no targets.
The authenticated cloud `/api/snapshot` and private local rule editor report its
paused state, next request, per-target due/failure state and last error. `set_paused`
uses an expected revision for explicit
state changes. The existing global kill switch and saved target enabled/owned flags
also stop reads. No new public control or navigation endpoint was added.

One `tick` claims an enabled, unowned saved exact-release target under SQLite
`BEGIN IMMEDIATE`. It persists a 15-minute global request slot, per-target due time
and a 120-second lease before touching the reader. A restarted or second worker
cannot immediately repeat a read. The socket client sends only the ID and fixed
action; the browser code constructs the canonical URL. The client rejects an
insecure socket, malformed response, wrong release, incomplete snapshot or any
purchase-enabled field. Reader access restrictions pause the schedule; other
failures back off up to 24 hours. No error is interpreted as zero listings.

The snapshot importer checks saved rules, cloud control, scheduler revision and
lease token in one write transaction. A pause or rules/control/baseline change
while the page is loading discards the observation. Only a complete rendered
snapshot can advance the baseline. An offline `assemble_release_pages` validator
combines already captured pages only when release identity, page order, displayed
ranges, total, unique listing IDs and capture times agree, with a 250-row cap.
Missing or changed pages fail closed. The fixed browser navigator now follows
only matching `rel=next` controls displayed in both pagination bars, with a
same-release `?page=N` URL, a 100-page/250-offer cap and a 90-second budget.
It checks HTTP status, challenge titles, release identity, ranges, total and
duplicate IDs on every page. A missing next control or page drift leaves the
snapshot incomplete; it never means zero offers. This traversal is tested with
offline browser fixtures only. The existing 25-of-47 Rocky observation was
one user-opened page and is **not** a live proof of multi-page traversal or an
atomic marketplace snapshot. The existing diff ledger tracks
new, disappeared, changed and reappeared listing IDs without treating any change
as sale or verified pressing. This path never qualifies a delivered total or
enables purchase.

An optional `CandidatePipeline` captures new, changed and reappeared listing
events while the same revisioned snapshot lease is held. It retains one row per
exact release/listing pair. A changed price or saved-rule/control/schedule
revision creates a new pending review generation; an unchanged snapshot or
restart does not duplicate a candidate. Two workers cannot claim the same row.
Interrupted or uncertain reads stay stopped until a distinct fresh event or
revision explicitly reopens them. The optional reviewer accepts synthetic
observations for the integrated dry run. It can also consume a
`VerifiedPublicDetail` produced by one fixed official public known-listing API
read. The adapter validates the requested listing and release identities,
rejects redirects and stale reads, and records reported shipping separately.
A public detail never supplies a destination-confirmed shipping, tax, fee or
physical pressing claim, so it stays blocked for all-in candidate eligibility.
This is a bounded detail integration, not permission for recurring collection.
The owner later approved an exact individual item-page read through the existing
private hosted browser. An optional `HostedListingCandidateReader` sends only
`observe_listing` with the saved exact release and discovered listing IDs. The
browser action accepts only canonical item URLs, a fixed DOM collector, and
bounded read-only fields. Reported page shipping is kept separate from a payable
destination total; unknown currency, tax, fees, quantity provenance and physical
pressing evidence still block eligibility. An already-in-cart control also blocks
the candidate. Neither reader is wired into a live
candidate worker or a recurring schedule.

The hosted bridge's `/bridge/access-stopped` file is a **local durable safety
latch**, created in the stopped state on startup after the earlier access
restriction. Its presence blocks ordinary socket reads before navigation. It
does not prove Discogs is currently showing a challenge. Nic later completed
Cloudflare verification himself, and a scoped current-cart read succeeded, so
the last observed page was not an ongoing cart challenge. Existing recovery
controls permit only separately acknowledged, single-use reads of the already
open Rocky release or the exact DS2 cart; they do not resume ordinary reads or
clear this latch. There is no implemented general operator reset. Returning to
ordinary unattended reads would require a separately reviewed recovery design
and site-access basis; installing the new worker does not change either gate.
The scoped operator-recovery bridge was deployed and completed one read of each
of releases `28720177`, `5520534` and `13053315` on October 25, 2026. Each
returned a complete single-page observation, as recorded in
[discogs-monitor-recovery.md](discogs-monitor-recovery.md). The historical
marker remains, and neither the recurring worker nor the purchase path was
activated. The site-access basis and repeated live-monitoring checks remain
unverified.

The reviewer applies exact-release and observation freshness, release quantity,
seller-comment damage, media/sleeve grade, seller reputation and all-in
destination rules. It rechecks target, owned marker, cloud control, schedule
and baseline revisions before storing `review_only` or blocked state. An
explicit rehearsal method can reserve the existing durable one-copy claim
for a fresh reviewed candidate, with state checks before and after. A racing
change may leave a conservative reserved slot but cannot permit a cart or
order operation. Its read-only bundle supplies a revisioned snapshot,
reviewed listing and matching claim to the existing JavaScript no-order cart
rehearsal. The integrated test uses a disposable fixture cart and a
*simulated* observed-cart claim; its visible total remains an unverified
final quote.

The private server does **not** construct this pipeline, supply a detail
reader, expose its bundle as an endpoint, or start a candidate worker. Live
targets remain empty and polling stays paused. No browser navigation or cart
mutation is triggered by this dry-run code.

For a web-only private test release, verify the exact source path and wait up
to 15 seconds for a successful loopback `/api/snapshot` after restarting the
web unit. `systemctl is-active` can precede the listener. Roll back the web
unit if the bounded health check or purchase-disabled assertions fail; do not
restart the hosted browser as part of that web release.

A single read-only official API rehearsal from the local development checkout
of user-identified DS2 listing `3526572746` on release `7609839` returned the
matching release, Mint media,
Near Mint sleeve, a 100% seller rating with 10,020 feedback entries, and a
USD 199.99 item price. Normalized comments were marked unsupported, and
shipping was unavailable or blocked. The release catalog reported two vinyl
records, but completeness could not be confirmed from that response. There was
no destination-confirmed delivered total. No listing or target was saved.
Later bounded hosted item-detail reads did not yield a verified observation:
one returned `listing_not_ready`, and the next diagnostic returned
`access_restricted` before any DOM structure could be captured. The bridge
stops further reads on an access restriction. Its safe code does not identify
whether the cause was HTTP denial, a login redirect, or a challenge. The current
private Firefox tab was blank when inspected without navigation, so it did not
resolve that ambiguity. No cart action or order followed. Hosted item-level
qualification and a final delivered quote remain unverified.

Tests use disposable SQLite and synthetic pages. One integration test runs the
real socket client through a private temporary Unix socket into the scheduler
and durable baseline; it has no Discogs session or network. Other tests exercise
restart cadence, nonoverlap, pause/revision races, incomplete pages and failure
backoff. Runtime activation is still blocked by Discogs' site terms for automated
listing collection. The owner approved a fixed-read private browser handoff;
the dedicated group-restricted Unix socket can connect the host-side reader
without exposing the browser profile. Connection does not start a worker or
authorize recurring collection.

## Auto-buyer readiness at the current private test release

The rule editor supports a saved release ID/link, an all-in per-item ceiling,
separate media and sleeve grades, seller percentage and feedback thresholds,
and a shipping destination. One-copy claims and contextual rejection of
disclosed missing or damaged playable media exist in the isolated dry run.
There are zero saved hosted targets. The hosted service starts no scheduler or
candidate worker, and purchasing stays disabled.

This is not an operational auto-buyer. The last bounded hosted item read returned
`access_restricted`; the browser bridge stopped. A later ordinary home-page
visit in the same private viewer displayed Discogs/Cloudflare security
verification. A later read-only observation of that viewer showed the ordinary
home page with signed-in account controls. This does not clear the item-route
restriction or the browser bridge's stop latch. Do not restart the browser,
repeat the item request, solve a challenge, or use another route to evade the
restriction. The owner supplied release `4204639` for a new detection test, but
no live read was attempted while that stop remains; its offer count is unknown.

Even after ordinary item access is restored, the hosted detail reader does not
attach trusted exact-release media quantity or physical pressing evidence, and
the candidate reviewer cannot verify destination shipping, tax, mandatory fees,
currency, or a final payable total. The observed cart rehearsal is read-only and
explicitly treats its displayed amount as an unverified quote. There is no
hosted cart-write adapter, verified final-submit control, purchase authorization
state, payment handoff, or live order reconciliation. An isolated synthetic
order-safety module now models an exact-target, expiring, revocable simulation
grant, final-quote binding, a durable one-copy submission fence, crash recovery,
and read-only receipt/absence reconciliation. A fixture-only coordinator now
binds the saved candidate review and simulated observed-cart claim to that
ledger and rejects stale, changed, or unknown evidence before its optional
in-memory synthetic executor. It has no provider adapter and the private service
does not initialize its ledger or issue grants. The current cart
claim file protects one cart attempt per release in a dry run, not a completed
order. These gaps cannot be validated by synthetic fixtures alone.

Before any record purchase, the owner still needs to enter a concrete target
and maximum delivered price, approve the eventual verified checkout/payment
scope, and have a supported normal-access path that yields an actual final quote.
No saved setting or passing test currently enables purchase or recurring reads.

## Seller disclosures

Playable-media completeness and media damage are separate assessments.
Explicit missing records and definite media/playback damage block review even
when a listed grade meets the target. Defects currently recognize contextual
English warp/dish, crack/break/chip, skip/jump/stuck groove, media
scratch/scuff/hairline/gouge/surface marks, and clearly stated playback
noise/distortion. Clear
negations and packaging-only damage do not assert playable-media damage.
Ambiguous, contradictory, unsupported or absent seller descriptions fail closed.
These pattern checks cannot verify the physical copy and may miss novel wording.
