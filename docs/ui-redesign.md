# Review-first dashboard

The dashboard opens on the review inbox. Watches, saved verdict summaries and monitoring
remain separate, keyboard-accessible hash-linked views. The first view no longer requires
scrolling through operational logs. Listing cards place large seller images alongside the
price, prediction, evidence and independent owner decisions. Photos still come directly
from the allowlisted eBay image host and are never stored by the app.

## Decision behavior

- My pressing, Other version and Can't tell are identity judgments. Repeating the selected
  choice is harmless. Clearing a judgment is a separate, explicit action.
- Bought is an independent purchase marker. It never supplies a positive pressing label.
- A row locks all decision actions while saving. Acknowledged canonical server fields drive
  the selected state. Failed or unconfirmed saves show an error with a refresh/retry path.
- Stale dashboard requests cannot overwrite a newer save or filter request. Simultaneous
  saves to different rows are supported, including mixed success/failure.
- A save includes the prediction tier and evaluation timestamp displayed to the user.
  Changed evidence requires a new review instead of silently recording another prediction.
- Saved labels survive navigation and refresh. Purchase-only records appear in Judged.
  Verdict reports use mine/(mine+other); Can't tell remains outside the denominator.
- Original recorded judgment context is separate from the current prediction/evidence.
  Migrated timestamps are labeled Recorded, never asserted to be first-ever judgment times.

See the separate additive decision migration and its backend tests for compatibility,
legacy purchase-only records and preservation of judgment provenance.

## Offline preview

Run `node --import tsx scripts/preview_ui.ts` from the repository root. Open the resulting
`dist/finder-design-preview.html` in a browser at desktop and phone widths. The preview uses
the production HTML/CSS/script with an in-memory API adapter and synthetic SVG artwork.
It contains no real listings, credentials or backend connection. Preview changes reset when
the tab reloads. External merchant/catalog links are disabled in the preview.

The preview is for design review, not proof of API integration, model accuracy or a live
production deployment. DOM tests exercise the actual unmodified app script. Rendering in
an actual browser remains a separate validation step.

## Checks and release gates

Required repository gates remain Python tests, Ruff lint/format, Node tests and TypeScript.
The DOM suite additionally covers saved states, all three identity choices, purchase-only
records, explicit clear, same-row locking, failed saves, stale evidence, out-of-order reads,
concurrent mixed outcomes, view navigation, safe seller content and photo controls.

Before production deployment, run the isolated PostgreSQL migration/lock/backup checks and
inspect desktop and phone rendering and keyboard focus in an authenticated preview. The
private static design preview does not migrate or modify Finder production.
