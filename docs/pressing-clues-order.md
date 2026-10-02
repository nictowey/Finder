# Optional pressing-clue ordering

Newest first remains the default. To review and Judged also offer **Pressing clues
first**. This puts current recorded pressing clues ahead of generic album results,
then sorts each group newest first. Every row allowed by the existing view filters
remains reachable. Judged includes the option to inspect saved decisions using the
same evidence rule; owner identity labels and purchase markers never determine rank.
Other views retain their existing newest-first workflow.

This is evidence ordering, not a new identity assessment. It does not change the
matching policy, tiers, watches, price ceilings, alert eligibility or owner judgments.
It makes no claim of improved precision or of covering every real pressing. Sparse
generic listings may still be the wanted pressing. No description or new catalog
inference is used.

## What qualifies

The query uses only recorded review meanings: `seller_identifier_claim`,
`seller_numbered_claim`, `seller_color_claim`, and nonempty matched owner signs
with the existing `Clue.describe()` shape. The generic artist/album clue,
`cheat_sheet_sign` without a found sign, missing signs, common-version signs,
current tier alone, seller keywords and owner labels do not add priority.

Color-only support is excluded when existing verification flags report an incomplete
pair, unresolved choice, color conflict, required-sign conflict, or unsupported
catalog color comparison. The last exclusion is deliberately conservative: the
current fields cannot establish full owner-palette coverage without reconstructing
the matcher. Independent identifier, numbering or non-color owner signs can still
qualify while those color warnings remain visible.

The review and shared listing must refer to the same successful detail observation
within the last **one hour**, matching the UI's fresh-check threshold. This is narrower
than the six-hour seller-content display window. Both timestamps must use the app's
normalized UTC ISO format; missing, malformed and unsupported values receive no
ordering support. A current review-tier status and watch revision, no invalidation
or pending seller change, well-formed detail quality flags, and no unavailable,
ended or failed/stale-detail flags are also required. A newer shared listing never
freshens old review clues. With no qualifying clues, rows remain
in deterministic newest order and the UI states that no ordering support is recorded.

## Paging contract

The opt-in query applies existing price and judgment filters before keyset paging.
It orders by binary clue priority, first discovery time, watch, marketplace and item.
Its cursor binds the selected order and all filters to that complete key. Existing
three-string newest-first cursors remain supported; incompatible cursors fail clearly.
Changing the view, price, order or judgment filters resets the UI to the first page.

This remains a live queue, not a stored snapshot. One SQL statement computes both
the page and a digest of the eligible set's ranks and keys. If a new listing, save,
invalidation, source refresh, watch edit, price change or natural evidence aging
changes membership or rank, the next cursor is rejected with `inbox_order_changed`.
The UI restarts once at the first page and explains why. An exhausted result still
returns its generation internally, so a changed empty page cannot silently look like
normal exhaustion. Source edits that leave membership and ordering unchanged are
shown as current data; this is not a historical evidence snapshot. No additional
seller payloads, session snapshots or persistent database records are retained.

## Validation and release

Synthetic API tests execute the real queries on a PostgreSQL engine, including
multiple pages, tied keys, all-prices and saved-decision intersections, unsupported
timestamps, UTC midnight/leap-day boundaries, missing/stale/invalidated evidence,
color ambiguity and source/rank changes. DOM tests cover interrupted loads, pending
judgment saves, repeated paging, filter changes, one-time conflict recovery, and
browser Back/Forward between views. Public fixtures contain invented data only.

`scripts/check_pressing_order.ts` runs those same API scenarios on real PostgreSQL.
It is called by the existing isolated CI rehearsal and pre-deployment database gate;
the exact candidate must pass that gate before publishing the endpoint. Local engine
tests alone do not establish production compatibility or owner outcome improvements.
