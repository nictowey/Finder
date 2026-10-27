# Private checkout operator checklist

This checklist separates secure entry from permission to pay. The cart-read
privacy correction was activated in `60992b2`; a real blank-form observation and
entry handoff were recorded at ledger64. The owner subsequently reported entry
complete, then a provider `SESSION_EXPIRED` message and the form disappearing.
Those later reports are user evidence, not an agent inspection or proof that
hidden/restored input values were cleared. No Pay action is recorded.

The late-completion correction described below is a local code change until
separately activated. It cannot change the old resident runtime's closure in
memory. Keep its privacy lock and browser untouched during implementation.

## Exact approved scope

- One copy of listing `1366950910`, release `3422100`, seller `Mailman21842`.
- Maximum **USD5.05 all-in**, including shipping and any mandatory charges.
- Poor media and Generic sleeve are accepted for this candidate only.
- No other purchase, subscription, paid add-on or higher total is authorized.
- Do not touch legacy test listing `4311669777` or enable monitoring/buy rules.

## Before secure entry

1. The owner completes the displayed challenge through the existing private
   viewer. Do not solve, refresh around or change routes around it without the
   applicable action-time authorization. Observe the normal result afterward.
2. Obtain fresh complete All/All Purchases history and record the truthful form
   disposition. Zero merchant orders does not prove absence of a provider draft.
3. Activate only the reviewed private image with profile, permissions, audit,
   access-stop history and paused monitoring preserved. Web deployment is a
   separate pending approval.
4. For later same-session continuation, choose the reviewed capability mode
   before entry. Actual payment authorization and confirmation files remain
   absent. Entry readiness does not require those files or final billing data.
5. Use one fresh bounded no-submit preparation, observe the actual blank form
   and Pay USD5.05 context, then append its observation. Never reuse a spent job
   or turn a simulated fixture into live evidence.
6. Wait for the owner to be available before the real entry handoff. Freeze and
   drain every CUA actor used for this browser. Stop manual screenshots, AX/DOM
   inspection, Inspector, page-content logging, traces and browser input.
7. Invoke the fixed `begin_secure_entry` command with the actual guest
   head/claim and expiry. This bounded grant is checked while beginning entry;
   it is not a deadline for the owner to finish typing. Only `entry_ready` with
   `reads_suspended: true` confirms the managed entry lock. Record the original
   entry reference/time and grant expiry; do not inspect the page afterward.

## During entry and completion

- The owner enters card/billing details directly in the private viewer and
  **does not press Pay**. Never request PAN, CVV, passwords or codes in chat.
- The provider defaults to "Ship to billing address"; the owner must check the
  actual delivery choice and displayed amount/notices. Do not assume that this
  matches the saved merchant destination after billing entry.
- Obtain an explicit completion report and final non-sensitive details. Send
  `complete_secure_entry` with its original token and genuine completion time;
  the report must still be fresh when delivered. The corrected path accepts
  entry lasting longer than the original begin grant. It checks only the
  unchanged anchored claim/head and access-stop metadata before releasing the
  entry lease once. It neither reads the page nor declares payment readiness.
- Grant expiry does not automatically release the privacy lock or renew any
  authority. Disconnect, restart, wrong token, changed metadata or cancellation
  blocks completion. Never change the original expiry or synthesize completion
  to unlock the browser. A retry must follow a verified known outcome.
- Provider-session expiry is separate from application grant expiry. An entry
  completion acknowledgement cannot renew a provider session. Stop repeated
  payment-control clicks and reinitialization; retain the original claim and
  unknown outcome. A user report that the form disappeared does not establish
  whether hidden or restored input values remain, or whether an order exists.

## Recovering an entered, expired session

This path does not use the earlier blank-form/normal-Close disposition.

1. Keep native inspection paused. Where the corrected runtime is installed,
   send only `cancel_secure_entry` with the original entry reference and fixed
   reason `provider_session_expired`. It invalidates completion and Pay while
   retaining the privacy lease; it does not resume sensitive reads. Priority
   cancellation can quarantine a completion waiting on metadata without clearing
   that operation's busy or uncertain latch.
2. Verify the actual original handoff receipt, issued scope, claim/head and boot,
   absence of Pay authorization/confirmation, and absence of a durable runtime
   Pay intent. The fixed retirement producer must establish these facts; hash
   shapes and a typed operator attestation alone are insufficient.
3. Retire the old browser context through the reviewed service stop. Verify
   termination using unit/container/PID metadata, without screenshots, DOM or
   field reads. The old `60992b2` runtime has no cancellation RPC: do not invent a
   successful cancellation or completion for it. Preserve the profile and all
   ledger records, and do not claim hidden values were erased.
4. On the corrected startup path, discard restored Page objects without reading
   their contents and establish a controlled blank page before exposing browser
   readers. A persistent-profile restart by itself is not blank-page evidence.
   Obtain fresh complete All/All Purchases reconciliation through the ordinary
   UI. Stop for any actual challenge or denial.
5. Append the distinct user-reported entered-session-expired disposition, with
   `card_data_entered:true`, the genuine provider-expiry/form-disappearance report
   times, and verified retirement metadata. Keep payment/order/provider-draft
   outcomes unverified. This disposition permits only a separately reviewed,
   fresh bounded no-submit preparation; it is not purchase authority.

## Before any payment

Require separate current authorization issued after entry completion, genuine post-completion amount,
delivery, selected method and visible terms/notices confirmation, fresh
same-document revalidation and the durable real-order fence. All must retain the
original entry document, guest claim/head and token. No payment input values are
read when metadata inspection resumes. A missing positive receipt is expected
before purchase; after submission, uncertain evidence retains `submission_unknown`
and forbids another submit while ordinary merchant reconciliation is performed.

## Privacy boundary verified offline

The managed read-only, guest and payment callers share one browser lease. Entry
drains outstanding quote reads before retaining that lease. The audit reproduced
a cart-inspector timeout returning while its underlying DOM operation remained
pending. Both ordinary and operator cart wrappers now track that actual read,
so their lease cannot release until it resolves or rejects. Four regressions
failed before the correction and passed afterward; live activation is pending.
Test fixtures make
payment-input getters and network payment unavailable. Expiry, cancellation,
browser loss and restart tests cover token invalidation and duplicate fencing.

The lease does **not** block independent native CUA, noVNC or Firefox DevTools.
CUA actor freezing is cooperative and must be accompanied by the operator pause
above; do not claim global screenshot prevention from `entry_ready` alone.
The memory logger may continue fixed cgroup counters; it has no page, screenshot,
card-input or address-content reader. No trace/video recording is configured in
the production launcher.

## Pending web UI deployment plan — not executed

After specific approval, confirm the existing `sniper-test.service` release,
interpreter, loopback port and drop-in against current state. Package a pinned
reviewed source revision without secrets; preserve `/var/lib/sniper/rules.db`,
saved rules and all browser/worker configuration. Upload only that release and
restart **the web unit only**. Check exact source revision and successful
loopback `/api/snapshot` within 15 seconds, paused scheduler, disabled purchases
and unchanged saved targets/rules. Roll back the web drop-in if health or these
assertions fail. Do not restart the browser, enable monitoring, create payment
metadata, alter authentication/network settings or treat UI status as live
payment evidence. Retained deployment scripts identify `/opt/sniper/releases`
and loopback port8080; these are planning references to reverify after approval.
