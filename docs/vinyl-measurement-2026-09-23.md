# Vinyl measurement: 2026-09-23

## What was actually measured

| Check | Result | What the denominator means |
| --- | --- | --- |
| Production ingestion, broad rap monitor | [Scan #5](https://github.com/nictowey/Finder/actions/runs/35814465415): 10 fetched; 10 new; 0 invalid, suppressed, or unprocessed; 0 detail failures; 11 Browse GET attempts; 0 retries. | One capped first page on September 23, not all relevant listings. This is the second distinct successful UTC date toward the seven-day ingestion audit. |
| Cost and listing fields in that sample | 10/10 had quoted shipping, a fixed-price delivered subtotal, condition, and item specifics; 0/10 had `listing_ends_at`. | Ten normalized eBay responses. Destination context was `none`, so these quotes are not verified for any buyer's postal code. |
| Synthetic pressing decisions | 35 fabricated cases, seven scenarios in each of jazz, rock, classical, electronic, and folk. The policy made 20 provisional pressing calls, all 20 agreed with the constructed truth; 15 cases withheld a pressing call (five ambiguous, five insufficient, five rejected); zero exact-variant calls. | The scorer was given both catalog variants in every case. Genres reuse the same scenario templates, so 20/20 is **not** a real-world precision estimate or an independent holdout. |
| Live catalog title retrieval | Pending authenticated cross-genre workflow result. | Favorable catalog-derived titles without seller identifiers; cannot estimate actual seller-title recall. |

The synthetic panel is rerunnable with `python scripts/measure_vinyl_identity.py`. The live
Discogs panel runs in the manual `Cross-genre vinyl catalog measurement` workflow with
`python scripts/measure_live_catalog.py` and prints only aggregate per-genre counts. It fetches
up to three initial Vinyl releases per genre, selects one with the expected catalog genre, then
searches on a synthetic title copied from that release with at most four hydrated candidates.
No selected release is pinned during retrieval. Request budget without retries: at most 45
Discogs catalog calls across five genres. These probes neither read Discogs marketplace data nor
include real eBay listings in a labeled dataset.

## What cannot be inferred yet

| Product question | Current status | Measurement needed |
| --- | --- | --- |
| Does Finder find most relevant active listings for a chosen pressing? | Unknown. Both the broad and exact-target eBay searches reach a ten-item page cap in observed runs. | Compare a target's surfaced item set with an independently checked sample from the same marketplace and time window, including misses and the reasons for them, after data-use review. |
| How accurately does Finder identify real pressings across genres? | Unknown. No representative retained labeled real-listing sample or held-out set. | Obtain the evaluation-use decision, define an adjudication protocol, and label development and holdout sets across at least 40 families and difficult neighbor pressings. Report family precision, provisional/exact pressing precision, abstention, and retrieval misses separately. |
| Do delivered totals reflect the collector's destination? | Unknown. The Production sample had no destination context. | Repeat a bounded scan using an explicit buyer destination and verify the quoted context before treating a subtotal as a buyer cost. |
| Are listings still buyable, and are any underpriced? | Unknown. Current offers are snapshots; no permitted sold-transaction source or valuation model. | Verify current listing state at review time and secure the separate sold-data and provider-use decisions before value claims. |

The policy never emits `exact_variant` today. The roadmap's 98% family and 99% exact pressing
precision targets have **not** been met or estimated by this work. Current buyer-facing deal
claims remain blocked by the provider-use decisions in
[`decisions/0001-provider-use.md`](decisions/0001-provider-use.md).
