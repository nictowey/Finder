# Hosted exact-release scheduler: disabled integration

`HostedReleaseScheduler` is an opt-in, single-read tick around the fixed
`observe_release` Unix-socket action and `ReleaseSnapshots.ingest`. The app does not
start a hosted worker, mount a browser socket, or call `tick`. A new rules database
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
single-page snapshot can advance the baseline. The existing diff ledger tracks
new, disappeared, changed and reappeared listing IDs without treating any change
as sale or verified pressing. This path never qualifies a delivered total or
enables purchase.

An optional `CandidatePipeline` captures new, changed and reappeared listing
events while the same revisioned snapshot lease is held. It retains one row per
exact release/listing pair. A changed price or saved-rule/control/schedule
revision creates a new pending review generation; an unchanged snapshot or
restart does not duplicate a candidate. Two workers cannot claim the same row.
Interrupted or uncertain reads stay stopped until a distinct fresh event or
revision explicitly reopens them. The current injected detail reader accepts
**synthetic observations only**. This is fixture-tested orchestration, not live
candidate qualification or permission to collect marketplace data.

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

Tests use disposable SQLite and synthetic pages. One integration test runs the
real socket client through a private temporary Unix socket into the scheduler
and durable baseline; it has no Discogs session or network. Other tests exercise
restart cadence, nonoverlap, pause/revision races, incomplete pages and failure
backoff. Runtime activation is still blocked by Discogs' site terms for automated
listing collection. The owner approved a fixed-read private browser handoff;
the dedicated group-restricted Unix socket can connect the host-side reader
without exposing the browser profile. Connection does not start a worker or
authorize recurring collection.

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
