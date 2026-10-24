"""Bundled, loopback-only Discogs target rules interface with synthetic evaluation."""

# Bundled HTML, CSS, and JavaScript retain their native line layout.
# ruff: noqa: E501

import html
import json
import re


def render_page(csrf_token: str, nonce: str) -> str:
    """Render escaped security tokens without recursively replacing their contents."""
    replacements = {
        "@@NONCE@@": html.escape(nonce, quote=True),
        "@@TOKEN@@": json.dumps(csrf_token)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026"),
    }
    return re.sub(r"@@(?:NONCE|TOKEN)@@", lambda match: replacements[match.group()], _PAGE)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Finder · Discogs targets</title>
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
input:focus,select:focus,textarea:focus,button:focus-visible { outline:3px solid #96cbb2; outline-offset:2px; }
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
<header><div><p class="eyebrow">Finder / Discogs targets</p><h1>Your rules. Your limits.</h1>
<p class="muted">Save release targets and try the rules with made-up observations.</p></div>
<span class="badge">Private · local dry run</span></header>
<div class="notice"><strong>Purchasing disabled. Rules only. No live discovery.</strong>
<p>Nothing here searches Discogs, contacts a seller, places an order, or sends an alert.
Passing a synthetic test is not pressing verification or permission to buy.</p></div>
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
<label>Minimum seller positive %<input id="min-seller-percent" name="min_seller_percent" type="number" required min="0" max="100" step="0.01" value="99" inputmode="decimal"></label>
<label>Minimum feedback count<input id="min-feedback" name="min_feedback" type="number" required min="0" max="10000000" step="1" value="100" inputmode="numeric"></label>
<label>Maximum total<input id="max-total" name="max_total" type="text" required inputmode="decimal" maxlength="10"
 pattern="[0-9]{1,7}([.][0-9]{1,2})?" placeholder="e.g. 50.00">
<small>Item + shipping + tax + fees. Your limit, not a market valuation.</small></label>
<label>Currency<select id="currency" name="currency"><option>USD</option><option>EUR</option><option>GBP</option><option>CAD</option><option>AUD</option></select>
<small>All amounts must use this currency. No conversion.</small></label>
<label>Destination country<input id="destination-country" name="destination_country" type="text" required pattern="[A-Za-z]{2}"
 minlength="2" maxlength="2" value="US" autocomplete="off" autocapitalize="characters" spellcheck="false">
<small>Two-letter country code.</small></label>
<label>Destination postal code<input id="destination-postal" name="destination_postal" type="text" required maxlength="16" pattern="[A-Za-z0-9 \-]{1,16}" autocomplete="off" spellcheck="false">
<small>Stored locally to check the supplied destination.</small></label>
<label class="check full"><input id="enabled" name="enabled" type="checkbox" checked>
<span>Active target<small>Uncheck to pause this target and block synthetic review. Nothing runs automatically.</small></span></label>
</div><div class="actions"><button type="submit" id="save-target" disabled>Save target rules</button>
<small id="draft-note">Refreshing saved state leaves these fields untouched.</small></div></form>
</section>
<section class="panel" aria-labelledby="saved-title"><div class="section-head"><div><h2 id="saved-title">2. Saved targets</h2>
<p id="saved-summary" class="muted">Waiting for local state.</p></div>
<button type="button" class="secondary" id="refresh">Refresh</button></div>
<p class="muted">Load a target to replace the fields on the left. Refresh never replaces your draft.</p>
<div id="targets" aria-live="polite"></div></section>
</div>
<section class="panel evaluator" aria-labelledby="test-title"><h2 id="test-title">3. Test a synthetic observation</h2>
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
<p class="footnote muted">Local rules and synthetic tests only. Unsaved edits exist in this page, not browser storage.
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
  document.querySelectorAll('.ownership').forEach((button) => { button.disabled = pending || needsRefresh; });
}
function setPending(next) {
  pending = next;
  el('target-form').setAttribute('aria-busy', String(next));
  el('evaluate-form').setAttribute('aria-busy', String(next));
  el('targets').setAttribute('aria-busy', String(next));
  updateButtons();
}
async function request(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
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
  if (targets.length === 0) el('targets').append(node('p', 'No targets yet. Add your first set of rules.', 'empty'));
  targets.forEach((target) => {
    const card=node('article', '', 'target');
    card.append(node('h3', target.label || `Release ${target.release_id}`), node('p', `Discogs release ${target.release_id} · reference only`, 'muted'));
    const facts=document.createElement('dl');
    detail(facts, 'Minimum grades', `Media ${target.min_media ?? 'unknown'} / sleeve ${target.min_sleeve ?? 'unknown'}`);
    detail(facts, 'Seller threshold', `${target.min_seller_percent ?? 'unknown'}% positive · ${target.min_feedback ?? 'unknown'} feedback`);
    detail(facts, 'Maximum total', `${target.max_total ?? 'unknown'} ${target.currency ?? ''}`);
    detail(facts, 'Destination', `${target.destination_country ?? 'unknown'} ${target.destination_postal ?? 'unknown'}`);
    detail(facts, 'Target status', target.enabled === false ? 'Paused · blocks synthetic review' : 'Active · rules only');
    const owned=snapshot.owned.includes(target.release_id);
    detail(facts, 'Already-owned marker', owned ? 'Yes · blocks synthetic review' : 'Not marked');
    card.append(facts);
    const load=node('button', 'Load these rules into the form', 'secondary'); load.type='button';
    load.addEventListener('click', () => {
      if (pending) return;
      const fields = {'release-id':'release_id', label:'label', 'min-media':'min_media', 'min-sleeve':'min_sleeve',
        'min-seller-percent':'min_seller_percent', 'min-feedback':'min_feedback', 'max-total':'max_total',
        currency:'currency', 'destination-country':'destination_country', 'destination-postal':'destination_postal'};
      Object.entries(fields).forEach(([id, field]) => { el(id).value = target[field] ?? ''; });
      el('enabled').checked=target.enabled !== false;
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
  const selected=el('evaluate-target').value;
  el('evaluate-target').replaceChildren(node('option', targets.length ? 'Choose a saved target' : 'Save a target first'));
  el('evaluate-target').firstElementChild.value='';
  targets.forEach((target) => {
    const option=node('option', `${target.release_id}${target.label ? ' · ' + target.label : ''}`);
    option.value=String(target.release_id); el('evaluate-target').append(option);
  });
  if (targets.some((target) => String(target.release_id) === selected)) el('evaluate-target').value=selected;
  else if (!selected && targets.length === 1) el('evaluate-target').value=String(targets[0].release_id);
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
  updateButtons();
}
async function refreshState() {
  const data=validateSnapshot(await request('/api/snapshot'));
  snapshot=data; needsRefresh=false; renderSaved();
}
function targetFromForm() {
  const target={release_id:value('release-id'), label:value('label'), min_media:value('min-media'),
    min_sleeve:value('min-sleeve'), min_seller_percent:Number(value('min-seller-percent')),
    min_feedback:Number(value('min-feedback')), max_total:value('max-total'), currency:value('currency'),
    destination_country:value('destination-country').toUpperCase(), destination_postal:value('destination-postal'),
    enabled:el('enabled').checked};
  if (!target.release_id || !target.destination_postal) throw new Error('Supply a release ID or link and destination postal code.');
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
el('refresh').addEventListener('click', async () => {
  if (pending) return; setPending(true); message('Refreshing saved rules without changing your drafts…');
  try { await refreshState(); message('Saved state refreshed. Your form and JSON drafts are unchanged.'); }
  catch (error) { needsRefresh=true; message(`${errorText(error)} Your drafts are intact.`, true); }
  finally { setPending(false); }
});
el('evaluate-target').addEventListener('change', updateButtons);
el('example').addEventListener('click', () => {
  if (pending || !snapshot) return;
  const target=snapshot.targets.find((row) => String(row.release_id) === el('evaluate-target').value);
  if (!target) return;
  el('observation').value=JSON.stringify({source:'synthetic', listing_id:'synthetic-example-only', release_id:target.release_id,
    media:'VG+', sleeve:'VG+', seller_percent:99.5, feedback:200, item:'20.00', shipping:'5.00', tax:'2.00', fees:'0.00',
    currency:target.currency, destination_country:target.destination_country, destination_postal:target.destination_postal,
    observed_at:new Date().toISOString(), available:true, pressing_evidence:'unknown'}, null, 2);
  message('Loaded made-up example JSON with the current time. Pressing evidence is deliberately unknown. Nothing was imported or fetched.');
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
(async () => {
  setPending(true);
  try { await refreshState(); message('Local rules ready. No discovery or purchasing is running.'); }
  catch (error) { needsRefresh=true; message(`${errorText(error)} Your drafts are intact. Use Refresh to try again.`, true); }
  finally { setPending(false); }
})();
</script>
</body></html>
"""
