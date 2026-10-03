# Finder zero cost delivery and commercial launch gates

Status: proposal; remote and commercial gates remain open. Sources reviewed October 3, 2026. This document provides no provider approval or legal clearance.

Continue with the local manual/synthetic interface, SQLite and disposable local PostgreSQL, without provider or Neon usage. Preserve the goal of remotely accessible, autonomous discovery and alerts. The local interface is a development adapter; paused repository jobs do not prove separately deployed services have stopped incurring usage.

## Delivery choice

| Option | Useful role | Decision and tradeoff |
| --- | --- | --- |
| Local interface and fixtures | Matching, review and persistence development | Use now; live usefulness remains unmeasured. |
| Hosted worker, database and browser interface | Autonomous remote product | Target architecture, subject to all gates below. |
| GitHub Actions or Pages runtime | Reuse development infrastructure | Do not assume permitted production backend or commercial SaaS hosting. |
| Cloudflare or Neon Free | Candidate pilot components | Quotas, account eligibility, compatibility and terms unresolved; no deployment approval or $0 guarantee. |

**Source rules.** GitHub restricts Actions use and Pages business/SaaS hosting. Vercel Hobby permits personal or non-commercial use. [GitHub terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features), [Vercel terms](https://vercel.com/legal/terms)

Cloudflare documents free static requests, 100,000 shared daily Pages Functions/Workers Free requests, and 10 ms Workers Free CPU limits for HTTP/cron invocations. Neon's October 2 announcement gives Free projects 1 GB storage and 100 CU-hours monthly. These allowances establish neither account status nor complete product cost. [Pages pricing](https://developers.cloudflare.com/pages/functions/pricing/), [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Neon announcement](https://neon.com/blog/neon-free-plan-1-gb-per-project)

**Engineering inference.** Remote autonomy needs scheduling, execution, storage and delivery beyond static hosting. Cloudflare compatibility with the Python worker and PostgreSQL behavior must be demonstrated. Free branding cannot replace enforceable limits.

## Reuse the current boundaries

Keep `domain.py`, provider `adapters/`, vinyl `categories/`, deterministic `matching.py` and `persistence.py` separate. Reuse watch assessment and resumable discovery. The proposed remote flow connects an authenticated browser to a server API, bounded shared worker and persistent review/notification state. Keep credentials server-side, tenant isolation, deletion callbacks and notification deduplication. User-set price caps determine eligibility; no provider-derived pricing model, fair-value estimate or bargain score.

## Gates before resuming remote work or launching

1. **Demonstrate cost containment.** Verify the actual plan and billable schedules, functions, compute/storage, authentication, logs, egress and notifications before hosted resumption. Pausing Actions does not stop deployed services. Preserve required deletion handling. Define hard application/platform limits, disable unapproved paid fallback and test stopping before exhaustion. Exclude dependencies that cannot stay within the approved budget. Local development needs no production database access.

2. **Resolve provider applicability.** Source rules: Discogs CC0 fields do not waive API/integration restrictions on outbound traffic, charging for otherwise-free integrated access, or Restricted Data. Attribution/linking, necessary-only caching and display no more than six hours behind the source also apply. Resolve the eBay-linked catalog and paid/affiliate design before launch. [Discogs API terms](https://support.discogs.com/hc/en-us/articles/360009334593-API-Terms-of-Use)

   eBay production access depends on business-model requirements; working keys are not commercial approval. Its agreement restricts price modeling, storage and algorithm training. Document which terms cover discovery, labeling/evaluation, retention and monetization; obtain explicit approval where required, resolving uncertainty before dependent work. Manual copying or sanitization establishes no exemption. [Buy requirements](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html), [eBay API agreement](https://developer.ebay.com/join/api-license-agreement)

3. **Prove bounded, truthful operation.** Source rules: eBay requires displayed listings within six hours of source information, disclosure of older displayed information, and deletion from the application when publicly displayed content ceases to be publicly available. Public-display content must also be visually isolated from non-eBay information. [eBay API agreement, section 8](https://developer.ebay.com/join/api-license-agreement)

   Engineering consequence: budget refresh/removal alongside discovery, detail reads, reconciliation and retries; suppress stale displays when compliant refresh cannot fit. Declare watch/query capacity and shared request caps. Bound CPU, concurrency, storage and notifications; resume cursors after backoff. Test changed/failed/ended evidence, deletion races, tombstones and expiry across snapshots, history, labels and backups. Preserve source, observation time and decision provenance. Unknown shipping stays unknown; subtotal caps require same-currency, destination-confirmed price plus shipping, excluding tax, duties and fees.

4. **Measure identity and delivery prospectively.** With applicable rights resolved, freeze the policy for representative real prospective evaluation with independent target/other/unclear/unavailable labels. Include rejects and discovery misses; report denominators, uncertainty, catalog coverage and discovery delay. Synthetic tests establish no real accuracy. Keep 98% family and 99% exact-pressing targets unachieved until predeclared evidence supports them; exact identity remains disabled. Verify actual background-device receipt, recovery and duplicate prevention.

5. **Establish marketability.** Demonstrate useful leads and time saved against ordinary saved searches. Publish supportable coverage/freshness claims and degraded-service behavior. Record hosting eligibility, required provider decisions, retention/deletion responsibilities and an approved budget before public or paid launch. If these cannot coexist at $0, continue local development and revise scope.
