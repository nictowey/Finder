"""Private rules UI with synthetic evaluation, manual reads and cloud monitor status."""

# Bundled HTML, CSS, and JavaScript retain their native line layout.
# ruff: noqa: E501

import html
import json
import re


def render_page(csrf_token: str, nonce: str, *, cloud=False) -> str:
    """Render escaped security tokens without recursively replacing their contents."""
    replacements = {
        "@@NONCE@@": html.escape(nonce, quote=True),
        "@@TOKEN@@": json.dumps(csrf_token)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026"),
    }
    page = _PAGE
    if cloud:
        page = (
            page.replace(
                "Purchasing disabled. See the exact-release read-only schedule below.",
                "Purchasing disabled. Read-only monitoring.",
            )
            .replace(
                "Nothing here searches Discogs, contacts a seller, places an order, or sends an alert.\n"
                "The optional read button fetches only the listing you supply.",
                "Cloud checks can track aggregate release availability and supplied listing IDs. "
                "Automatic listing discovery is not connected. No orders or email monitoring.",
            )
            .replace(
                "Local rules, synthetic tests, and optional manual listing reads. No background polling.",
                "Private server rules and read-only monitoring. Scheduled checks require an enabled "
                "cloud worker and durable hosting.",
            )
            .replace(
                "Local rules ready. No discovery or purchasing is running.",
                "Private rules ready. See Cloud monitoring for worker and source status.",
            )
        )
    return re.sub(r"@@(?:NONCE|TOKEN)@@", lambda match: replacements[match.group()], page)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Sniper · Discogs targets</title>
<style nonce="@@NONCE@@">
:root { color-scheme:light; --ink:#242b2c; --muted:#59676a; --line:#d8e0dc;
  --accent:#17654f; --paper:#f7f8f4; --tint:#eaf4ed; --error:#9a2538; }
* { box-sizing:border-box; } body { margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.55 system-ui,sans-serif; } main { max-width:1180px; margin:auto; padding:32px 24px 64px; }
header { display:flex; justify-content:space-between; align-items:start; gap:24px; margin-bottom:22px; }
h1,h2,h3,p { margin:0 0 12px; } h1 { font-size:36px; line-height:1.15; letter-spacing:-1.2px; }
h2 { font-size:22px; letter-spacing:-.4px; } h3 { font-size:17px; } .eyebrow { color:var(--accent);
  font-size:12px; font-weight:800; letter-spacing:1.8px; text-transform:uppercase; }
.muted,small { color:var(--muted); } small { display:block; margin-top:6px; font-size:12px; font-weight:400; }
.badge { display:inline-block; background:var(--tint); color:var(--accent); border:1px solid #c3d8ca;
  border-radius:30px; padding:6px 12px; font-size:12px; font-weight:750; white-space:nowrap; }
.notice { background:var(--tint); border:1px solid #c3d8ca; border-left:4px solid var(--accent);
  border-radius:10px; padding:17px 20px; } .notice strong { display:block; margin-bottom:4px; }
.notice p { margin:0; } .status { min-height:58px; padding:16px 2px; font-size:14px; font-weight:600;
  overflow-wrap:anywhere; } .error { color:var(--error); } .workspace { display:grid;
  grid-template-columns:minmax(0,1.15fr) minmax(0,1fr); align-items:start; gap:22px; }
.panel { background:white; border:1px solid var(--line); border-radius:14px; padding:24px; min-width:0; }
.panel > p { font-size:14px; } .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.full { grid-column:1/-1; } label { display:block; font-size:13px; font-weight:650; }
input,select,textarea { display:block; width:100%; margin:6px 0 0; border:1px solid #aebdb5;
  border-radius:7px; padding:11px; background:white; color:var(--ink); font:inherit; }
input,select { min-height:44px; } textarea { resize:vertical; min-height:360px;
  font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; }
.check { display:flex; gap:10px; align-items:start; } .check input { width:18px; height:18px; min-height:0; margin:3px 0 0; flex-shrink:0; }
input:focus,select:focus,textarea:focus,button:focus-visible,a:focus-visible { outline:3px solid #96cbb2; outline-offset:2px; }
a { color:var(--accent); overflow-wrap:anywhere; }
button { background:var(--accent); border:1px solid var(--accent); border-radius:7px; color:white;
  min-height:44px; padding:10px 15px; font:650 14px/1.4 system-ui,sans-serif; cursor:pointer; }
button.secondary { color:var(--accent); background:white; } button:disabled { opacity:.5; cursor:not-allowed; }
.actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:20px; }
.actions small { flex-basis:100%; margin:0; } .section-head { display:flex; align-items:start;
  justify-content:space-between; gap:15px; } .section-head h2 { margin-bottom:6px; }
#saved-summary { font-size:13px; } .target { padding:19px 0; border-top:1px solid var(--line); }
.target h3 { margin-bottom:3px; overflow-wrap:anywhere; } .target p { font-size:13px; margin-bottom:10px; }
.target dl,.result dl { display:grid; grid-template-columns:1fr 1.15fr; gap:5px 12px; margin:13px 0;
  font-size:13px; } dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
.target button { width:100%; margin-top:6px; } .empty { text-align:center; padding:30px 15px; color:var(--muted); }
.evaluator { margin-top:24px; } .evaluator-grid { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);
  gap:24px; align-items:start; margin-top:20px; } #observation-label { margin-top:16px; }
.result { background:var(--paper); border:1px solid var(--line); border-radius:10px; padding:20px; min-width:0; }
.result h3 { font-size:19px; } .result ul { margin:12px 0; padding-left:22px; }
.result li { margin-bottom:7px; overflow-wrap:anywhere; } .result .disclaimer { border-top:1px solid var(--line);
  padding-top:12px; margin:16px 0 0; font-size:13px; } #server-blockers { margin-top:12px; }
#server-blockers ul { margin:8px 0 0; padding-left:22px; } .footnote { margin:22px 0 0; font-size:12px; }
[hidden] { display:none !important; }
@media(max-width:850px) { .workspace,.evaluator-grid { grid-template-columns:1fr; } }
@media(max-width:540px) { main { padding:22px 15px 36px; } header { display:block; } header .badge { margin-top:4px; }
  h1 { font-size:30px; } .panel { padding:19px; } .grid { grid-template-columns:1fr; } .section-head { flex-wrap:wrap; }
  .actions button { flex:1 1 100%; } .target dl,.result dl { grid-template-columns:1fr; gap:2px; }
  dd { margin-bottom:7px; } textarea { min-height:420px; } }
</style>
</head>
<body><main>
<header><div><p class="eyebrow">Sniper / Discogs targets</p><h1>Your rules. Your limits.</h1>
<p class="muted">Save release targets, test your rules, and optionally read a known listing.</p></div>
<span class="badge">Private · no purchasing</span></header>
<div class="notice"><strong>Purchasing disabled. See the exact-release read-only schedule below.</strong>
<p>Nothing here searches Discogs, contacts a seller, places an order, or sends an alert.
The optional read button fetches only the listing you supply. Neither a listing read nor a synthetic test verifies a pressing or permits a purchase.</p></div>
<div id="server-blockers" class="notice" hidden></div>
<div id="status" class="status" role="status" aria-live="polite">Loading saved rules…</div>
<div class="workspace">
<section class="panel" aria-labelledby="target-title"><h2 id="target-title">1. Set a release target</h2>
<p class="muted">A release ID or link is saved as a reference only. No catalog details are fetched.
Saving the same release updates its rules.</p>
<form id="target-form"><div class="grid">
<label class="full">Discogs release ID or release link
<input id="release-id" name="release_id" type="text" required maxlength="500" autocomplete="off"
 placeholder="e.g. 123456 or a Discogs release link">
<small>Use a release, not a master. A pasted link will not be opened.</small></label>
<label class="full">Your label <span class="muted">(optional)</span>
<input id="label" name="label" type="text" maxlength="120" autocomplete="off" placeholder="Artist · album · edition note"></label>
<label>Minimum media grade<select id="min-media" name="min_media">
<option>P</option><option>F</option><option>G</option><option>G+</option><option>VG</option><option selected>VG+</option><option>NM</option><option>M</option>
</select></label>
<label>Minimum sleeve grade<select id="min-sleeve" name="min_sleeve">
<option>P</option><option>F</option><option>G</option><option>G+</option><option>VG</option><option selected>VG+</option><option>NM</option><option>M</option>
</select></label>
<label class="check full"><input id="allow-generic-sleeve" name="allow_generic_sleeve" type="checkbox">
<span>Allow a Generic sleeve for this release<small>Generic is separate from the minimum sleeve grade and is rejected unless checked.</small></span></label>
<label>Minimum seller positive %<input id="min-seller-percent" name="min_seller_percent" type="number" required min="0" max="100" step="0.01" value="99" inputmode="decimal"><small>Use 0 for no rating requirement.</small></label>
<label>Minimum feedback count<input id="min-feedback" name="min_feedback" type="number" required min="0" max="10000000" step="1" value="100" inputmode="numeric"><small>Use 0 for no feedback-count requirement.</small></label>
<label>Maximum total<input id="max-total" name="max_total" type="text" required inputmode="decimal" maxlength="10"
 pattern="[0-9]{1,7}([.][0-9]{1,2})?" placeholder="e.g. 50.00">
<small>Item + shipping + tax + fees. Your limit, not a market valuation.</small></label>
<label>Currency<select id="currency" name="currency"><option>USD</option><option>EUR</option><option>GBP</option><option>CAD</option><option>AUD</option></select>
<small>All amounts must use this currency. No conversion.</small></label>
<label>Destination country<input id="destination-country" name="destination_country" type="text" required pattern="[A-Za-z]{2}"
 minlength="2" maxlength="2" value="US" autocomplete="off" autocapitalize="characters" spellcheck="false">
<small>Two-letter country code.</small></label>
<label>Destination postal code<input id="destination-postal" name="destination_postal" type="text" required maxlength="16" pattern="[A-Za-z0-9 \-]{1,16}" autocomplete="off" spellcheck="false">
<small>Required before activating a rule. A paused draft can leave it blank.</small></label>
<label class="check full"><input id="enabled" name="enabled" type="checkbox" checked>
<span>Active target<small>Uncheck to pause this target and block rule eligibility. Scheduled reads also require an enabled cloud monitor.</small></span></label>
</div><div class="actions"><button type="submit" id="save-target" disabled>Save target rules</button>
<small id="draft-note">Refreshing saved state leaves these fields untouched.</small></div></form>
</section>
<section class="panel" aria-labelledby="saved-title"><div class="section-head"><div><h2 id="saved-title">2. Saved targets</h2>
<p id="saved-summary" class="muted">Waiting for local state.</p></div>
<button type="button" class="secondary" id="refresh">Refresh</button></div>
<p class="muted">Load a target to replace the fields on the left. Refresh never replaces your draft.</p>
<p id="hosted-schedule-status" class="muted">Exact-release browser schedule: checking stopped state.</p>
<p id="candidate-pipeline-status" class="muted">Candidate checks, one-copy cart rehearsal, and order lifecycle safeguards are fixture-tested only. Runtime purchase authorization is not configured. No hosted candidate worker, live checkout, or record purchasing is running. Read-only release acquisition status is shown separately below. A partial marketplace page cannot establish full availability or qualify a row by itself; each listing needs separate item and final delivered-quote checks.</p>
<section class="notice" aria-labelledby="checkout-readiness-title"><h3 id="checkout-readiness-title">Checkout readiness</h3>
<div id="checkout-readiness" aria-live="polite">Checkout status is unverified. Order submission remains disabled.</div></section>
<div id="targets" aria-live="polite"></div></section>
</div>
<section class="panel evaluator" aria-labelledby="monitor-release-title">
<h2 id="monitor-release-title">Monitor-only release IDs</h2>
<p class="muted">Save an exact Discogs release ID or release link without setting buying rules.
Saving an ID does not start the hosted browser schedule, create a quote, or permit a purchase.
Reviewed bounded-lease code covers only the release IDs shown below; this does not mean a lease is active
or has remaining reads. Other IDs remain drafts.</p>
<form id="monitor-release-form"><label for="monitor-release-id">Release ID or link</label>
<input id="monitor-release-id" required maxlength="500" autocomplete="off" placeholder="e.g. 28720177 or a Discogs release link">
<div class="actions"><button type="submit" id="save-monitor-release" disabled>Save for read-only monitoring</button></div></form>
<div id="monitor-releases" aria-live="polite"></div>
</section>
<section class="panel evaluator" aria-labelledby="change-alerts-title">
<h2 id="change-alerts-title">Listing changes for review</h2>
<p class="muted">In-app review only. No email, push message, cart action, or purchase is sent.
A listing change does not verify the pressing, availability, or delivered total.</p>
<div id="change-alert-list" aria-live="polite"></div>
</section>
<section id="monitor-panel" class="panel evaluator" aria-labelledby="monitor-title" hidden>
<h2 id="monitor-title">Cloud monitoring</h2>
<p id="monitor-status" class="notice"></p>
<p id="discovery-status" class="muted"></p>
<div class="actions"><button type="button" id="toggle-monitor">Start cloud reads</button>
<button type="button" id="monitor-once" class="secondary">Run one due check</button></div>
<p class="muted">Release availability is checked every 10 minutes; supplied listing IDs every 5 minutes.
The server keeps checking while this page is closed, when deployed and started. Refresh to see results.
No email or external notifications are sent. Checks can be delayed by rate limits or outages.</p>
<form id="track-form"><div class="grid">
<label>Saved release<select id="track-target" required><option value="">Save a target first</option></select></label>
<label>Known listing ID or link<input id="track-listing" required maxlength="500" autocomplete="off" placeholder="Discogs sell/item link"></label>
</div><div class="actions"><button type="submit" id="track-listing-button" class="secondary">Track this known listing</button></div>
<small>Tracking saves the reference. The first successful check must establish its reported release ID.
This does not discover other listings for the release.</small></form>
<div id="monitor-rows" aria-live="polite"></div>
<h3>Recent updates</h3><div id="monitor-events"></div>
</section>
<section class="panel evaluator" aria-labelledby="listing-title"><h2 id="listing-title">3. Read one known listing <span class="muted">(optional)</span></h2>
<p class="muted">Read-only live data, kept separate from the synthetic test below. Supply a listing ID or Discogs sell/item link you already know.
One explicit click reads that listing through the official API. There is no search, automatic refresh, checkout, or order action.</p>
<p id="listing-capability" class="notice">Checking whether manual listing reads are enabled on this server…</p>
<div class="evaluator-grid"><form id="listing-form">
<label>Saved target to compare<select id="listing-target" required><option value="">Save a target first</option></select></label>
<label class="actions" for="listing-input">Known listing ID or HTTPS Discogs sell/item link</label>
<input id="listing-input" name="listing" type="text" required maxlength="500" autocomplete="off"
 placeholder="e.g. 123456 or https://www.discogs.com/sell/item/123456">
<small>Only the listing reference is sent for this read. Do not enter credentials or payment details.</small>
<div class="actions"><button type="submit" id="read-listing" disabled>Read known listing once</button></div>
</form><div class="result" id="listing-result" role="status" aria-live="polite">
<h3>No live listing read yet</h3><p class="muted">Reported shipping is not a final destination quote. Tax, fees, and pressing evidence remain unverified.</p>
<p class="disclaimer">Purchasing remains disabled. Nothing is fetched until you press the read button.</p></div></div>
</section>
<section class="panel evaluator" aria-labelledby="test-title"><h2 id="test-title">4. Test a synthetic observation</h2>
<p class="muted">This is a made-up rule check, not a live listing check. Only source: "synthetic" is accepted.
Unknown amounts or missing evidence stay unknown and may block review.</p>
<div class="evaluator-grid"><form id="evaluate-form">
<label>Saved target to test<select id="evaluate-target" required><option value="">Save a target first</option></select></label>
<label id="observation-label" for="observation">Synthetic observation JSON</label>
<textarea id="observation" spellcheck="false" autocomplete="off" placeholder="Save a target, then load the synthetic example or type a made-up observation."></textarea>
<small>Maximum 64 KiB. Do not paste real listings, provider responses, credentials, or payment details.</small>
<div class="actions"><button type="submit" id="evaluate" disabled>Run synthetic test</button>
<button type="button" class="secondary" id="example" disabled>Load example (replace JSON)</button></div>
</form><div class="result" id="result" role="status" aria-live="polite">
<h3>No test run yet</h3><p class="muted">The result will show every reported blocker and the supplied total, if known.</p>
<p class="disclaimer">Purchasing remains disabled, whatever the result.</p></div></div>
</section>
<p class="footnote muted">Local rules, synthetic tests, and optional manual listing reads. No background polling. Unsaved edits exist in this page, not browser storage.
Save your rules before closing or reloading. No external scripts, images, or fonts are loaded.</p>
</main>
<script nonce="@@NONCE@@">
'use strict';
const csrf = @@TOKEN@@;
const el = (id) => document.getElementById(id);
const value = (id) => el(id).value.trim();
const maxBytes = 64 * 1024;
let snapshot = null;
let pending = false;
let needsRefresh = false;
let evaluationTarget = null;
let evaluationRevision = null;
let listingEvaluationTarget = null;
let listingEvaluationRevision = null;
const listingReadEnabled = () => snapshot?.capabilities?.listing_read_enabled === true;
function node(tag, text, className) {
  const result = document.createElement(tag); result.textContent = text;
  if (className) result.className = className; return result;
}
function message(text, error=false) {
  el('status').textContent = text; el('status').classList.toggle('error', error);
}
function updateButtons() {
  document.querySelectorAll('button').forEach((button) => { button.disabled = pending; });
  el('save-target').disabled = pending || !snapshot || needsRefresh;
  const noTarget = !snapshot || !snapshot.targets.some((target) => String(target.release_id) === el('evaluate-target').value);
  el('evaluate').disabled = pending || needsRefresh || noTarget;
  el('example').disabled = pending || noTarget;
  const noListingTarget = !snapshot || !snapshot.targets.some((target) => String(target.release_id) === el('listing-target').value);
  el('read-listing').disabled = pending || needsRefresh || noListingTarget || !listingReadEnabled();
  const monitor=snapshot?.monitor;
  el('toggle-monitor').disabled=pending || needsRefresh || monitor?.configured !== true;
  el('monitor-once').disabled=pending || needsRefresh || monitor?.configured !== true || snapshot?.kill_switch !== false;
  el('track-listing-button').disabled=pending || needsRefresh || !monitor || !el('track-target').value;
  document.querySelectorAll('.ownership').forEach((button) => { button.disabled = pending || needsRefresh; });
  el('save-monitor-release').disabled=pending || needsRefresh || snapshot?.hosted_schedule?.paused !== true;
  document.querySelectorAll('.monitor-release-toggle').forEach((button) => {
    button.disabled=pending || needsRefresh || snapshot?.hosted_schedule?.paused !== true;
  });
}
function setPending(next) {
  pending = next;
  el('target-form').setAttribute('aria-busy', String(next));
  el('evaluate-form').setAttribute('aria-busy', String(next));
  el('listing-form').setAttribute('aria-busy', String(next));
  el('monitor-release-form').setAttribute('aria-busy', String(next));
  el('targets').setAttribute('aria-busy', String(next));
  updateButtons();
}
async function request(path, body) {
  const controller = new AbortController();
  // Explicit listing review may include a second catalog GET after a bounded rate-budget wait.
  // Aborting the UI request does not prove server-side cancellation.
  const timeout = setTimeout(() => controller.abort(), path === '/api/listing' ? 25000 : 10000);
  try {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
      headers:body === undefined ? {} : {'Content-Type':'application/json', 'X-Finder-CSRF':csrf},
      body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal,
      cache:'no-store', credentials:'omit', mode:'same-origin', redirect:'error'});
    let data;
    try { data = await response.json(); }
    catch (_) { const error = new Error('The local server returned an unreadable response.'); error.status=response.status; throw error; }
    if (!response.ok) {
      const error = new Error(typeof data?.error === 'string' ? data.error : 'The local request was rejected.');
      error.status=response.status; throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function errorText(error) {
  if (error.name === 'AbortError') return 'The local request timed out.';
  if (error instanceof TypeError) return 'The local server could not be reached.';
  return error.message || 'The local request failed.';
}
function detail(list, label, text) { list.append(node('dt', label), node('dd', text)); }
function readable(text) { return String(text).replaceAll('_', ' '); }
function validateSnapshot(data) {
  if (!data || !Number.isSafeInteger(data.revision) || data.revision < 0 || !Array.isArray(data.targets) ||
      data.mode !== 'dry_run' || data.purchasing_enabled !== false || !Array.isArray(data.owned) ||
      data.owned.some((id) => !Number.isSafeInteger(id) || id < 1) ||
      data.targets.some((target) => !target || !Number.isSafeInteger(target.release_id) || target.release_id < 1)) {
    throw new Error('The server did not return a valid, purchasing-disabled dry-run state. Saving is paused.');
  }
  return data;
}
function renderSaved() {
  el('targets').replaceChildren();
  const targets = snapshot.targets;
  el('saved-summary').textContent = `${targets.length} saved ${targets.length === 1 ? 'target' : 'targets'} · revision ${snapshot.revision}`;
  const hosted=snapshot?.hosted_schedule;
  const enabledIds=Array.isArray(hosted?.monitor_releases) ?
    hosted.monitor_releases.filter(row=>row?.enabled === true).length : 0;
  const recorded=Array.isArray(hosted?.targets) ? hosted.targets.filter(row=>
    row && Number.isSafeInteger(row.release_id) &&
    Number.isFinite(row.last_attempt) && row.last_attempt > 0 && row.last_attempt < 8640000000000 &&
    ['baseline','changed','unchanged'].includes(row.last_status)) : [];
  recorded.sort((a,b)=>b.last_attempt-a.last_attempt);
  const last=recorded[0];
  const lastRead=last ? `Last stored complete read: release ${last.release_id}, ${last.last_status} at ${new Date(last.last_attempt*1000).toISOString()}.` :
    'No completed read is recorded.';
  const worker=hosted?.worker;
  const workerTime=(value)=>Number.isFinite(value) && value>0 && value<8640000000000 ?
    new Date(value*1000).toISOString() : 'an unknown time';
  const workerStatus=worker?.status==='stopped' ?
    `Separate worker stopped at ${workerTime(worker.stopped_at)}; reason: ${typeof worker.stop_reason==='string' && /^[a-z0-9_]{1,48}$/.test(worker.stop_reason) ? readable(worker.stop_reason) : 'unknown'}.` :
    worker?.status==='recent_heartbeat' ?
      `Separate worker reported a recent heartbeat at ${workerTime(worker.last_heartbeat_at)}. This is not independent proof that its process is still running.` :
    worker?.status==='heartbeat_stale' ?
      `Separate worker heartbeat is stale; last heard at ${workerTime(worker.last_heartbeat_at)}. Do not assume reads continue.` :
    worker?.status==='never_started' ?
      'No separate worker heartbeat has been recorded by this version.' :
      'Separate worker heartbeat status is unavailable.';
  el('hosted-schedule-status').textContent=hosted?.paused === true ?
    `Exact-release browser schedule: paused; ${enabledIds} monitor-only IDs enabled. No unattended browser reads. ${lastRead} ${workerStatus}` :
    hosted?.paused === false && hosted.kill_switch === true ?
      `Exact-release schedule is stopped by its safety control. ${lastRead} ${workerStatus}` :
    hosted?.paused === false && hosted.kill_switch === false ?
      `Exact-release schedule is resumed for ${enabledIds} monitor-only IDs. ${lastRead} ${workerStatus} Browser lease expiry and remaining reads are unverified here; a recent heartbeat does not verify future availability.` :
    'Exact-release browser schedule status is unavailable or unverified.';
  if (targets.length === 0) el('targets').append(node('p', 'No targets yet. Add your first set of rules.', 'empty'));
  targets.forEach((target) => {
    const card=node('article', '', 'target');
    card.append(node('h3', target.label || `Release ${target.release_id}`), node('p', `Discogs release ${target.release_id} · reference only`, 'muted'));
    const facts=document.createElement('dl');
    detail(facts, 'Minimum grades', `Media ${target.min_media ?? 'unknown'} / sleeve ${target.min_sleeve ?? 'unknown'}`);
    detail(facts, 'Generic sleeve', target.allow_generic_sleeve === true ? 'Allowed for this release' : 'Rejected');
    detail(facts, 'Seller threshold', `${target.min_seller_percent ?? 'unknown'}% positive · ${target.min_feedback ?? 'unknown'} feedback`);
    detail(facts, 'Maximum total', `${target.max_total ?? 'unknown'} ${target.currency ?? ''}`);
    detail(facts, 'Destination', target.destination_postal ?
      `${target.destination_country} ${target.destination_postal}` : 'Not set · paused draft');
    detail(facts, 'Target status', target.enabled === false ? 'Paused · blocks rule eligibility' : 'Active · rules only');
    const owned=snapshot.owned.includes(target.release_id);
    detail(facts, 'Already-owned marker', owned ? 'Yes · blocks rule eligibility' : 'Not marked');
    card.append(facts);
    const load=node('button', 'Load these rules into the form', 'secondary'); load.type='button';
    load.addEventListener('click', () => {
      if (pending) return;
      const fields = {'release-id':'release_id', label:'label', 'min-media':'min_media', 'min-sleeve':'min_sleeve',
        'min-seller-percent':'min_seller_percent', 'min-feedback':'min_feedback', 'max-total':'max_total',
        currency:'currency', 'destination-country':'destination_country', 'destination-postal':'destination_postal'};
      Object.entries(fields).forEach(([id, field]) => { el(id).value = target[field] ?? ''; });
      el('allow-generic-sleeve').checked=target.allow_generic_sleeve === true;
      el('enabled').checked=target.enabled !== false;
      syncDestinationRequirement();
      el('draft-note').textContent = `Editing rules for release ${target.release_id}. Saving updates that release; changing the ID saves a different target.`;
      message(`Loaded release ${target.release_id} into the form. No rules have been changed.`);
      el('release-id').focus();
    });
    const ownership=node('button', owned ? 'Clear already-owned marker' : 'Mark already owned', 'secondary ownership');
    ownership.type='button';
    ownership.addEventListener('click', () => persist('/api/owned', {release_id:target.release_id, owned:!owned},
      `Already-owned marker ${owned ? 'cleared' : 'saved'} for release ${target.release_id}. Purchasing remains disabled.`));
    card.append(load, ownership); el('targets').append(card);
  });
  ['evaluate-target','listing-target','track-target'].forEach((id) => {
    const selected=el(id).value;
    el(id).replaceChildren(node('option', targets.length ? 'Choose a saved target' : 'Save a target first'));
    el(id).firstElementChild.value='';
    targets.forEach((target) => {
      const option=node('option', `${target.release_id}${target.label ? ' · ' + target.label : ''}`);
      option.value=String(target.release_id); el(id).append(option);
    });
    if (targets.some((target) => String(target.release_id) === selected)) el(id).value=selected;
    else if (!selected && targets.length === 1) el(id).value=String(targets[0].release_id);
  });
  el('listing-capability').textContent=listingReadEnabled() ?
    'Manual listing reads are enabled. One listing is read only when you press the button; there is no automatic fetching.' :
    'Manual listing reads are disabled on this server. The optional --live-listing-read server flag enables this read-only feature. Synthetic tests remain available.';
  const blockers=Array.isArray(snapshot.blockers) ? snapshot.blockers : [];
  el('server-blockers').replaceChildren(); el('server-blockers').hidden=blockers.length === 0;
  if (blockers.length) {
    el('server-blockers').append(node('strong', 'Current limitations'));
    const list=document.createElement('ul'); blockers.forEach((reason) => list.append(node('li', readable(reason))));
    el('server-blockers').append(list);
  }
  if (evaluationTarget !== null && evaluationRevision !== snapshot.revision && !el('result-stale')) {
    el('result').querySelector('h3').textContent='Previous test is out of date';
    const warning=node('p', 'Saved rules or owned markers changed after this test. Run it again before relying on this result.', 'error');
    warning.id='result-stale'; el('result').prepend(warning);
  }
  if (listingEvaluationTarget !== null && listingEvaluationRevision !== snapshot.revision && !el('listing-result-stale')) {
    el('listing-result').querySelector('h3').textContent='Previous listing check is out of date';
    const warning=node('p', 'Saved rules or owned markers changed after this read. Read the listing again explicitly before relying on this comparison.', 'error');
    warning.id='listing-result-stale'; el('listing-result').prepend(warning);
  }
  renderMonitorReleases();
  renderCheckoutReadiness();
  renderInAppAlerts();
  renderMonitor();
  updateButtons();
}
function renderMonitorReleases() {
  const box=el('monitor-releases'); box.replaceChildren();
  const hosted=snapshot?.hosted_schedule;
  if (!hosted || !Array.isArray(hosted.monitor_releases) || hosted.monitor_releases.length > 100 ||
      hosted.monitor_releases.some((row) => !row || !Number.isSafeInteger(row.release_id) || row.release_id < 1 ||
        typeof row.enabled !== 'boolean')) {
    box.append(node('p', 'Monitor-only saved state is unavailable. No read has started.', 'error'));
    return;
  }
  const leaseIds=Array.isArray(hosted.reviewed_lease_release_ids) &&
    hosted.reviewed_lease_release_ids.length<=20 &&
    hosted.reviewed_lease_release_ids.every(id=>Number.isSafeInteger(id) && id>0) ?
      new Set(hosted.reviewed_lease_release_ids) : null;
  box.append(node('p', leaseIds ?
    `Reviewed bounded-lease scope: ${[...leaseIds].sort((a,b)=>a-b).join(', ')}. An active lease, its expiry and remaining reads are unverified here. Saving another ID does not extend it.` :
    'Reviewed browser lease scope is unavailable. Saved IDs are not proof of active coverage.', 'muted'));
  if (!hosted.monitor_releases.length) {
    box.append(node('p', 'No monitor-only releases saved. The schedule remains off.', 'muted'));
    return;
  }
  hosted.monitor_releases.forEach((row) => {
    const card=node('article', '', 'target');
    card.append(node('h3', `Release ${row.release_id}`),
      node('p', !leaseIds?.has(row.release_id) ?
        'Saved as a draft; the reviewed bounded-lease code does not cover this release.' :
        row.enabled && hosted.paused === true ? 'Saved for read-only checks; hosted schedule is still paused.' :
        row.enabled ? 'Enabled in saved configuration; an active lease and future reads are not verified here. Buying is disabled.' :
        'Monitor-only checks paused; a separate enabled buying rule may still request reads.', 'muted'));
    const button=node('button', row.enabled ? 'Pause monitor-only checks' : 'Enable monitor-only checks',
      'secondary monitor-release-toggle'); button.type='button';
    button.addEventListener('click', () => persist('/api/monitor-release',
      {release_id:row.release_id, enabled:!row.enabled},
      `Monitor-only release ${row.release_id} ${row.enabled ? 'paused' : 'enabled'}. Hosted schedule remains paused; purchasing disabled.`));
    card.append(button); box.append(card);
  });
}
function renderCheckoutReadiness() {
  const box=el('checkout-readiness'); box.replaceChildren();
  const state=snapshot?.checkout_readiness;
  if (!state || state.schema !== 'sniper_checkout_readiness_v1' ||
      state.purchase_allowed !== false || state.order_submission_enabled !== false ||
      state.enhanced_routes_to_legacy !== false || state.live_status_checked !== false ||
      state.legacy_helper !== 'offline_tested_disabled' ||
      state.manual_unpaid_order_creation !== 'unverified' ||
      !['unknown','legacy_v1','enhanced_v2'].includes(state.observed_checkout_kind) ||
      !['unknown','blocked_browser_security_challenge'].includes(state.payment_access)) {
    box.append(node('p', 'Checkout status is unavailable or unverified. Order submission remains disabled.'));
    return;
  }
  box.append(node('p', 'Legacy checkout helper: tested offline and disabled.'));
  box.append(node('p', state.observed_checkout_kind === 'enhanced_v2' ?
    'Last observed: enhanced checkout. Automatic orders through this flow are unverified and unavailable.' :
    state.observed_checkout_kind === 'legacy_v1' ?
      'Last observed: legacy checkout. Live order submission is unavailable.' :
      'The current checkout flow has not been verified.'));
  box.append(node('p', state.payment_access === 'blocked_browser_security_challenge' ?
    'Hosted browser payment access was blocked at a security challenge. This is the last recorded browser state, not an account limitation finding.' :
    'Hosted browser payment access is unverified.'));
  box.append(node('p', 'Creating an order for manual payment afterward is unverified. No order or payment action is enabled.'));
  if (Number.isFinite(state.observation_recorded_at) && state.observation_recorded_at > 0 &&
      state.observation_recorded_at < 8640000000000) {
    box.append(node('small', `Status recorded ${new Date(state.observation_recorded_at*1000).toISOString()}. No live payment check was made.`));
  }
}
function renderInAppAlerts() {
  const box=el('change-alert-list'); box.replaceChildren();
  const alerts=snapshot?.in_app_alerts;
  if (!alerts || alerts.channel !== 'in_app_only' || alerts.messages_sent !== 0 ||
      alerts.purchase_allowed !== false || !Array.isArray(alerts.pending) || alerts.pending.length > 500 ||
      !(alerts.control_revision === null || Number.isSafeInteger(alerts.control_revision))) {
    box.append(node('p', 'Change review status is unavailable. Refresh before acknowledging anything.', 'error'));
    return;
  }
  if (!alerts.pending.length) {
    box.append(node('p', 'No pending listing changes. This does not prove that monitoring is running or no copies are for sale.', 'muted'));
    return;
  }
  const valid=alerts.pending.every((change) =>
    change && Number.isSafeInteger(change.change_sequence) && change.change_sequence > 0 &&
    Number.isSafeInteger(change.release_id) && change.release_id > 0 &&
    /^[1-9][0-9]{0,15}$/.test(String(change.listing_id)) &&
    Number.isSafeInteger(Number(change.listing_id)) &&
    ['new','changed','disappeared','reappeared'].includes(change.kind) &&
    change.delivery === 'in_app_only' && change.purchase_allowed === false);
  if (!valid) {
    box.append(node('p', 'An alert was malformed. Refresh before reviewing changes.', 'error'));
    return;
  }
  alerts.pending.forEach((change) => {
    const card=node('article', '', 'target');
    card.append(node('h3', `${readable(change.kind)} · release ${change.release_id}`),
      node('p', `Listing ${change.listing_id} · observed ${change.observed_at || 'time unknown'}. Review only; no buying action.`, 'muted'));
    const link=node('a', 'Open listing on Discogs');
    link.href=`https://www.discogs.com/sell/item/${change.listing_id}`;
    link.target='_blank'; link.rel='noopener noreferrer'; card.append(link);
    const button=node('button', 'Mark reviewed', 'secondary alert-ack'); button.type='button';
    button.addEventListener('click', () => acknowledgeInAppAlert(change.change_sequence));
    card.append(button); box.append(card);
  });
}
async function acknowledgeInAppAlert(sequence) {
  if (pending || needsRefresh || !snapshot?.in_app_alerts) return;
  setPending(true); message('Recording in-app review…');
  try {
    await request('/api/change-alert/ack', {change_sequences:[sequence],
      expected_revision:snapshot.revision,
      expected_control_revision:snapshot.in_app_alerts.control_revision});
    needsRefresh=true; await refreshState();
    message('Listing change marked reviewed in this private app. No external message or purchase was sent.');
  } catch (error) {
    needsRefresh=true;
    message(`${errorText(error)} Review state may have changed. Refresh before trying again.`, true);
  } finally { setPending(false); }
}
function sourceLink(kind, reference, label) {
  if (!/^[1-9][0-9]{0,15}$/.test(String(reference)) || !Number.isSafeInteger(Number(reference))) return node('span', 'Source link unavailable');
  const link=node('a', label); link.href=`https://www.discogs.com/sell/${kind === 'release' ? 'release' : 'item'}/${reference}`;
  link.target='_blank'; link.rel='noopener noreferrer'; return link;
}
function timeLabel(timestamp) {
  return typeof timestamp === 'number' && Number.isFinite(timestamp) ? new Date(timestamp * 1000).toLocaleString() : 'Not yet';
}
function renderMonitor() {
  const monitor=snapshot?.monitor;
  el('monitor-panel').hidden=!monitor;
  if (!monitor) return;
  // A malformed response must not present an operating or purchasing-capable monitor.
  if (monitor.automatic_listing_discovery !== false || monitor.checkout_available !== false ||
      monitor.email_monitoring !== false || !Number.isSafeInteger(monitor.revision) ||
      !Array.isArray(monitor.rows) || !Array.isArray(monitor.events)) {
    throw new Error('Unverified monitoring response. Refresh before changing monitoring settings.');
  }
  el('monitor-status').textContent=monitor.configured !== true ?
    'Cloud polling is not configured. No scheduled provider checks are running.' :
    snapshot.kill_switch !== false ? 'Cloud reads are stopped. Saved targets are intact.' :
    monitor.worker_running !== true ? 'Read adapters are configured; the background worker is not running. Manual due checks only.' :
    'Cloud reads are enabled. Individual listing discovery is not connected. No checkout capability.';
  el('discovery-status').textContent=monitor.discovery_blocker;
  el('toggle-monitor').textContent=snapshot.kill_switch === false ? 'Stop cloud reads' : 'Start cloud reads';
  const rows=el('monitor-rows'); rows.replaceChildren();
  if (!monitor.rows.length) rows.append(node('p', 'No availability snapshots yet. A stopped monitor does not mean no copies are for sale.', 'empty'));
  monitor.rows.forEach((row) => {
    const card=node('article', '', 'target');
    card.append(node('h3', row.kind === 'release' ? `Release ${row.release_id} · availability only` : `Listing ${row.reference} · reported details`));
    const facts=document.createElement('dl');
    detail(facts, 'Check status', row.paused ? 'Target paused or owned' : readable(row.status));
    detail(facts, 'Last successful check', timeLabel(row.last_success));
    detail(facts, 'Next due', timeLabel(row.next_check));
    detail(facts, 'Fresh snapshot', row.fresh === true ? 'Yes' : 'No · availability is unverified');
    if (row.kind === 'release') {
      detail(facts, 'Reported listings', row.data?.num_for_sale ?? 'Unknown');
      detail(facts, 'Lowest item price only', reportedMoney(row.data?.lowest_item_price, row.data?.currency));
      detail(facts, 'Condition, seller and delivered total', 'Not supplied by this aggregate endpoint');
    } else {
      detail(facts, 'Reported release', known(row.data?.release_id));
      detail(facts, 'Media / sleeve', `${known(row.data?.media)} / ${known(row.data?.sleeve)}`);
      detail(facts, 'Seller', `${known(row.data?.seller_percent)}% · ${known(row.data?.feedback)} feedback`);
      detail(facts, 'Item price', reportedMoney(row.data?.item, row.data?.currency));
      detail(facts, 'All-in filter', row.review?.status === 'needs_total' ? 'Needs delivered total · does not qualify yet' : 'Blocked or not checked');
      detail(facts, 'Delivered total', 'Unknown · shipping, tax and fees unverified');
    }
    card.append(facts, sourceLink(row.kind, row.reference, 'Data provided by Discogs · open source'));
    if (row.review?.reasons?.length) {
      const list=document.createElement('ul'); row.review.reasons.forEach((reason) => list.append(node('li', reason))); card.append(list);
    }
    if (row.kind === 'listing') {
      const remove=node('button', 'Stop tracking this listing', 'secondary'); remove.type='button';
      remove.addEventListener('click', () => monitorAction('/api/monitor/track', {release_id:row.release_id, listing:row.reference,
        remove:true, expected_revision:snapshot.revision, monitor_revision:snapshot.monitor.revision}, 'Listing tracking removed.'));
      card.append(remove);
    }
    rows.append(card);
  });
  el('monitor-events').replaceChildren();
  if (!monitor.events.length) el('monitor-events').append(node('p', 'No recent updates. Discovery coverage remains incomplete.', 'muted'));
  monitor.events.forEach((event) => el('monitor-events').append(node('p',
    `${timeLabel(event.at)} · Release ${event.release_id} · ${event.kind === 'release' ? 'Availability snapshot updated; listing identities unknown' : 'Known listing details updated; delivered total still unverified'}`)));
}
async function monitorAction(path, body, success) {
  if (pending || needsRefresh || !snapshot?.monitor) return;
  setPending(true); message('Updating cloud monitoring…');
  try {
    const result=await request(path,body); needsRefresh=true; await refreshState();
    message(path === '/api/monitor/run' ? `Due check: ${readable(result.status)}. No checkout or payment action.` : success);
  } catch (error) {
    needsRefresh=true;
    message(`${errorText(error)} Refresh saved state before retrying; drafts are unchanged.`,true);
  } finally { setPending(false); }
}
async function refreshState() {
  const data=validateSnapshot(await request('/api/snapshot'));
  snapshot=data; needsRefresh=false; renderSaved();
}
function syncDestinationRequirement() {
  const active=el('enabled').checked;
  el('destination-country').required=active;
  el('destination-postal').required=active;
}
function targetFromForm() {
  const postal=value('destination-postal');
  const active=el('enabled').checked;
  const target={release_id:value('release-id'), label:value('label'), min_media:value('min-media'),
    min_sleeve:value('min-sleeve'), allow_generic_sleeve:el('allow-generic-sleeve').checked,
    min_seller_percent:Number(value('min-seller-percent')),
    min_feedback:Number(value('min-feedback')), max_total:value('max-total'), currency:value('currency'),
    destination_country:postal ? value('destination-country').toUpperCase() : null,
    destination_postal:postal || null, enabled:active};
  if (!target.release_id || (active && !postal)) throw new Error('Supply a release ID or link and, for active rules, a destination postal code.');
  if (!Number.isFinite(target.min_seller_percent) || !Number.isSafeInteger(target.min_feedback)) {
    throw new Error('Use a valid seller percentage and a whole feedback count.');
  }
  return target;
}
async function persist(path, body, success) {
  if (pending || !snapshot || needsRefresh) return;
  setPending(true); message('Saving the local change…');
  let accepted=false;
  try {
    await request(path, {...body, expected_revision:snapshot.revision}); accepted=true;
    needsRefresh=true; await refreshState();
    message(success);
    if (path === '/api/target') el('draft-note').textContent='Last submitted rules were saved. Any edits made while saving remain in this form.';
  } catch (error) {
    if (error.status === 409) {
      needsRefresh=true;
      try {
        await refreshState();
        message('Saved state changed in another tab. Your change was not saved and drafts are intact. Review the refreshed targets before trying again.', true);
      } catch (_) { message('Saved rules changed, and refreshing failed. Your draft is intact. Refresh successfully before saving again.', true); }
    } else if (accepted) {
      needsRefresh=true;
      message('The save was accepted, but saved state could not be verified. Your drafts are intact. Refresh before saving again.', true);
    } else if (!error.status || (error.status >= 200 && error.status < 300) || error.status >= 500) {
      needsRefresh=true;
      message(`${errorText(error)} The save outcome is unverified. Your drafts are intact. Refresh to check before retrying.`, true);
    } else { message(`${errorText(error)} Your drafts are intact.`, true); }
  } finally { setPending(false); }
}
el('target-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (pending || !snapshot || needsRefresh) return;
  let target;
  try { target=targetFromForm(); } catch (error) { message(errorText(error), true); return; }
  await persist('/api/target', {target}, 'Target rules saved. Purchasing remains disabled. Your form and JSON drafts are unchanged.');
});
el('enabled').addEventListener('change', syncDestinationRequirement);
el('monitor-release-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (pending || needsRefresh || snapshot?.hosted_schedule?.paused !== true) return;
  const release_id=value('monitor-release-id');
  if (!release_id || release_id.length > 500) {
    message('Supply one exact Discogs release ID or release link.', true); return;
  }
  await persist('/api/monitor-release', {release_id, enabled:true},
    'Monitor-only release saved. The hosted browser schedule remains paused; purchasing disabled.');
});
el('refresh').addEventListener('click', async () => {
  if (pending) return; setPending(true); message('Refreshing saved rules without changing your drafts…');
  try { await refreshState(); message('Saved state refreshed. Your form and JSON drafts are unchanged.'); }
  catch (error) { needsRefresh=true; message(`${errorText(error)} Your drafts are intact.`, true); }
  finally { setPending(false); }
});
el('evaluate-target').addEventListener('change', updateButtons);
el('listing-target').addEventListener('change', updateButtons);
el('track-target').addEventListener('change', updateButtons);
el('toggle-monitor').addEventListener('click', () => monitorAction('/api/kill',
  {killed:snapshot.kill_switch === false, expected_revision:snapshot.control_revision}, 'Cloud read controls updated.'));
el('monitor-once').addEventListener('click', () => monitorAction('/api/monitor/run', {}, 'Due check finished.'));
el('track-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (!snapshot?.monitor) return;
  const release_id=Number(value('track-target')), listing=value('track-listing');
  if (!Number.isSafeInteger(release_id) || release_id < 1 || !listing) return;
  monitorAction('/api/monitor/track', {release_id, listing, remove:false,
    expected_revision:snapshot.revision, monitor_revision:snapshot.monitor.revision}, 'Known listing reference saved. Its release still needs a successful check.');
});
el('example').addEventListener('click', () => {
  if (pending || !snapshot) return;
  const target=snapshot.targets.find((row) => String(row.release_id) === el('evaluate-target').value);
  if (!target) return;
  el('observation').value=JSON.stringify({source:'synthetic', listing_id:'synthetic-example-only', release_id:target.release_id,
    seller_comments:'Clean copy with light surface marks.', seller_comments_supported:true,
    release_media_metadata:{id:target.release_id,formats:[{name:'Vinyl',qty:'1'}]},
    media:'VG+', sleeve:'VG+', seller_percent:99.5, feedback:200, item:'20.00', shipping:'5.00', tax:'2.00', fees:'0.00',
    currency:target.currency, destination_country:target.destination_country, destination_postal:target.destination_postal,
    observed_at:new Date().toISOString(), available:true, pressing_evidence:'unknown'}, null, 2);
  message('Loaded made-up one-record example JSON, not actual release metadata. Pressing evidence is deliberately unknown. Nothing was imported or fetched.');
});
function renderResult(data, targetId, observation) {
  if (!data || typeof data.eligible_for_review !== 'boolean' || !Array.isArray(data.reasons) ||
      data.reasons.some((reason) => typeof reason !== 'string') || data.purchase_allowed !== false ||
      !(data.total === null || typeof data.total === 'string')) {
    throw new Error('The server returned an invalid dry-run result. No eligibility result can be trusted.');
  }
  const revisionMatches=Number.isSafeInteger(data.rules_revision) && data.rules_revision === snapshot.revision;
  const result=el('result'); result.replaceChildren();
  result.append(node('h3', !revisionMatches ? 'Rules version is unverified' :
    data.eligible_for_review ? 'Eligible for synthetic review' : 'Not eligible for review'));
  if (!revisionMatches) result.append(node('p', 'The result used a different or missing rules version. No eligibility decision is shown. Refresh saved state, then run the test again.', 'error'));
  result.append(node('p', `Synthetic test for release ${targetId}. This is not a live listing decision.`, 'muted'));
  const facts=document.createElement('dl');
  detail(facts, 'Reported total', data.total === null ? 'Unknown, not zero' : `${data.total} ${typeof observation.currency === 'string' ? observation.currency : '(currency unknown)'}`);
  detail(facts, 'Purchase allowed', 'No. Purchasing is disabled.'); result.append(facts);
  result.append(node('h3', data.reasons.length ? 'Reported reasons' : 'Reported blockers'));
  if (data.reasons.length) {
    const reasons=document.createElement('ul'); data.reasons.forEach((reason) => reasons.append(node('li', readable(reason)))); result.append(reasons);
  } else { result.append(node('p', revisionMatches ?
    'No blockers reported for this synthetic input. This does not verify a physical pressing.' :
    'No blockers were included in this response, but its rules version is unverified.')); }
  result.append(node('p', 'The result describes only the submitted JSON and saved rules at the time of this test. Edits or later rule changes require another test. Purchasing remains disabled.', 'disclaimer'));
  return revisionMatches;
}
el('evaluate-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (pending || !snapshot || needsRefresh) return;
  let observation, targetId;
  try {
    targetId=Number(value('evaluate-target'));
    if (!Number.isSafeInteger(targetId) || targetId < 1) throw new Error('Choose a saved release target.');
    const draft=el('observation').value;
    if (new TextEncoder().encode(draft).length > maxBytes) throw new Error('The synthetic JSON exceeds 64 KiB.');
    try { observation=JSON.parse(draft); } catch (_) { throw new Error('The observation is not valid JSON. Check commas, quotes, and braces.'); }
    if (!observation || Array.isArray(observation) || typeof observation !== 'object') throw new Error('Supply one JSON observation object.');
    if (observation.source !== 'synthetic') throw new Error('Only made-up observations with source: "synthetic" are allowed.');
  } catch (error) { message(errorText(error), true); return; }
  setPending(true); message('Checking the submitted synthetic observation…');
  el('result').replaceChildren(node('h3', 'Checking synthetic rules…'), node('p', 'Purchasing remains disabled.', 'muted'));
  try {
    const result=await request('/api/evaluate', {release_id:targetId, observation});
    const revisionMatches=renderResult(result, targetId, observation);
    evaluationTarget=revisionMatches ? targetId : null; evaluationRevision=revisionMatches ? result.rules_revision : null;
    if (revisionMatches) message(`Synthetic check finished for release ${targetId}. No live listing was checked and no purchase is allowed.`);
    else {
      needsRefresh=true;
      message('The result used different or unverified rules. Eligibility is not shown. Refresh saved state, then run the test again. Your drafts are intact.', true);
    }
  } catch (error) {
    evaluationTarget=null; evaluationRevision=null;
    el('result').replaceChildren(node('h3', 'No usable test result'), node('p', errorText(error), 'error'),
      node('p', 'Your JSON draft is unchanged. Purchasing remains disabled.', 'disclaimer'));
    message(`${errorText(error)} Your JSON draft is unchanged.`, true);
  } finally { setPending(false); }
});
function known(value) {
  return (typeof value === 'string' && value.trim()) || (typeof value === 'number' && Number.isFinite(value)) ? String(value) : 'Unknown';
}
function reportedMoney(amount, currency) {
  return known(amount) === 'Unknown' ? 'Unknown' : `${known(amount)} ${known(currency)}`;
}
function renderListingResult(data, targetId) {
  const listing=data?.listing, assessment=data?.evaluation;
  if (!listing || typeof listing !== 'object' || Array.isArray(listing) ||
      !assessment || typeof assessment.eligible_for_review !== 'boolean' || !Array.isArray(assessment.reasons) ||
      assessment.reasons.some((reason) => typeof reason !== 'string') || assessment.purchase_allowed !== false ||
      !(assessment.total === null || typeof assessment.total === 'string')) {
    throw new Error('The server returned an invalid read-only listing result. No comparison can be trusted.');
  }
  const revisionMatches=Number.isSafeInteger(assessment.rules_revision) && assessment.rules_revision === snapshot.revision;
  const result=el('listing-result'); result.replaceChildren();
  result.append(node('h3', !revisionMatches ? 'Rules version is unverified' :
    assessment.eligible_for_review ? 'Manual review only · purchase disabled' : 'Live listing does not pass the rules'));
  if (!revisionMatches) result.append(node('p', 'The result used a different or missing rules version. No eligibility decision is shown. Refresh saved state, then read this listing again explicitly.', 'error'));
  result.append(node('p', `One read-only listing snapshot compared with saved release ${targetId}. This is not continuous monitoring.`, 'muted'));
  const facts=document.createElement('dl');
  detail(facts, 'Listing ID', known(listing.listing_id));
  detail(facts, 'Reported release ID', known(listing.release_id));
  detail(facts, 'Media grade', known(listing.media));
  detail(facts, 'Sleeve grade', known(listing.sleeve));
  detail(facts, 'Seller positive %', known(listing.seller_percent));
  detail(facts, 'Seller feedback count', known(listing.feedback));
  detail(facts, 'Item price', reportedMoney(listing.item, listing.currency));
  detail(facts, 'Reported shipping (not a quote)', reportedMoney(listing.reported_shipping, listing.reported_shipping_currency));
  detail(facts, 'Reported shipping restriction', listing.shipping_blocked === true ? 'Shipping blocked' : listing.shipping_blocked === false ? 'Not reported blocked; destination quote still unknown' : 'Unknown');
  detail(facts, 'Availability', listing.available === true ? 'Reported available' : listing.available === false ? 'Reported unavailable' : 'Unknown');
  detail(facts, 'Destination-confirmed shipping', 'Unknown');
  detail(facts, 'Tax and mandatory fees', 'Unknown');
  detail(facts, 'Physical pressing evidence', 'Unverified');
  const completeness=assessment.media_completeness;
  detail(facts, 'Playable record set', completeness?.status === 'incomplete' ? 'Incomplete: blocked' :
    completeness?.status === 'reported_complete' ? 'Seller reports complete; not physically verified' :
    completeness?.status === 'no_disclosed_media_omission' ? 'No missing records disclosed; not physically verified' : 'Unknown: blocked');
  detail(facts, 'Expected vinyl count', known(completeness?.expected_media_count));
  const defects=assessment.media_defects;
  detail(facts, 'Playable-media damage', defects?.status === 'defective' ? 'Disclosed damage: blocked' :
    defects?.status === 'no_disclosed_media_defect' ? 'No damage disclosed; not physically verified' : 'Unknown: blocked');
  detail(facts, 'Evaluated all-in total', assessment.total === null ? 'Unknown, not zero' : reportedMoney(assessment.total, listing.currency));
  detail(facts, 'Purchase allowed', 'No. Purchasing is disabled.');
  result.append(facts);
  const urlMatch=typeof listing.url === 'string' ? listing.url.match(/^https:\/\/(?:www\.)?discogs\.com\/sell\/item\/([1-9]\d*)\/?$/) : null;
  if (urlMatch && urlMatch[1] === String(listing.listing_id)) {
    const link=node('a', 'Open this listing on Discogs'); link.href=listing.url; link.target='_blank'; link.rel='noopener noreferrer'; result.append(link);
  } else result.append(node('p', 'Listing link: Unknown', 'muted'));
  result.append(node('p', 'Reported shipping is not a final destination quote; tax, fees, and pressing evidence remain unverified.', 'notice'));
  if (typeof data.warning === 'string' && data.warning.trim()) result.append(node('p', data.warning, 'muted'));
  result.append(node('h3', 'Reported reasons'));
  if (assessment.reasons.length) {
    const reasons=document.createElement('ul'); assessment.reasons.forEach((reason) => reasons.append(node('li', readable(reason)))); result.append(reasons);
  } else result.append(node('p', 'No additional blockers were reported. This is not pressing verification, a final price quote, or purchase permission.'));
  result.append(node('p', 'Data provided by Discogs. A read-only snapshot can become outdated. Any later read requires another explicit click. Purchasing remains disabled.', 'disclaimer'));
  return revisionMatches;
}
el('listing-form').addEventListener('submit', async (event) => {
  event.preventDefault(); if (pending || !snapshot || needsRefresh || !listingReadEnabled()) return;
  const targetId=Number(value('listing-target')), listing=value('listing-input');
  if (!Number.isSafeInteger(targetId) || targetId < 1 || !snapshot.targets.some((target) => target.release_id === targetId)) {
    message('Choose a saved release target for the manual listing comparison.', true); return;
  }
  if (!listing || listing.length > 500) { message('Supply one listing ID or HTTPS Discogs sell/item link, up to 500 characters.', true); return; }
  setPending(true); message('Reading the one supplied listing through the local server…');
  el('listing-result').replaceChildren(node('h3', 'Reading one known listing…'), node('p', 'Read-only. No checkout or order action.', 'muted'));
  try {
    const data=await request('/api/listing', {release_id:targetId, listing});
    const revisionMatches=renderListingResult(data, targetId);
    listingEvaluationTarget=revisionMatches ? targetId : null;
    listingEvaluationRevision=revisionMatches ? data.evaluation.rules_revision : null;
    if (revisionMatches) message('One listing read completed. Destination shipping, tax, fees, and pressing evidence remain unverified. Purchasing is disabled.');
    else {
      needsRefresh=true;
      message('The listing comparison used different or unverified rules. Refresh saved state, then read it again explicitly. Your drafts are intact.', true);
    }
  } catch (error) {
    listingEvaluationTarget=null; listingEvaluationRevision=null;
    el('listing-result').replaceChildren(node('h3', 'No usable listing result'), node('p', errorText(error), 'error'),
      node('p', 'No automatic retry will run. Your listing input and other drafts are unchanged. Purchasing remains disabled.', 'disclaimer'));
    message(`${errorText(error)} No automatic retry will run. Your drafts are unchanged.`, true);
  } finally { setPending(false); }
});
(async () => {
  setPending(true);
  try { await refreshState(); message('Local rules ready. No discovery or purchasing is running.'); }
  catch (error) { needsRefresh=true; message(`${errorText(error)} Your drafts are intact. Use Refresh to try again.`, true); }
  finally { setPending(false); }
})();
</script>
</body></html>
"""
