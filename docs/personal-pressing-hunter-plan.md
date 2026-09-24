# Personal pressing hunter: plan

Status: **implemented, awaiting deployment.** Written and approved September 24, 2026.

This plan turns Finder into a personal tool for spotting specific vinyl pressings on eBay
at or below a price the owner sets. It is not a public product.

## Owner decisions (September 24, 2026)

- Personal scope approved: public-launch gates in `ROADMAP.md` are parked, and alerts may
  include uncertain pressings, gated by the owner's own prices.
- **Add-ons stay free.** Paid photo reading with a vision model was declined; listing
  photos are shown in the inbox instead (section 6).
- **As many watches as possible:** 20, the most the 5,000-call daily eBay quota supports with
  room for details and retries. Cadence stretches as watches are added (section 3).
- Hosting: keep GitHub Actions dispatched by the existing Neon trigger (section 10).
- Everything else below was approved as written.

## Goal

Alert me quickly when an eBay listing is plausibly **the specific pressing I want**, at a
delivered price **at or below the number I set**, with enough evidence (text clues, photos,
photo-reading results) for me to decide in under a minute.

## Non-goals

- Estimating fair value or "market price" from eBay data. The owner sets every price by hand.
- Confirming a pressing automatically. The tool narrows the field and the owner decides.
- Buying, bidding or messaging sellers automatically.
- Categories other than vinyl, until vinyl works well.

## Principles

1. **The owner's number, not a model's.** Each watch has hand-set prices, researched from sold
   listings, Discogs history or price guides. The tool only compares against them.
2. **Observation is separate from judgment.** Text rules report what the seller wrote.
   Plain, deterministic code turns those observations into a tier.
3. **"Not visible" beats a guess.** Missing evidence means *unclear*, never *confirmed* and
   never *rejected*.
4. **Measure before trusting.** The owner's verdicts measure each tier before its alerts are
   relied on.
5. **Personal-use terms stance.** Official APIs only, no scraping, no eBay-derived price
   modeling, no stored images (see [Terms and data handling](#terms-and-data-handling)).

## Pipeline

```text
Discogs target ──► all versions of the album ──► distinguishing facts + owner's cheat sheet
                                                              │
eBay searches (broad, barcode, bargain, auctions ending) ─────┤
                                                              ▼
                                  text triage ──► likely / unclear / likely-other (hidden)
                                                              │
                                                  price gate (two owner-set prices)
                                                              │
                                  alert: tier, clues, photos, "ask the seller" draft
                                                              │
                                  owner's verdict ──► personal accuracy record
```

## 1. Watch definition

Each watch keeps today's fields (Discogs release, currency, destination, conditions) and adds:

| Field | Purpose |
| --- | --- |
| `maximum_subtotal` | The most I'd pay when the evidence says it's my pressing (the existing field, relabeled in the dashboard). |
| `gamble_max` | The most I'd pay for an *unclear* listing, where the rare pressing isn't claimed but isn't ruled out. Usually well below `maximum_subtotal`. Optional. |
| `tells` | Signs of my pressing: `{kind, value, required}`, up to 12. |
| `anti_tells` | Signs of a common version, in the same shape. |
| `auction_alert_minutes` | How long before an auction ends to alert (default 120; 0 turns auction alerts off). |
| `extra_queries` | Up to two of the owner's own searches, such as misspellings or "lot". |

As built, `kind` is one of `keyword`, `color`, `catalog_number`, `barcode`, `label`, `country`
or `numbered`. Keywords recognize common spellings of edition terms ("180g", "re-press").
Label designs, runout text and barcode absence can't be read from seller text, so they're left
to the owner's photo check.

## 2. All versions and distinguishing facts

**Why:** a clue only matters if it separates the target from its sibling pressings. Finder
currently checks up to five alternatives from a capped catalog search.

**How:**

1. Get the target's master ID from `/releases/{id}` (`master_id`), then page through
   `/masters/{master_id}/versions`. For each sibling, fetch `/releases/{id}` to get
   formats, colors, identifiers (barcode, matrix/runout), labels, country, year and notes.
   Cache this daily per watch. Stay well under Discogs' authenticated rate limit by using
   the existing client throttling. This widens the current Discogs allow-list (`/database/search`,
   `/releases/{id}`) to include `/masters/{id}/versions`; update `AGENTS.md` accordingly.
2. For each attribute the target has, count how many siblings share it:
   - Unique to the target → proposed **required or supporting tell**.
   - Shared by every sibling → useless; never shown as evidence.
   - Present on many siblings but absent on the target (such as "180g", "Reissue",
     "Remastered", or a barcode when the target predates barcodes) → proposed **anti-tell**.
3. Show the proposals in the dashboard. The owner accepts, edits or adds items; the owner's
   entries win. Each derived entry records the sibling count behind it.
4. Masters with very many versions: cap sibling detail fetches (for example 60) and show
   "partial version list" in the cheat-sheet UI instead of implying completeness.

**Acceptance:** for the two existing test targets (numbered DS2 and pink/green Don't Be Dumb),
the proposed tells include the numbering and the color respectively, and no attribute shared by
every sibling is proposed as a tell.

## 3. Searches

| Search | Why | Cadence (initial proposal) |
| --- | --- | --- |
| Broad artist + album, vinyl category, `sort=newlyListed` | Main feed; finds rare copies listed generically | Every 10 minutes |
| Barcode (`gtin=`), when the target or a sibling has one | Catches listings with poor titles that eBay matched to a product | Every 30 minutes |
| Bargain variants: common misspellings, artist-only + "lot", album-only | Poorly written listings | Every 30–60 minutes |
| Auctions | Already found by the album searches; re-read as their alert window opens | — |

- As built, every search runs through the resumable discovery engine: a full backlog pass
  when a watch is created, new-listing windows every poll interval, and daily reconciliation.
  The poll interval is `max(10, ceil(1440 × enabled queries / 2000))` minutes: 10 minutes for up
  to about 13 queries, about 44 minutes for 20 watches of three searches each.
- The legacy sampled scan path was removed. A worker run now claims due watches until about
  nine minutes are used, instead of a fixed three.
- Listing re-reads: likely leads every 6 hours, unclear every 24, others weekly; qualifying
  auctions just as their alert window opens.

**eBay request budget** (5,000 Browse calls/day; estimates for 20 watches, to re-check live):

| Item | Calls/day |
| --- | --- |
| New-listing polls across all enabled queries | ≤ 2,000 by design |
| Detail reads: new or changed listings, plus re-reads (likely 4/day, unclear 1/day, others weekly) | ~1,000–1,700 at ~150 listings per watch |
| Daily reconciliation passes (~6 per query) | ~360 |
| **Total** | **~3,400–4,100**, under the 4,800 usable above the 200-call reserve |

Detail reads grow with the number of listings each watch accumulates, so popular albums cost
more. If the quota runs short, the shared budget guard (`adapters/ebay/budget.py`) pauses
scans until eBay resets it rather than exceeding it; the dashboard shows those pauses.

## 4. Text triage (three tiers)

Plain, deterministic rules over title, item specifics and description fields already fetched:

- **likely_target:** every `required` tell is present in the seller text, and no anti-tell is.
- **likely_other:** any anti-tell is present, or a required tell conflicts (for example a
  different color). Hidden by default; visible under a filter.
- **unclear:** everything else. **This is where rare copies listed generically land.**

Existing rules stay: barcode conflicts, negated "not numbered" claims, comma-inverted artist
names, and the non-vinyl filter. Existing `possible_pressing / family_review / conflicting`
statuses map onto these three tiers.

## 5. Price gate

Delivered subtotal = price + shipping to the saved destination (existing logic; unknown
shipping stays unknown).

| Tier | Alert when |
| --- | --- |
| likely_target | subtotal ≤ `maximum_subtotal` (any price when unset) |
| unclear | subtotal ≤ `gamble_max` |
| auction (any tier except likely_other) | current bid + shipping ≤ the tier's max, and the auction ends within the alert window |
| likely_other | never |

An unknown subtotal still appears in the inbox but doesn't alert when a price applies.

## 6. Photo reading (declined)

Automated photo reading with a vision model was designed here but declined on September 24:
at an estimated $0.03–0.15 per listing it would have been the project's first recurring cost.
Instead the inbox shows up to 12 listing photos straight from eBay's image host, beside the
signs found or missing, and the owner judges them. Nothing is stored or sent to third parties.
A free alternative (for example local OCR of label text) can be revisited if manual review
becomes the bottleneck; it is not planned.

## 7. Alerts

Push notifications (existing), linking to an inbox card that shows:

- Tier, delivered subtotal against the relevant maximum, and time left for auctions.
- Signs of the pressing found, required signs not mentioned, and common-version signs found.
- The listing photos, inline in the private dashboard and not stored.
- An **"ask the seller"** draft asking for label and runout photos and listing the watch's
  signs. The owner copies it into eBay; the buyer API can't message sellers.

## 8. Owner verdicts (the accuracy record)

Each inbox card has buttons: **my pressing**, **other version**, **can't tell**, **bought**.
The dashboard's "Your verdicts" panel summarizes them per tier.
Store the item ID, watch, verdict, text tier and photo tier at the time. Store no listing
content beyond what the inbox already keeps.

This builds the missing accuracy measurement from ordinary use:

- Precision of the *likely_target* tier, from text alone and with photos.
- How often *unclear* copies turn out to be the target.

## 9. What to cut

- The legacy sampled scan path (`watch_worker.run_due_watches` non-discovery branch).
- Public-launch documentation. Replace the dated status blocks in `AGENTS.md`, `README.md` and
  `docs/identification-roadmap.md` with a short personal-scope statement plus one dated evidence
  log. Archive, don't delete, the provider-decision records.
- Alert suppression for uncertain pressings. Uncertainty is shown in the alert instead of
  blocking it, gated by `gamble_max`.

Keep: the eBay and Discogs clients, normalization, the delivered-price calculation, seller
deletion handling (required for Production keys), the shared request budget, leases,
push delivery, the owner-only dashboard, and the privacy rule for public logs.

## 10. Hosting and cadence

10-minute polling doesn't fit GitHub's twice-hourly cron. Options:

| Option | Cost | Notes |
| --- | --- | --- |
| Keep Actions, dispatched every 10 min by the existing Neon trigger | Free if the repository stays public (Actions minutes on private repos are limited) | About 144 runs a day; logs are public, so the existing log-privacy rules stay. |
| Small always-on worker (VPS or container host) | A few dollars a month | Simplest cadence and no public logs; adds a host to maintain. |
| Move the fast poll into a Neon Function (TypeScript) | Free within Neon limits (check execution-time limits) | Splits the scanner across two languages. |

Recommendation: start with the first option, since it needs no new infrastructure. Revisit if
dispatch latency or Actions limits get in the way.

## Delivery status

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0. Clean-up | Legacy scan path removed; docs trimmed into an evidence log | Done |
| 1. Versions + cheat sheet | Master versions, suggestions, dashboard editor | Done |
| 2. Triage + prices + searches | Three tiers, gamble price, auctions, barcode and extra searches, adaptive cadence, 20 watches | Done |
| 3. Photos + verdicts | Inline photos, verdict buttons, accuracy panel, seller question | Done |
| 4–5. Photo reading | Declined (paid) | — |

Live acceptance still to observe after deployment: daily Browse use stays under the quota with
the watches actually saved, alerts arrive within about one poll interval, and at least 30
verdicts per tier before trusting its accuracy.

## Terms and data handling

- eBay: official Browse API only, no scraping, no eBay-derived price modeling. Listing photos
  are displayed from eBay's image host and never stored.
- Discogs: catalog endpoints only (adding `/masters/{id}/versions`). No marketplace or
  price data. Keep "Data provided by Discogs" attribution in the dashboard.
