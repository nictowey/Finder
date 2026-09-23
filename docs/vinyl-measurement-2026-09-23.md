# Vinyl measurement: 2026-09-23

## What was actually measured

| Check | Result | What the denominator means |
| --- | --- | --- |
| Production ingestion, broad rap monitor | [Scan #5](https://github.com/nictowey/Finder/actions/runs/35814465415): 10 fetched; 10 new; 0 invalid, suppressed, or unprocessed; 0 detail failures; 11 Browse GET attempts; 0 retries. | One capped first page on September 23, not all relevant listings. This is the second distinct successful UTC date toward the seven-day ingestion audit. |
| Cost and listing fields in that sample | 10/10 had quoted shipping, a fixed-price delivered subtotal, condition, and item specifics; 0/10 had `listing_ends_at`. | Ten normalized eBay responses. Destination context was `none`, so these quotes are not verified for any buyer's postal code. |
| Synthetic pressing decisions | 35 fabricated cases, seven scenarios in each of jazz, rock, classical, electronic, and folk. The policy made 20 provisional pressing calls, all 20 agreed with the constructed truth; 15 cases withheld a pressing call (five ambiguous, five insufficient, five rejected); zero exact-variant calls. | The scorer was given both catalog variants in every case. Genres reuse the same scenario templates, so 20/20 is **not** a real-world precision estimate or an independent holdout. |
| Live catalog title retrieval | [Catalog panel #1](https://github.com/nictowey/Finder/actions/runs/35815105736) completed. Jazz, rock, classical, and electronic each had an eligible Vinyl release in the first three search results; all four selected releases appeared in the first four results from a catalog-derived title. The folk query had no eligible release in its first three results. | Five chosen catalog queries, four eligible selected releases, four favorable synthetic title lookups. This is 4/4 retrieval **conditional on selection**, not a 4/5 real-listing recall rate. All four title-search first pages were full or reported more results. |
| Private watch inventory sample | [Deployment #6](https://github.com/nictowey/Finder/actions/runs/35855743741) on the merged reconciliation code passed 256 Python and 16 Node tests, isolated PostgreSQL checks and private deployment. It attempted two watches, completed both with zero scan failures and added one inbox row. Both watches displayed an older best-match sample of six items. The DS2 newest page and two of three pink/green newest pages were capped. | Two saved targets and a deployment scan around 11:39 UTC after the prior [scheduled scan around 10:00 UTC](https://github.com/nictowey/Finder/actions/runs/35846240451). This first pass did not recover the known older DS2 lead. The alternate direct recheck was observed in the subsequent scan below. |
| Known-lead direct refresh | [Private scan #4](https://github.com/nictowey/Finder/actions/runs/35857378734) at approximately 11:56 UTC completed both due watches with zero failures and zero new inbox rows. The owner-supplied DS2 example, outside the latest newest results and first older sample, was directly rechecked and appeared again in the fresh inbox under policy v2. Five catalog alternatives were examined; four remained compatible, so its alert was withheld. | One previously known lead recovered from the existing inbox, not a listing found independently by marketplace search. This demonstrates rechecking, not broader discovery recall or proof of the numbered edition. |
| Production Browse quota | [Read-only quota check #3](https://github.com/nictowey/Finder/actions/runs/35857263684) returned a 5,000-call daily `buy.browse` window with 4,940 calls remaining at roughly 11:54 UTC; the separate bulk-item window reported 5,000 remaining. | One point-in-time application response. The nominal maximum of 4,464 daily watch refresh calls leaves 536 calls for initial scans, retries and other workflows only if the cadence and three-watch worst case hold. There is no global reservation or guaranteed future headroom. |
| Third target and inversion failure | The owner added an older, non-rap album as the third watch. [Private scan #6](https://github.com/nictowey/Finder/actions/runs/35887951829) at approximately 16:20 UTC attempted and completed all three watches with zero failures and ten new inbox rows overall. Its one newest-page query hit the cap. Ten rows for that target appeared as conflicting; inspection of a same-album listing showed a seller `Artist` field in `Last, First` order even though the title gave the catalog artist in normal order. The inbox marked this an artist conflict. The fetched set also included listings for a longer, distinct album title. | A live false-conflict diagnosis, not a representative error rate. A bounded first page and one catalog alternative do not establish target recall, exact pressing identity or a full album family. The following code change needs another live run before claiming it repaired this example. |
| Scheduling and quota follow-up | The scheduled watch workflow ran around 06:00 and 11:02 EDT on September 23, with a manual scan in between. The [later quota check #4](https://github.com/nictowey/Finder/actions/runs/35887651796) returned 4,880 Browse calls remaining at approximately 16:17 UTC. | Scheduled checks were roughly five hours apart despite a twice-hourly cron expression. Manual runs and a point-in-time quota reading do not prove timely monitoring. Measure successive scheduled timestamps over 14 days and provide a dependable trigger before a one-hour discovery claim. |

The synthetic panel is rerunnable with `python scripts/measure_vinyl_identity.py`. The live
Discogs panel runs in the manual `Cross-genre vinyl catalog measurement` workflow with
`python scripts/measure_live_catalog.py` and prints only aggregate per-genre counts. It fetches
up to three initial Vinyl releases per genre, selects one with the expected catalog genre, then
searches on a synthetic title copied from that release with at most four hydrated candidates.
No selected release is pinned during retrieval. Request budget without retries: at most 45
Discogs catalog calls across five genres. These probes neither read Discogs marketplace data nor
include real eBay listings in a labeled dataset.
The first attempt to run the panel immediately after authenticated matching validation hit
Discogs' rate limit ([smoke #28](https://github.com/nictowey/Finder/actions/runs/35814753513));
the separate manual panel completed without that stacked request load. A full first page does
not show whether a more relevant catalog release lies beyond the cap, and the missing folk
selection shows the chosen query and small initial cap do not guarantee genre coverage.

## What cannot be inferred yet

| Product question | Current status | Measurement needed |
| --- | --- | --- |
| Does Finder find most relevant active listings for a chosen pressing? | Unknown. Broad ten-item and target eight-item newest searches hit page caps. The rotating six-item older page is only a sample. A later direct getItem refreshed one **already known** DS2 lead; it did not make search more complete. | Compare a target's surfaced item set with an independently checked sample from the same marketplace and time window, including misses and the reasons for them, after data-use review. Measure scan delays separately. |
| How accurately does Finder identify real pressings across genres? | Unknown. No representative retained labeled real-listing sample or held-out set. | Obtain the evaluation-use decision, define an adjudication protocol, and label development and holdout sets across at least 40 families and difficult neighbor pressings. Report family precision, provisional/exact pressing precision, abstention, and retrieval misses separately. |
| Do delivered totals reflect the collector's destination? | Unknown. The Production sample had no destination context. | Repeat a bounded scan using an explicit buyer destination and verify the quoted context before treating a subtotal as a buyer cost. |
| Are listings still buyable, and are any underpriced? | Unknown. Current offers are snapshots; no permitted sold-transaction source or valuation model. | Verify current listing state at review time and secure the separate sold-data and provider-use decisions before value claims. |

The policy never emits `exact_variant` today. The roadmap's 98% family and 99% exact pressing
precision targets have **not** been met or estimated by this work. Current buyer-facing deal
claims remain blocked by the provider-use decisions in
[`decisions/0001-provider-use.md`](decisions/0001-provider-use.md).
