# Personal pressing hunter: plan

Status: **proposed, not implemented.** Written September 24, 2026.

This plan turns Finder into a personal tool for spotting specific vinyl pressings on eBay
at or below a price the owner sets. It is not a public product. Several gates in `AGENTS.md`
and `ROADMAP.md` were written for a public launch. Where this plan relaxes them, the owner has
to approve that explicitly and those files need updating before implementation (see
[Decisions needed](#decisions-needed)).

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
2. **Observation is separate from judgment.** Text rules and photo reading report what they
   saw. Plain, deterministic code turns those observations into a tier.
3. **"Not visible" beats a guess.** Missing evidence means *unclear*, never *confirmed* and
   never *rejected*.
4. **Measure before trusting.** Every automated signal starts in shadow mode. It becomes
   decisive only after the owner's own verdicts show it's accurate.
5. **Personal-use terms stance.** Official APIs only, no scraping, no eBay-derived price
   modeling. Listing photos are sent to the vision model for inference only; images are not
   stored (see [Terms and data handling](#terms-and-data-handling)).

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
                                  photo reading (vision model, only for listings that pass)
                                                              │
                                  alert: tier, clues, photos, "ask the seller" draft
                                                              │
                                  owner's verdict ──► personal accuracy record
```

## 1. Watch definition

Each watch keeps today's fields (Discogs release, currency, destination, conditions) and adds:

| Field | Purpose |
| --- | --- |
| `confirmed_max` | The most I'd pay when the evidence says it's my pressing. Replaces `maximum_subtotal`. |
| `gamble_max` | The most I'd pay for an *unclear* listing, where the rare pressing isn't claimed but isn't ruled out. Usually well below `confirmed_max`. Optional. |
| `tells` | Signs of my pressing: `{id, kind, value, weight: required\|supporting, source: derived\|owner}`. |
| `anti_tells` | Signs of a common version, in the same shape. |
| `auction_alerts` | Whether to check auctions ending soon, and how early to alert (default 2 hours). |

`kind` is one of: `color`, `catalog_number`, `barcode_present`, `barcode_absent`, `label_name`,
`label_design`, `runout_text`, `country`, `year_range`, `numbered`, `weight_180g`, `keyword`,
`sticker_text`, `insert`.

`tells` and `anti_tells` are shared by the text rules and the photo reading, so one cheat sheet
drives both.

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
| Auctions ending soon (`buyingOptions:{AUCTION}`, end-time filter, `sort=endingSoonest`) | Low-competition auctions | Hourly |

- The existing resumable discovery engine (`discovery_worker.py`) stays as the full-backlog
  pass when a watch is created and for daily reconciliation. The newest-listing polls above are
  the fast path.
- Remove the legacy sampled scan path in `watch_worker.run_due_watches` once discovery is
  confirmed on all slots.

**eBay request budget** (5,000 Browse calls/day; estimates to re-check once live):

| Item | Calls/day for 5 watches |
| --- | --- |
| Broad polls: 5 × 144 | 720 |
| Barcode polls: 5 × 48 | 240 |
| Bargain variants: 5 × 2 queries × 24 | 240 |
| Auction checks: 5 × 24 | 120 |
| Detail fetches for new or changed listings | 300–800 |
| Daily reconciliation passes | 200–600 |
| **Total** | **~1,800–2,700**, leaving the existing shared reserve intact |

The shared budget table (`adapters/ebay/budget.py`) keeps enforcing the reserve. Raising the
three-watch cap to five is an owner decision.

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
| likely_target | subtotal ≤ `confirmed_max` |
| unclear | subtotal ≤ `gamble_max` |
| auction (any tier except likely_other) | current bid + shipping ≤ the tier's max, and the auction ends within the alert window |
| likely_other | never |

An unknown subtotal still appears in the inbox but doesn't alert unless the owner turns that on.

## 6. Photo reading

### When it runs

Only for listings that pass the price gate, and only once per distinct photo set: a hash of the
ordered image URLs. A photo change triggers a re-run. There is a hard daily spend cap; when
reached, alerts go out without photo reading and say so.

### Input

- Up to 12 listing images (`image` + `additionalImages` from the item detail call). Request
  the largest eBay size, download, downscale to **1600 px on the long edge**, and send as
  base64. Base64 rather than the URL source lets the code control size and cost and avoids
  failed remote fetches.
- The watch's `tells` and `anti_tells`, plus a short description of the closest sibling
  versions (for example "Version B: black vinyl, barcode on back, label text …").
- **Seller text is not included.** The photo pass must be independent evidence from the text
  triage and must not anchor on the title.

### Model call

- Anthropic Python SDK, `client.messages.parse(...)` with a Pydantic schema (structured
  output), adaptive thinking, and effort starting at `medium`. Measure before raising it.
- Model: `claude-opus-5` by default; see the cost table for alternatives, which are the owner's
  choice.
- Enable the server-side refusal fallback (`fallbacks`) as the SDK guidance recommends for
  this model.
- The system prompt holds the fixed instructions. The per-watch cheat sheet follows it. Prompt
  caching will usually **not** apply, because the prefix is shorter than the minimum cacheable
  length. Don't design around caching.
- The Message Batches API (half price, results usually within an hour, at most 24 hours) is
  **not** used for alerts, where speed matters. It may be used for non-urgent re-checks and for
  the evaluation runs in phase 5.

### Output schema (sketch)

```python
class PhotoRole(StrEnum):
    label_a = "label_a"; label_b = "label_b"; runout = "runout"; vinyl = "vinyl"
    cover_front = "cover_front"; cover_back = "cover_back"; sticker = "sticker"
    insert = "insert"; spine = "spine"; other = "other"

class PhotoNote(BaseModel):
    index: int
    role: PhotoRole
    stock_or_catalog_image: bool      # not a photo of the actual copy
    legible: bool

class TellCheck(BaseModel):
    tell_id: str
    status: Literal["confirmed", "contradicted", "not_visible"]
    photo_index: int | None
    observed: str | None              # exact text read, or what was seen

class PhotoReading(BaseModel):
    photos: list[PhotoNote]
    vinyl_color: str | None
    label_text: list[str]             # quoted exactly as read
    runout_text: list[str]            # quoted exactly; empty if not legible
    barcode_visible: Literal["present", "absent", "not_visible"]
    copy_number: str | None           # for numbered editions
    tells: list[TellCheck]
    anti_tells: list[TellCheck]
    closest_version: Literal["target", "sibling", "unclear"]
    seller_questions: list[str]       # photos that would settle it
```

Key instructions in the prompt:

- Report only what's visible; default to `not_visible`.
- Quote label and runout text exactly; never fill it in from memory of the album.
- Flag stock or catalog photos. Evidence from a stock photo counts as not visible.
- Lighting changes how vinyl color looks; report a color claim as confirmed only when it's
  unambiguous.

### Turning the reading into a tier (plain code, not the model)

- Any photo from the actual copy confirming an anti-tell → **likely_other**.
- All required tells confirmed from actual-copy photos, and nothing contradicted →
  **likely_target**.
- Otherwise the text tier stands, with the photo evidence attached.
- `closest_version` is stored and shown, but it's advisory and never decides the tier.

### Rollout

1. **Shadow mode:** the reading runs and appears in the inbox, but doesn't change tiers or
   alerts.
2. After at least 30 owner verdicts (section 8), compare. Enable tier changes only if photo
   **confirmed** results are wrong in at most 1 of 30 cases. A wrong confirmation is the costly
   error.

### Cost estimate (to verify with `count_tokens` on real listings)

Image tokens scale with pixel area (roughly one token per 28×28-pixel patch). A 1600×1200 photo
is about 2,500 tokens. With 8 photos, ~1.5k tokens of instructions and cheat sheet, and ~1.5k
output tokens including thinking:

| Model | Price per million input / output tokens | Per listing (est.) | 20 listings/day (est.) |
| --- | --- | --- | --- |
| `claude-opus-5` (default) | $5 / $25 | ~$0.15 | ~$3/day, ~$90/month |
| `claude-sonnet-5` | $2 / $10 | ~$0.06 | ~$1.20/day |
| `claude-haiku-4-5` | $1 / $5 | ~$0.03 | ~$0.60/day |

Volume is the biggest unknown: the price gate and photo-hash deduplication decide how many
listings reach this step. The daily spend cap bounds the worst case. This is the first recurring
cost in a project that has so far kept hosting free.

### Known limits

- Runout etchings are often not photographed, or illegible from glare.
- Many represses use identical labels; photos can't separate them.
- Sealed copies show no label, vinyl or runout.
- Vinyl color varies with lighting; splatter and marble patterns vary per copy.
- Sellers reuse other people's or catalog photos.

## 7. Alerts

Push notifications (existing), linking to an inbox card that shows:

- Tier, delivered subtotal against the relevant maximum, and time left for auctions.
- Text clues and anti-clues found; the photo-reading results with the photo that shows each
  item.
- The listing photos, inline in the private dashboard and not stored.
- An **"ask the seller"** draft built from `seller_questions`, for example: "Could you share a
  photo of the dead wax on side A? I'm looking for the etching '…'." The owner sends it by hand
  on eBay; the buyer API can't message sellers.

## 8. Owner verdicts (the accuracy record)

Each inbox card has buttons: **my pressing**, **other version**, **can't tell**, **bought**.
Store the item ID, watch, verdict, text tier and photo tier at the time. Store no listing
content beyond what the inbox already keeps.

This builds the missing accuracy measurement from ordinary use:

- Precision of the *likely_target* tier, from text alone and with photos.
- How often *unclear* copies turn out to be the target.
- Photo reading's wrong-confirmation rate, which is the gate in section 6.

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

## Phases

| Phase | Deliverable | Done when |
| --- | --- | --- |
| 0. Clean-up | Remove the legacy scan path; trim the docs | Tests pass; one dated evidence log remains |
| 1. Versions + cheat sheet | Master versions fetch, distinguishing-fact proposals, dashboard editor | Acceptance in section 2 passes for both test targets |
| 2. Triage + price + searches | Three tiers, two prices, anti-tells, barcode/bargain/auction searches, 10-minute cadence | A week of live runs within budget; no unclear or likely-other alerts above their prices |
| 3. Photos in alerts + verdicts | Inline photos, verdict buttons, accuracy page | Owner can decide from the card; verdicts recorded |
| 4. Photo reading, shadow mode | Model call, schema, cost cap, results shown | 30+ verdicts collected alongside readings |
| 5. Photo reading, live | Photo evidence can change tiers | Wrong confirmations ≤ 1 in 30; spend within cap |

Each phase is its own PR with tests, following the existing required checks.

## Terms and data handling

- eBay: official Browse API only, no scraping, no eBay-derived price modeling. Listing photos
  are sent to the model for inference and then discarded. Only the extracted observations
  are kept, and they're deleted with the listing on seller-deletion notices. Whether eBay's
  content rules cover sending photos to a third-party model isn't spelled out; treat it as a
  gray area acceptable for personal use, not for a product.
- Discogs: catalog endpoints only (adding `/masters/{id}/versions`). No marketplace or
  price data. Keep "Data provided by Discogs" attribution in the dashboard.
- Anthropic API key: new encrypted Actions secret `ANTHROPIC_API_KEY`. Never logged. Update
  `.env.example` when implemented.

## Decisions needed

1. **Personal scope:** approve relaxing the public-launch gates in `AGENTS.md` and `ROADMAP.md`
   for personal use (alerts on uncertain pressings, price-gated by the owner's own numbers).
2. **Photo-reading model and spend cap:** `claude-opus-5` by default; a cheaper model is
   the owner's choice. Proposed starting cap: $3/day.
3. **Watch cap:** keep three, or raise to five within the request budget above.
4. **Hosting for faster polling:** see section 10.
5. **Repository visibility:** it affects free Actions minutes and whether the public-log rules
   are still needed.
