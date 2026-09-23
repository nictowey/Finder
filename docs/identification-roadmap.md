# Reliable pressing discovery: execution and acceptance plan

Owner direction: September 23, 2026. This is the active delivery sequence for Finder.

## Product outcome

Save a Discogs vinyl pressing and see the relevant eBay listings currently discovered, with
clear evidence for the requested edition, visible alternatives and uncertainty, and timely
notifications for actionable leads. Sparse descriptions must remain discoverable. A barcode,
runout, photo, or complete item-specific form is not universally required: the evidence needed
depends on what distinguishes that pressing from its neighbors.

The first supported scope is the owner's eBay US watchlist. DS2 numbered and Don't Be Dumb
pink/green are acceptance examples, not special cases to hard-code or enough to establish
performance on any vinyl. Other marketplaces and market-value estimates are separate stages.

## Baseline and gaps

| Question | Verified baseline | Gap to close |
| --- | --- | --- |
| Can a pressing be saved and scanned? | Owner login, two watches, scheduled worker and private inbox work. | Observe scheduled operation over time and verify push on the owner's device. |
| Does search find what is listed? | A bounded scan found the owner-supplied DS2 example. The recurring worker now samples older inventory and directly rechecks one known lead on alternating refreshes. | Eight newest results per query and at most 36 sampled older results per query; offsets shift. No independent coverage denominator. |
| Does the watch distinguish neighboring pressings? | Sparse color/numbering/identifier claims can surface a lead. | The initial scheduled policy compared only the target; the richer CLI comparison was separate. |
| Do identification rules work on real listings? | Synthetic rule tests pass. | No approved real-listing holdout, measured wrong-pressing rate, or missed-listing audit. |
| Does a low price mean a deal? | Optional buyer ceiling and shipping checks exist. | No qualified sold-transaction source or measured valuation model. |

## Delivery order

### 1. Make ambiguity visible in the running product — first implementation

- Retrieve a bounded set of catalog alternatives once per watch scan and reuse it across the
  listings. Preserve query/detail limits and indicate incomplete catalog retrieval.
- Compare the offered copy against the target and retrieved alternatives. Shared barcode,
  catalog number, color, or numbering claims cannot uniquely identify an edition.
- Keep a possible listing visible when several pressings fit, explain the ambiguity, and
  withhold automatic notifications for known ambiguous cases. Missing catalog evidence is not
  evidence that a competitor is impossible.
- A failed alternative lookup must leave discovery/review usable, report the failure, and
  withhold notifications that depend on that lookup. No exact-match promotion.

Acceptance: synthetic shared-identifier/color cases remain ambiguous; a contradicted
alternative does not create false ambiguity; missing details and failed catalog requests
cannot manufacture certainty. Offline regressions and an authenticated bounded live check pass.

### 2. Account for search coverage and recover missed inventory

- Record query version, sort, pages, result counts, caps, detail failures and success times.
  Separate "requests succeeded" from "query results exhausted" and from marketplace recall.
- Introduce a quota-budgeted initial inventory sweep, newest-listing refresh, and periodic
  reconciliation. Persist resumable progress; recover from interrupted scans without silently
  skipping pages. Recheck older candidates so an active offer does not vanish merely because
  it fell outside the newest first page. API offsets can shift; deduplication alone is insufficient.
- Keep broad artist/album searches alongside aliases and optional edition searches. Require
  no pressing keyword in every query. Inspect wrongly rejected and unclassified results too.
- Before raising request volume, measure actual quotas, current request cost, retries and
  other jobs sharing credentials. Display the remaining coverage gap when a budget is exhausted.

Acceptance: controlled multi-page, insertion-between-pages, restart, duplicate, failed-detail,
and quota tests pass. On an independently assembled permitted sample, report found/missed counts
for each target and time window, including listings search never returned. Initial pilot target:
at least 95 of 100 independently verified eligible active listings found; fewer than 100 is an
incomplete sample, and this target does not establish universal marketplace recall. Investigate
every miss. Report the time from listing publication to first discovery separately from scan time.

### 3. Establish correct answers with small manual review batches

Build a private review workflow and adjudication guide using synthetic examples first. Apply it
to real listings once the existing evaluation/retention use decision is recorded. Reviews are
separate from Dismiss, which only controls the inbox. No captured seller identities in public
fixtures, PRs or CI logs; preserve seller-deletion cascades for derived review records.

Each review records:

- Target release, listing reference, observation time, matcher/query version and review evidence.
- Verdict: target pressing, different pressing, cannot tell, or unavailable before inspection.
- Basis: readable copy number, record colors, jacket/label/sticker, identifier, runout, seller
  statement, or physical-copy inspection. Separate a seller claim from independently visible evidence.
- Correct alternative release if known, reason for exclusion, and any conflicting evidence.
- Whether the listing was discovered automatically or submitted as a known miss. A directly
  submitted item is never counted as a search success.

Start with batches of 10–20, including both user examples, near-identical variants, ordinary
black vinyl, reissues, numbered/un-numbered copies, unofficial editions, and incomplete listings.
Unresolved cases stay unresolved; they must not become convenient positive or negative labels.

Manual input from Nic: review a batch and supply the verdict plus the decisive detail visible
in the listing or owned copy. Additional known matches AND wrong editions are useful. No need
to type every runout or find every barcode. For the supplied DS2 case, preserve the owner's
identification as an assertion until the basis distinguishing its edition is recorded.

### 4. Improve and measure identification

- Build per-target comparisons of distinguishing attributes across relevant variants. Use
  independently available clues together; do not count a title repeating an item specific as
  two independent pieces of evidence. Account for incomplete or erroneous catalog entries.
- Diagnose title aliases, negation, partial colors, multiple records/lots, editions, shared
  identifiers and runout normalization before changing weights. Surface contradictory
  seller fields rather than silently choosing one.
- Add image-derived evidence only after text-only errors establish the need and permitted
  image use is confirmed; label its origin and retain an inspectable reference. A stock image
  does not verify the offered copy. Do not require an image model for all listings.
- Split development and holdout by release family before tuning; use later listings for an
  additional time-separated check. Deduplicate relists and near-duplicate copies across splits.

Acceptance report must include raw numerators and denominators for discovery, catalog candidate
coverage, family identification, pressing calls, wrong-pressing calls, abstentions, ambiguous
cases and unresolved truth. Report genres/eras and difficult variant groups separately.
Identity accuracy conditional on retrieval cannot substitute for end-to-end discovery.

Keep the roadmap's family precision target of 98% and exact-pressing target of 99%. An initial
200-listing/40-family corpus is diagnostic, not sufficient certification. Before exact auto-match,
require at least 300 held-out exact decisions with zero observed errors, broad family coverage,
and an uncertainty interval with its sampling assumptions. Correlated examples reduce the
strength of that result; do not imply a universal guarantee. Report abstention and supported
listing coverage so refusing every hard case cannot appear to solve the product.

### 5. Prove the working watchlist over time

- Run the two pilot targets, then broaden to ten varied targets in batches within capacity.
  Expand the three-watch capacity only after a measured request budget supports it.
- Observe 14 consecutive days: overdue/failed scans, dropped pages, repeated alerts, stale
  prices, unavailable listings, inbox visibility, and actual device delivery.
- For at least 100 qualifying newly listed sample offers, target 95 surfaced within 60 minutes;
  report actual delays and scheduler gaps. If volume is insufficient, extend observation instead
  of treating a small sample as a pass. Verify condition and buyer shipping context before
  applying delivered-price ceilings. Taxes/unknown costs remain explicit.
- Re-fetch availability and price when practical before notification; a disappeared search
  result is not proof of a sale. Test the full notification path on Nic's own device.

Acceptance: publish a dated private pilot scorecard with the above denominators, failures and
supported scope. It must answer "which listings did we miss, which did we misidentify, and how
quickly did useful ones arrive?" Software test counts are supporting evidence only.

### 6. Add market-value deal detection

After identification and live-watch gates, qualify permitted sold data with pressing, condition,
date, shipping and accepted-offer certainty. Test coverage before implementing valuation;
active asking prices are not sale values. Until then, call price-filtered results "within your
ceiling" and show the identity limitations. Discogs marketplace discovery is a separate source
qualification item and must not be represented as connected today.

## Execution and decisions

Stage 1 was merged in [PR #39](https://github.com/nictowey/Finder/pull/39). The
[authenticated catalog check](https://github.com/nictowey/Finder/actions/runs/35822937443)
passed and exercised five alternatives with one unresolved competitor on a synthetic listing.
The [private deployment](https://github.com/nictowey/Finder/actions/runs/35823068795) passed;
both saved watches scanned successfully and five new review rows were added. In the hosted inbox,
the pink/green lead had five alternatives checked with no competing fit in that bounded sample.
The older known DS2 lead retained its prior assessment because the newest-first capped scan did
not rediscover it. This is direct evidence of the stage 2 inventory/reassessment gap. It is not
evidence that the listing ended or that either pressing is verified. Notification delivery
requires the current policy and saved-watch revision; older assessments must be refreshed.

The first stage 2 delivery records per-query page and result counts, caps, request attempts
and retries. An eight-item newest page per query is supplemented by a six-item best-match
inventory sample every other scan, rotating through the saved queries and offsets 0–30. The
other refresh directly checks one existing possible pressing omitted by that scan, so its
review can be updated without depending on search rank. The cursor persists on interruption
and resets on watch edits. An unavailable/ended item is separated from active leads without
calling it sold. The nominal three-watch refresh ceiling is 4,464 Browse requests/day, before
initial sweeps, retries or other jobs. The actual app quota and independent missed-listing
sample remain outstanding. This limited sample cannot establish 95% recall or guarantee that
new offers appear within one hour. See [watchlist limits](private-watchlist.md).
The [first Production deployment](https://github.com/nictowey/Finder/actions/runs/35855743741)
passed with two completed watches and an older sample on each. It added one inbox row; the
known DS2 lead remained outside the six-hour active inbox in this first pass. Its direct
alternate recheck, independent recall, and scan timing remain to be verified.
An isolated read-only quota workflow can now report actual Production Browse resource limits
and remaining calls without printing credentials or listing identities. Its first live response
must be checked before increasing search volume. It does not enforce a global reservation across
workflows.

Stages 1–2 and synthetic tooling can proceed immediately. The existing
[provider-use record](decisions/0001-provider-use.md) governs real evaluation retention,
public release, images and valuation. Its unresolved questions must be settled before the
dependent uses; they do not block improving bounded retrieval and conservative private review.
Provider outreach drafts already exist. Sending them externally still requires authorization
to send the specific message, not another general approval to develop Finder.

After each delivery: record what changed, the relevant tests, any live observation, and what
remains unmeasured. Merge tested changes to main. Finish stage 2 with a permitted independent
missed-listing audit and actual quota accounting; then implement the manual review workflow.
