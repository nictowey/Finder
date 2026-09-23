# Provider inquiries for Finder

Prepared: 2026-09-23. Status: **drafts only; not submitted**. Record the actual ticket links,
dates, replies, agreement versions, and decisions in
[`0001-provider-use.md`](0001-provider-use.md) after submission and review. An API key or
Production access is not a written decision on these use cases.

## eBay: proposed Developer Technical Support ticket

Route: [eBay Developer Technical Support](https://developer.ebay.com/support/developer-technical-support).
The registered developer activates support under Profile & Contacts, then asks through
AI-Assisted Support and opens a ticket for a written policy/business-model decision. If that
channel cannot decide, request the correct policy or business contact. Add the application ID
privately in the ticket; never put credentials or production identifiers in this repository.

**Subject:** Written review of Finder's buyer-facing vinyl watchlist, comparison, and data use

> Hello eBay Developer Support,
>
> We are building Finder, an early-stage collector tool using the official Production Buy Browse
> API (`item_summary/search` and `item/{item_id}`) for bounded searches of active vinyl listings.
> It maintains normalized current listings and repeat observations in a private PostgreSQL
> database, and handles eBay marketplace account-deletion notifications. It uses Discogs catalog
> release metadata to investigate a record's pressing. It does not buy items automatically,
> scrape eBay, or reprice sellers.
>
> We are requesting a written intended-use and data-handling decision for these distinct stages:
>
> 1. **Free collector watchlist:** A buyer saves a pressing, condition floor, destination, and
>    their own maximum delivered price. Finder shows eligible active listings, an honest known
>    price plus shipping caveats, pressing uncertainty, and a link back to the eBay item. It
>    makes no market-value or undervaluation claim. Is this use allowed under our existing Buy
>    API access, and does it require additional business-model review or agreement?
> 2. **Optional future valuation:** If we separately license independent sold transactions,
>    could Finder show a conservative range and comparable count for a pressing, identify an
>    eBay listing priced below that range, sort by that signal, and notify a buyer with a link
>    to the item? We noticed the API License Agreement's restriction on using eBay content to
>    model or suggest prices for items listed on eBay. Is this proposed buyer-facing use
>    prohibited, or is there a route to express written consent or a qualifying agreement?
>    Please address display, ranking, and notifications separately.
> 3. **Storage and evaluation:** May we keep normalized current listings and repeat observation
>    snapshots? For how long, and what refresh or deletion is required? May human reviewers
>    label real listings and retain their judgments or derived features to measure and tune a
>    deterministic pressing matcher? May any sanitized real listing fixtures be published in
>    our open-source tests? Please clarify how the agreement's intermediate-copy and algorithm
>    training restrictions apply.
> 4. **Deletion and business model:** How should deletion notifications affect observations,
>    derived candidate evidence, reviewer labels, database backups, local copies, and a minimal
>    seller-ID tombstone that prevents reimport? Do the answers change for a small private
>    collector pilot, paid subscriptions, affiliate links, or buyers who sometimes resell?
>
> We will keep valuation and public release gated while these questions are unresolved. Please
> identify which uses are permitted as described, which require an additional agreement or
> review, and which are unavailable. If this support channel cannot issue the relevant written
> determination, would you route us to the responsible team?

Before submission, privately add the real application ID, production marketplace, approximate
request volume, and a screenshot or mock of each proposed output. Do not include credentials,
OAuth responses, seller identities, or individual live listing payloads.

## Discogs: proposed Help Center request

Route: [Discogs Help Center](https://support.discogs.com/hc/en-us). Its
[API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)
direct questions about intended use there. Submit from the account that owns the API access.

**Subject:** Review of Discogs catalog API use in a vinyl watchlist with eBay links

> Hello Discogs team,
>
> We are building Finder, an early-stage tool that helps vinyl collectors identify a specific
> pressing in an active eBay listing. We currently request only `/database/search` and
> `/releases/{id}`. We use release titles, artists, formats, barcodes, catalog numbers, and
> dates as catalog identity evidence. We do not use Discogs marketplace prices, sales history,
> orders, seller information, or restricted images.
>
> The proposed free watchlist would display a possible release identity, its uncertainty,
> a direct link to the Discogs release and “Data provided by Discogs” attribution beside the
> evidence, and a separate link to the original eBay item. It would let a collector set their
> own maximum delivered price. A future paid subscription could provide saved searches and
> notifications; no pricing feature or paid launch is approved today.
>
> Your API Terms identify use intended to drive traffic to non-Discogs services as a prohibited
> commercial use. Would the described free watchlist with outbound eBay purchase links fall
> under that restriction? If so, is written permission possible, or is there another acceptable
> design? Does the answer differ for a private collector pilot or a paid subscription? What
> written permission is needed before charging for API-integrated functionality?
>
> Please also clarify the allowed storage period for search results and release metadata used
> in matching; the six-hour display freshness condition; required release links and notices;
> and whether separately obtained CC0 catalog data from a Discogs data dump has different
> application/use conditions from data obtained through the API. We would appreciate a written
> decision for the exact free outbound-link journey and the separate paid proposal.

## Decision handling

- Save replies and the applicable terms version outside the public repository if they contain
  account, ticket, or confidential agreement details; summarize the actionable decision here.
- Keep the free outbound watchlist, real-listing retention/evaluation, valuation, and paid use as
  separate go/no-go decisions. A conditional answer requires the stated condition to be met.
- Revisit implementation and the release gates after an answer; do not infer permission from a
  lack of response or from successful API calls.
