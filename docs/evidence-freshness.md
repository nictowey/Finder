# Known-invalid evidence

Age alone does not make a listing's details current. A changed search summary invalidates
older reviews for the same marketplace item across watches. A failed detail request
invalidates the requesting watch's review without claiming the item ended or sold.

The shared listing carries a monotonic local `finder_details_invalidated_at` watermark in
its source metadata. Provider snapshot replacements retain it, including older in-flight
requests. Its associated summary fingerprint is preserved with it, so another watch seeing
the same already-checked change does not invalidate fresh evidence again. This also protects
a new watch without an existing inbox row, and survives removal
of the watch that first observed the change. A review can become current only when assessed
from successful details at or after its invalidation boundary. Older assessments cannot
replace newer completed reviews.
Invalidation advances the review update token, so an already-open page must refresh before
saving a judgment. It does not freshen provider timestamps or rewrite prior owner provenance.

Known changes lock shared listings in stable item order, then their reviews and queued events.
They do not acquire another watch's lease lock. Invalidated evaluated rows become due for
reassessment; failed requests retain retry backoff. Cached settings re-sorts and snapshot reuse
cannot turn known-invalid details into a fresh assessment.

The dashboard withholds invalidated photos, specifics and price assurances, displays a fresh
check warning, and retains the listing reference and owner judgment. Such rows cannot pass an
active price ceiling or notification eligibility check. All-prices, no-ceiling and saved
history views keep the reference discoverable. A failed read remains distinct from a confirmed
unavailable result.

Invalidation expires pending notification events. Only a pending event with zero attempts is
recorded as suspended in review metadata. A later fresh, eligible, unjudged and undismissed
assessment can resume that exact existing event with zero attempts. It creates no replacement
event and resets neither the alerted flag nor the initial digest marker. Attempted or in-flight
delivery may already have reached a device; it is never blindly rearmed or claimed recalled.

Validation includes synthetic two-watch changes, new-watch reuse, removal of the first watch,
old in-flight completion, cache resets, owner feedback, price/UI projection, and idempotent
same-event recovery. The existing isolated PostgreSQL rehearsal checks reversed overlapping
pages with actual blocking-PID evidence. No provider requests or production data are used in
these tests. Seller deletion continues to cascade the listing, reviews and event references.

Prefer a forward fix, or roll back only the presentation while retaining the worker and
notification invalidation guards. A full revert to older code ignores retained watermarks and
can reuse known-invalid cached evidence; recover fresh details before removing those guards.
There is no schema rollback or blanket event replay. Local watermarks and suspended-event
references stay with their records; do not force expired events back to pending without fresh
eligibility checks.
