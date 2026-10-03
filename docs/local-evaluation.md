# Frozen local case evaluation

`finder-evaluate` checks agreement with synthetic or user-authored case expectations.
It uses the same validated local v1/v2 inputs and the same `assess_review` path as
`finder-local`. It does not change classification or estimate prices. No account,
provider, database, notification, hosted job, or paid service is used.

This is **case agreement**, not evidence of physical pressing identity, market
precision, catalog coverage, time saved, or readiness for launch. A successful command
means an artifact was scored. It does not mean the matcher is accurate. Invented cases
can expose a regression but cannot establish real-market performance.

## Two distinct commands

The supported bounded unit is one target family, up to 20 authored alternative profiles
in v2, and 1–100 unique listing IDs. Use fresh files for each run. Repeated observation IDs
are accepted by the local workspace but rejected here: each evaluation case is counted
exactly once. There is no multi-family suite or benchmark service in this milestone.

With the already-installed environment, run from the repository root:

```sh
PYTHONPATH=src python -m finder.local_evaluation predict \
  --input cases.json \
  --assessed-at 2026-10-03T05:00:00Z \
  --output predictions-1.json

PYTHONPATH=src python -m finder.local_evaluation score \
  --predictions predictions-1.json \
  --answers answers-1.json \
  --output report-1.json
```

The equivalent installed console command is `finder-evaluate`. No installation or
network access is necessary when using the existing environment and `PYTHONPATH=src`.
Both commands reject live Finder/provider/database configuration and credential-bearing
environment variables. Do not supply or load a dotenv file.

`predict` has no answer argument. It validates metadata, runs the matcher, and freezes
results before any answer manifest is opened. Its runtime boundary rejects undeclared
JSON reads, database connections, network/DNS, subprocesses, dotenv access, and unrelated
writes. `score` reads the already-frozen prediction first, checks its integrity, then
opens the separately authored answers. It never calls the matcher or reinterprets input
with the current classifier. Do not use UI verdicts as independent expected truth: no
workspace, history export, or verdict import is supported.

Command separation alone cannot prove that the case author was blind to predictions.
Prefer writing the case design, labels, and expectations before inspecting output, then
copy only the resulting input digest into the manifest. If the UI informed an answer,
mark its provenance `ui_assisted_judgment`. A digest is a binding, not a certificate of
independent authorship or a held-out evaluation.

`--assessed-at` is a required timestamp with an explicit timezone. The artifact stores
its UTC equivalent and `clock_semantics: "fixed_simulation_clock"`. Staleness, auction
windows, ended listings, and price eligibility are simulated at this replay clock. It
is not the wall clock and does not certify current availability. Observations later than
this clock fail validation. The ordinary review UI continues using its live clock.

## Small synthetic input

The input schema is the existing local import bundle. This v2 example supplies one
alternative; coverage is always incomplete even if the supplied alternative is ruled
out. A v1 bundle, or v2 without alternatives, preserves unchecked-alternative uncertainty.
Money values are decimal strings. Missing shipping is unknown, not zero.

```json
{
  "schema_version": 2,
  "source": "synthetic",
  "target": {
    "artist": "Example Ensemble",
    "album": "Offline Horizons",
    "colors": ["Blue"]
  },
  "alternatives": [{
    "id": "black-profile",
    "artist": "Example Ensemble",
    "album": "Offline Horizons",
    "colors": ["Black"]
  }],
  "settings": {
    "maximum_subtotal": "30.00",
    "gamble_max": "15.00",
    "country": "US",
    "postal_code": "00000",
    "tells": [{"kind": "color", "value": "Blue", "required": true}]
  },
  "listings": [{
    "id": "blue-case",
    "title": "Example Ensemble Offline Horizons blue vinyl",
    "observed_at": "2026-10-03T04:50:00Z",
    "details_observed_at": "2026-10-03T04:50:00Z",
    "current_price": "10.00",
    "currency": "USD",
    "shipping_cost": "4.00",
    "shipping_currency": "USD",
    "price_kind": "fixed_price",
    "delivery_country": "US",
    "delivery_postal_code": "00000"
  }]
}
```

Use only invented or manually authored development cases. Do not import owner records,
provider responses, photos, URLs, credentials, or production data. Additional fields such
as an expected label in the observation bundle fail validation. Input files are bounded
at 256 KiB; artifacts at 4 MiB. Duplicate JSON keys and nonfinite numbers are rejected.

## Separate answer manifest

Write the answers yourself. The program never generates answers from predictions. Copy
the 64-character `input_digest` printed by `predict` into this separate manifest; the
placeholder below is intentionally invalid until replaced.

```json
{
  "schema_version": 1,
  "kind": "finder-local-answers",
  "answer_version": "case-design-1",
  "input_digest": "REPLACE_WITH_THE_FROZEN_INPUT_DIGEST",
  "coverage": "complete",
  "answers": [{
    "id": "blue-case",
    "declared_identity": "target",
    "provenance": {
      "kind": "synthetic_case_design",
      "detail": "Invented blue target and black alternative."
    },
    "rationale": "The authored case declares the blue record to be the target.",
    "expected_status": "possible_pressing",
    "expected_eligible": true,
    "required_evidence_codes": ["seller_color_claim"],
    "forbidden_evidence_codes": ["color_conflict"]
  }]
}
```

The exact contract is defined by `AnswerManifest`, `Answer`, and `Provenance` in
`src/finder/local_evaluation.py`:

- `answer_version` is a required author-controlled revision, separate from schema version
- `declared_identity` is `target`, `other`, `unresolved`, or `unlabeled`. These are authored
  labels, not facts inferred by the matcher or from a purchase
- Provenance kind is `synthetic_case_design`, `user_authored_case`, or
  `ui_assisted_judgment`; detail and rationale are required plain text, at most 500
  characters each, with no URLs, credentials, or control characters
- `coverage: "complete"` requires every frozen case ID, including an explicit
  `unlabeled` entry where needed. `partial` permits omitted IDs; they remain in every
  applicable denominator as missing/unlabeled and are listed in the report
- Unknown or duplicate case IDs, digest mismatch, unexpected fields, and unsupported
  schema versions fail before a report is written
- `expected_status` optionally asserts one exact tier: `possible_pressing`,
  `family_review`, `conflicting`, or `unrelated`
- `expected_eligible` optionally asserts the simulated `review.notify` boolean. It is
  separate from identity and never causes an actual alert
- Required/forbidden evidence codes assert membership in the union of `review.clues`
  and `review.verify`. A code cannot be both required and forbidden. Raw comparison
  fields and matched booleans are preserved as diagnostics, not identity labels

A declared target can correctly receive an unclear tier because its text is incomplete.
Labels describe the case design; optional expectations describe intended policy behavior.
These two questions must remain distinct.

## Frozen artifact and integrity

Predictions contain normalized canonical input, its SHA-256 digest, explicit UTC replay
clock, every case's full review and comparison evidence, unknowns/verification codes,
price inputs and subtotal basis, thresholds, and the scope of supplied alternatives.
No supplied profile set is treated as a complete catalog. Subtotals exclude tax, duties,
and fees; bid prices remain current bids and unknown shipping remains unknown.

Canonical JSON sorts object keys, uses UTF-8 without extra whitespace, and rejects
NaN/Infinity. Input normalization comes from the shared validation models: default fields
are explicit, amounts/times are normalized, alternatives follow the adapter's ordering,
and listing order is preserved. The input digest binds all these normalized fields.

Implementation provenance records the exact review and matching policy strings, Python
and Pydantic versions, and a SHA-256 manifest for **every `.py` file under the installed
`finder` package directory**, including this harness and the local adapter. The source
digest binds that manifest. It does not cover SQL/configuration files, third-party package
source, the interpreter binary, environment/OS, or author behavior. It is not a signature
or proof of the complete runtime. Source edits cause new freezes to carry a new digest;
existing freezes remain scorable without rerunning a changed matcher.

Each prediction/report additionally hashes its payload, excluding its own digest field.
Scoring checks prediction, canonical-input, and source-manifest digests and case
consistency. This detects accidental edits; a person who deliberately rewrites a payload
and its hashes can forge an artifact. It is not a tamper-proof audit system.

Outputs use exclusive creation, local paths, and read-only file mode `0400`. Existing
inputs, predictions, answers, and reports are never overwritten. Symlink and dotenv paths
are rejected. Use a fresh answer filename/revision and report filename for corrections.
Changing answers cannot change frozen predictions. Keep a partial output if a write
fails; choose another fresh path to retry. The runtime guard protects this trusted local
development path against regressions; it is not an OS sandbox for hostile code or
concurrent hostile filesystem changes.

## Reading reports without inflating agreement

Reports carry the source/policy/input/prediction/answer digests, full canonical input,
per-case original prediction and answer provenance, explicit expected-versus-actual
checks, and every mismatch. The full four-tier by four-declared-identity matrix includes
zero cells, unresolved labels, and missing/unlabeled cases.

Counts and ratios are deliberately narrow:

| Report measure | Numerator / denominator |
| --- | --- |
| Abstentions | `family_review` cases / all cases |
| Supported-positive agreement | declared targets in `possible_pressing` / target-or-other labels in `possible_pressing` |
| Declared-target recognition | declared targets in `possible_pressing` / all declared targets |
| False review leads | declared others in either `possible_pressing` or `family_review` / target-or-other labels in those two tiers |
| Excluded declared positives | declared targets in `conflicting` or `unrelated` / all declared targets |
| Expectation agreement | satisfied explicit assertions / all explicit assertions |

Every ratio includes its integer numerator and denominator. An empty denominator is
`null`, never 1 or 100%. Supported-positive and review-lead undecided counts are shown
separately. Declared-target recognition shows how many authored targets reached the
supported tier, so positive agreement cannot hide low recognition; it is a synthetic
case diagnostic, not market recall. With no decided labels, `identity_outcome` is `not_evaluated`; all-unresolved
or all-unlabeled inputs cannot produce perfect identity agreement. An unresolved author's
label is not an expected `family_review` assertion unless separately stated.

`review_lead_cases` is a candidate-tier count representing potential case-review burden,
not actual inbox traffic or minutes saved. `abstained_declared_positives` stays separate
from confidently excluded targets, and `supported_declared_other_cases` exposes confident
positive disagreements. Eligibility is reported separately in `simulated_qualification`:
qualifying cases, declared-other qualifiers, and nonqualifying declared targets. A target
outside its price cap or with stale details is not counted as an identity false exclusion.
The detailed reason codes explain the gate that blocked it.

UI-assisted answers are counted explicitly and retain their provenance per case; no
aggregate claims independent truth. All ratios remain synthetic/user-authored agreement.
Country/year or other diagnostic field matches are never counted as correct identities.

The command exits `0` when scoring completes with no explicit assertion failures, `1`
when one or more explicit expectations fail (the failing report is still saved), and `2`
for invalid inputs or a safety/file failure. Zero assertions produce
`expectation_outcome: "not_evaluated"`, `expectations_passed: null`, and null expectation
agreement. Exit `0` does not imply identity correctness; the CLI prints the supported-other
and excluded-target counts. Even passing every expectation is only agreement with the
author's assertions.

## Offline checks

```sh
PYTHONPATH=src python -m pytest -q tests/test_local_evaluation.py
ruff check src/finder/local_evaluation.py tests/test_local_evaluation.py
ruff format --check src/finder/local_evaluation.py tests/test_local_evaluation.py
```

Tests exercise the shared adapter, replay reproducibility, no answer reads during
prediction, no policy calls while scoring, answer edits without prediction changes,
wrong assertions, empty/missing label denominators, identity-versus-price separation,
full matrices, unknown shipping, file preservation, environment refusals, and blocked
network/database/dotenv/subprocess activity. Full repository checks and independent review
remain required before publication; these synthetic checks are not real-market validation.
