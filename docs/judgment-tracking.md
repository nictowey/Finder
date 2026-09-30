# Saved judgment tracking

The dashboard's `accuracy` rows group current explicit identity labels by their first
available recorded prediction. They are not current model tiers or a representative accuracy
sample. Editing or clearing a label retains its provenance. Purchase-only and cleared identity
records do not enter the identity denominator; unsure is reported separately from resolved
mine/other judgments.

`filter=judged` includes every saved identity or purchase marker, including dismissed and
unavailable listings. Dismissal still excludes the item from the ordinary review inbox and
does not erase its judgment. Mandatory seller/watch deletion still cascades normally.

Judged accepts `judgment=all|mine|other|unsure` and `purchased=all|yes`. Nondefault values require
the Judged view. Both filters combine with AND before keyset pagination. Its price scope does
not hide saved history. Responses include:

- `judged_counts`: stable whole saved-set counts for all, mine, other, unsure and purchased,
  independent of selected category, purchase filter, price or page
- `filtered_total`: count after both selected Judged filters, before pagination; null in
  other views
- `decision_totals`: global explicit judged/mine/other/unsure, purchased, purchase_only and
  dismissed_judged counts. These include null/unrecognized historical prediction tiers

The counts use canonical decisions, not compatibility alert-suppression rows. Database
foreign keys link retained decisions to inbox/listing/watch records; required deletion removes
the linked history instead of leaving unbrowseable orphan decisions.

Synthetic actual-PostgreSQL-engine API regressions cover more than one page, dismissed and
unavailable stale rows above a price ceiling, purchase-only and identity intersections, clear
records, unknown historical tiers and invalid filters. All three regression scenarios fail
against the prior API and pass after the change. These tests do not establish live labeling
accuracy or assert that previously saved owner labels were lost.
