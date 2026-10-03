"""Bundled interface for the separate local development adapter."""

# Bundled HTML, CSS, and JavaScript retain their native line layout.
# ruff: noqa: E501

import html
import json


def render_page(csrf_token: str, nonce: str) -> str:
    return _PAGE.replace("@@NONCE@@", html.escape(nonce, quote=True)).replace(
        "@@TOKEN@@", json.dumps(csrf_token).replace("<", "\\u003c")
    )


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finder · Local review</title>
<style nonce="@@NONCE@@">
:root { color-scheme: light; --ink:#262331; --muted:#64616c; --line:#dedbe5;
  --accent:#5143a5; --paper:#fcfbf8; --tint:#f0eef9; }
* { box-sizing:border-box; } body { margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.55 system-ui,sans-serif; } main { max-width:1120px; margin:auto; padding:30px 24px 64px; }
header { display:flex; justify-content:space-between; gap:24px; align-items:start; padding-bottom:22px; }
h1,h2,h3,p { margin:0 0 12px; } h1 { font-size:34px; letter-spacing:-1px; } h2 { font-size:22px; }
h3 { font-size:18px; } .eyebrow { color:var(--accent); font-size:12px; font-weight:800;
  letter-spacing:1.8px; text-transform:uppercase; } .muted,small { color:var(--muted); }
.notice { background:var(--tint); border:1px solid var(--line); padding:16px 18px; border-radius:12px; }
section { margin-top:24px; } .panel,.case { background:white; border:1px solid var(--line);
  border-radius:14px; padding:22px; } .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
label { display:block; font-size:14px; font-weight:600; } input,select,textarea { display:block;
  width:100%; background:white; color:var(--ink); border:1px solid #b9b5c7; border-radius:7px;
  padding:10px; margin:5px 0 0; font:inherit; } textarea { min-height:210px; resize:vertical;
  font:13px/1.5 ui-monospace,monospace; } input:focus,textarea:focus,select:focus,button:focus-visible,a:focus-visible {
  outline:3px solid #afa5ed; outline-offset:2px; } button,.button { display:inline-block;
  background:var(--accent); color:white; border:1px solid var(--accent); border-radius:7px;
  padding:10px 15px; font:600 14px/1.3 system-ui,sans-serif; cursor:pointer; text-decoration:none; }
.secondary { background:white; color:var(--accent); } button:disabled { opacity:.5; cursor:wait; }
.actions { display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin-top:15px; }
summary { cursor:pointer; font-weight:650; } details > .grid { margin-top:18px; }
.full { grid-column:1/-1; } .status { padding:12px 0; min-height:48px; font-weight:600; }
.error { color:#9a2538; } .case { margin:15px 0; } .case-head { display:flex; gap:12px;
  justify-content:space-between; align-items:start; } .badge { padding:4px 10px; border-radius:30px;
  background:var(--tint); font-size:13px; white-space:nowrap; } dl { display:grid;
  grid-template-columns:170px 1fr; gap:7px 14px; margin:14px 0; } dt { color:var(--muted); }
dd { margin:0; overflow-wrap:anywhere; } ul { margin:8px 0; padding-left:22px; }
pre { overflow:auto; font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
.empty { padding:35px 15px; text-align:center; color:var(--muted); } .verdict[aria-pressed="true"] {
  background:var(--ink); border-color:var(--ink); color:white; } .case p { overflow-wrap:anywhere; }
@media(max-width:680px) { main { padding:20px 15px 40px; } header,.case-head { display:block; }
  .grid { grid-template-columns:1fr; } dl { grid-template-columns:1fr; gap:2px; }
  dd { margin-bottom:8px; } header .badge { display:inline-block; margin-top:10px; } }
</style>
</head>
<body><main>
<header><div><p class="eyebrow">Finder / development workspace</p><h1>A clearer pressing review</h1>
<p class="muted">Enter a case, inspect the evidence, save your judgment.</p></div>
<span class="badge">Local only · no service calls</span></header>
<div class="notice">Synthetic and user-authored development inputs only. Do not import owner data,
provider responses, photos, URLs, or credentials. These provisional text assessments do not verify
identity. No discovery or notifications run here.</div>
<section class="panel" aria-labelledby="input-title"><h2 id="input-title">1. Prepare your input</h2>
<p class="muted">Build a small observation bundle, or paste/import its JSON. Saving adds observations
and updates the target and owner settings. Your draft stays on this page until you reload.</p>
<details><summary>Enter a target and a case</summary>
<form id="case-form"><div class="grid">
<label>Input origin<select id="source"><option value="manual">User-authored manual case</option>
<option value="synthetic">Invented synthetic fixture</option></select></label>
<label>Artist<input id="artist" maxlength="160"></label>
<label>Album<input id="album" maxlength="300"></label>
<label>Target disc color (optional)<input id="color" maxlength="80" placeholder="Blue"></label>
<label>Target country (optional; pressing fact)<input id="target-country" maxlength="160"></label>
<label>Target release year (optional)<input id="target-year" inputmode="numeric" maxlength="4"></label>
<label>Target editions (optional, comma-separated)<input id="target-editions" maxlength="160" placeholder="Limited Edition"></label>
<label>Target named cover (optional)<input id="target-cover" maxlength="60" placeholder="Northern Lights"></label>
<label>Target package requirement<select id="target-component"><option value="">None supplied</option><option value="signed_insert">Signed insert</option></select></label>
<label>Maximum subtotal for likely matches<input id="maximum" placeholder="30.00" inputmode="decimal"></label>
<label>Maximum subtotal for unclear cases<input id="gamble" placeholder="15.00" inputmode="decimal"></label>
<label>Currency<input id="currency" value="USD" maxlength="3" required></label>
<label>Saved destination country (optional)<input id="country" maxlength="2" placeholder="US"></label>
<label>Saved destination postal code (optional)<input id="postal" maxlength="16"></label>
<label>Case ID<input id="case-id" maxlength="64" required placeholder="manual-case-1"></label>
<label class="full">Case title<input id="case-title" maxlength="300" required></label>
<label>Observed at (UTC, supplied by you)<input id="observed" required placeholder="2026-10-03T12:00:00Z"></label>
<label>Details observed at (UTC, optional)<input id="details-observed" placeholder="2026-10-03T12:00:00Z"></label>
<label>Price (blank means unknown)<input id="price" inputmode="decimal"></label>
<label>Shipping (blank means unknown)<input id="shipping" inputmode="decimal"></label>
<label>Quote destination country (optional)<input id="quote-country" maxlength="2"></label>
<label>Quote destination postal code (optional)<input id="quote-postal" maxlength="16"></label>
<label>Case country (optional; pressing claim)<input id="case-country" maxlength="160"></label>
<label>Case release year (optional)<input id="case-year" inputmode="numeric" maxlength="4"></label>
<label>Case editions (optional, comma-separated)<input id="case-editions" maxlength="160"></label>
</div><p class="muted">A color adds a required color sign. Prices are your thresholds, not market
values. When a draft or saved target exists, its target, settings and comparison profiles are reused;
edit the JSON to change them. Target fields above are used only for a new bundle. Quote destinations
and observation times are explicit inputs, never inferred. Signed insert supports only the existing
explicit-denial check; missing mention and ambiguous claims do not verify package completeness.</p>
<div class="actions"><button type="submit" class="secondary">Add case to JSON draft</button></div>
</form></details>
<div class="actions"><button id="example" class="secondary">Load synthetic example</button>
<label>Import observation JSON<input id="file" type="file" accept="application/json,.json"></label></div>
<label for="draft">Observation bundle JSON (maximum 256 KiB, 100 case IDs)</label>
<textarea id="draft" spellcheck="false" aria-describedby="draft-help"></textarea>
<p id="draft-help" class="muted">Only the Save button writes this draft to the SQLite workspace.
Schema v2 accepts up to 20 authored comparison profiles in alternatives; edit these in JSON.
No supplied profiles means comparison is unchecked. Coverage always remains incomplete.
Review-history exports are archival records and cannot be restored through this importer.</p>
<div class="actions"><button id="save">Save observation bundle</button>
<button id="refresh" class="secondary">Refresh saved review</button>
<a class="button secondary" href="/api/export" download="finder-local-review.json">Export review record</a></div>
<div id="status" class="status" role="status" aria-live="polite"></div>
</section>
<section aria-labelledby="review-title"><h2 id="review-title">2. Review saved cases</h2>
<p id="summary" class="muted">Loading the local workspace…</p><div id="profiles"></div><div id="cases"></div></section>
</main>
<script nonce="@@NONCE@@">
'use strict';
const csrf = @@TOKEN@@;
const maxBytes = 256 * 1024;
const el = (id) => document.getElementById(id);
let snapshot = null;
let pending = false;
let requestGeneration = 0;
function message(text, isError=false) {
  el('status').textContent = text;
  el('status').classList.toggle('error', isError);
}
function setPending(value) {
  pending = value;
  document.querySelectorAll('button, input[type=file]').forEach((node) => { node.disabled = value; });
  el('cases').setAttribute('aria-busy', String(value));
}
function bytes(text) { return new TextEncoder().encode(text).length; }
function parseDraft() {
  const text = el('draft').value;
  if (bytes(text) > maxBytes) throw new Error('The JSON draft exceeds 256 KiB.');
  const value = JSON.parse(text);
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Use a JSON object.');
  if (!Array.isArray(value.listings) || value.listings.length > 100) {
    throw new Error('Supply an observation bundle with at most 100 case entries.');
  }
  return value;
}
function writeDraft(value) {
  const text = JSON.stringify(value, null, 2);
  if (bytes(text) > maxBytes) throw new Error('The JSON draft exceeds 256 KiB.');
  el('draft').value = text;
}
async function request(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
      headers:body === undefined ? {} : {'Content-Type':'application/json', 'X-Finder-CSRF':csrf},
      body:body === undefined ? undefined : body, signal:controller.signal,
      cache:'no-store', credentials:'omit', redirect:'error'});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || 'Local request failed.');
      error.status = response.status; throw error; }
    return data;
  } finally { clearTimeout(timeout); }
}
async function run(action, success) {
  if (pending) return;
  setPending(true);
  const generation = ++requestGeneration;
  try {
    const data = await action();
    if (generation !== requestGeneration) return;
    snapshot = data; render(); message(success);
  } catch (error) {
    if (generation !== requestGeneration) return;
    if (error.status === 409) {
      try { snapshot = await request('/api/snapshot'); render(); }
      catch (_) { message('The case changed and refresh failed. Refresh before saving again.', true); return; }
    }
    message(error.name === 'AbortError' ?
      'The request timed out. Refresh to check saved state before retrying; your draft is retained.' :
      (error.message || 'The local server is unavailable. Your draft is retained.'), true);
  } finally { if (generation === requestGeneration) setPending(false); }
}
function textNode(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className; return node;
}
function detail(dl, name, value) {
  dl.append(textNode('dt', name), textNode('dd', value));
}
function readable(value) { return String(value).replaceAll('_', ' '); }
function evidence(card, title, values) {
  card.append(textNode('h3', title));
  if (!Array.isArray(values) || values.length === 0) { card.append(textNode('p', 'None reported.', 'muted')); return; }
  const list = document.createElement('ul');
  values.forEach((value) => list.append(textNode('li', typeof value === 'string' ? readable(value) : JSON.stringify(value))));
  card.append(list);
}
function profileFacts(profile) {
  const dl = document.createElement('dl');
  [['Artist',profile.artist],['Album',profile.album],['Disc colors',profile.colors],
    ['Catalog numbers',profile.catalog_numbers],['Barcodes',profile.barcodes],['Formats',profile.formats],
    ['Country',profile.country],['Release year',profile.release_year],['Editions',profile.editions],['Named cover',profile.cover_edition],
    ['Required components',profile.required_components]].forEach(([label,value]) => {
      const text = Array.isArray(value) ? value.map(readable).join(', ') : value;
      detail(dl,label,text || 'Not supplied');
    });
  return dl;
}
function render() {
  el('cases').replaceChildren();
  el('profiles').replaceChildren();
  const rows = snapshot.rows || [];
  const settings = snapshot.settings || {};
  const target = snapshot.target;
  el('summary').textContent = target ?
    `${target.artist} · ${target.album} / ${rows.length} cases / revision ${snapshot.revision}` :
    'Your workspace is empty. Save an observation bundle to begin.';
  if (target) {
    const profiles = snapshot.alternatives || [];
    const panel = document.createElement('div'); panel.className = 'panel';
    panel.append(textNode('h3','Authored pressing profiles'),
      textNode('p',`${profiles.length} supplied competitor profiles. Comparison coverage is always incomplete. ` +
        (profiles.length ? 'Possible pressing means support among supplied profiles only; it does not prove market or physical identity.' :
          'No alternatives were checked; matching claims remain uncertain.'),'notice'),
      textNode('p','Country and year are evidence only; disagreements do not automatically reject a case under the current review policy. Edition terms use the existing normalizer.','muted'));
    if ((target.required_components || []).includes('signed_insert')) {
      panel.append(textNode('p','The target includes a signed insert. This activates the explicit-denial guard only; it does not verify seller inclusion. A possible pressing may still have no insert mention.','notice'));
    }
    const own = document.createElement('details');
    own.append(textNode('summary','Target facts'),profileFacts(target)); panel.append(own);
    profiles.forEach((profile) => {
      const details = document.createElement('details');
      details.append(textNode('summary',`Local competitor ${profile.id}`),profileFacts(profile));
      panel.append(details);
    });
    el('profiles').append(panel);
  }
  if (!rows.length) el('cases').append(textNode('p', 'No saved cases yet.', 'empty'));
  rows.forEach((row) => {
    const card = document.createElement('article'); card.className = 'case';
    const head = document.createElement('div'); head.className = 'case-head';
    head.append(textNode('h3', row.listing.title), textNode('span', readable(row.review.status), 'badge'));
    card.append(head, textNode('p', `Case ${row.id} · ${row.listing.source_metadata.local_source} input`, 'muted'));
    const dl = document.createElement('dl');
    detail(dl, 'Delivered subtotal', row.review.subtotal === null ? 'Unknown' :
      `${row.review.subtotal} ${row.review.currency || ''} (before tax and fees)`);
    detail(dl, 'Your likely-match cap', settings.maximum_subtotal === null ? 'Unset' :
      `${settings.maximum_subtotal} ${settings.currency}`);
    detail(dl, 'Your unclear-case cap', settings.gamble_max === null ? 'Unset' :
      `${settings.gamble_max} ${settings.currency}`);
    detail(dl, 'Price assessment', readable(row.review.alert_budget));
    detail(dl, 'Policy simulation', row.review.notify ? 'Would qualify; nothing is sent' : 'Would not qualify');
    detail(dl, 'Supplied comparison', row.review.alternatives_checked == null ? 'Unchecked' :
      `${row.review.alternatives_checked} checked; ${row.review.alternatives_not_ruled_out} unresolved; coverage incomplete`);
    detail(dl, 'Your verdict', row.verdict ? readable(row.verdict) : 'Not yet judged');
    card.append(dl);
    evidence(card, 'Evidence found', row.review.clues);
    evidence(card, 'Structured target comparison', (row.comparison_evidence || []).map((item) =>
      `${readable(item.field)}: case ${item.listing_values.join(', ')} / target ${item.variant_values.join(', ')} / ${item.matched ? 'agrees' : 'disagrees'}`));
    evidence(card, 'Still needs verification', row.review.verify);
    if (row.judgment_needs_review) {
      card.append(textNode('p', 'Assessment changed since this verdict. The saved verdict and its original record are retained.', 'notice'));
    }
    const actions = document.createElement('div'); actions.className = 'actions';
    [['mine','Mine'],['other','Other'],['unsure','Unsure']].forEach(([value, label]) => {
      const button = textNode('button', label, 'secondary verdict');
      button.type = 'button'; button.disabled = pending;
      button.setAttribute('aria-pressed', String(row.verdict === value));
      button.setAttribute('aria-label', `${label}: ${row.listing.title}`);
      const revision = snapshot.revision;
      button.addEventListener('click', () => run(() => request('/api/verdict', JSON.stringify({
        id:row.id, verdict:value, expected_revision:revision, review_fingerprint:row.review_fingerprint
      })), 'Verdict saved to the local workspace.'));
      actions.append(button);
    });
    card.append(actions);
    const details = document.createElement('details');
    details.append(textNode('summary', 'Assessment and judgment record'),
      textNode('pre', JSON.stringify({review:row.review, judgment:row.judgment}, null, 2)));
    card.append(details); el('cases').append(card);
  });
}
el('refresh').addEventListener('click', () => run(() => request('/api/snapshot'), 'Saved review refreshed.'));
el('save').addEventListener('click', () => {
  if (pending) return;
  try { parseDraft(); }
  catch (error) { message(error.message, true); return; }
  // Send original text: the server must detect duplicate keys, not JSON.parse's last value.
  const draft = el('draft').value;
  run(() => request('/api/import', draft), 'Observation bundle saved. Your draft is retained below.');
});
el('file').addEventListener('change', async (event) => {
  if (pending) return;
  const file = event.target.files[0]; if (!file) return;
  if (file.size > maxBytes) { message('The file exceeds 256 KiB; your draft is unchanged.', true); return; }
  const previous = el('draft').value;
  setPending(true);
  try {
    const text = await file.text();
    if (bytes(text) > maxBytes) throw new Error('The file exceeds 256 KiB.');
    const value = JSON.parse(text);
    if (!value || ![1,2].includes(value.schema_version) || !Array.isArray(value.listings)) {
      throw new Error('Import an observation bundle, not an exported review record.');
    }
    if (el('draft').value !== previous) throw new Error('Your draft changed while the file loaded; import it again.');
    el('draft').value = text; message('File loaded into the draft. Review it, then Save.');
  } catch (error) { message(error.message, true); }
  finally { setPending(false); event.target.value = ''; }
});
el('example').addEventListener('click', () => {
  if (pending) return;
  const time = new Date().toISOString();
  writeDraft({schema_version:2,source:'synthetic',target:{artist:'Example Ensemble',album:'Offline Horizons',
    colors:['Blue'],formats:['LP'],country:'US',release_year:2024,editions:['Limited Edition'],
    required_components:[]},alternatives:[{id:'invented-black',artist:'Example Ensemble',
      album:'Offline Horizons',colors:['Black'],formats:['LP'],country:'US',release_year:2024}],
    settings:{maximum_subtotal:'30.00',gamble_max:'15.00',currency:'USD',
    country:'US',postal_code:'00000',tells:[{kind:'color',value:'Blue',required:true}]},
    listings:[['blue','blue','20.00'],['unclear','LP','10.00'],['black','black','8.00']].map(([id,color,price]) => ({
      id:`example-${id}`,title:`Example Ensemble Offline Horizons ${color} vinyl`,observed_at:time,
      details_observed_at:time,current_price:price,currency:'USD',shipping_cost:'4.00',shipping_currency:'USD',
      price_kind:'fixed_price',delivery_country:'US',delivery_postal_code:'00000'}))});
  message('Invented example loaded with the current fixture time. Review it, then Save.');
});
el('case-form').addEventListener('submit', (event) => {
  event.preventDefault(); if (pending) return;
  const value = (id) => el(id).value.trim();
  const optional = (id) => value(id) || null;
  try {
    const savedInput = snapshot && snapshot.target ? {schema_version:snapshot.schema_version,
      source:snapshot.source,target:snapshot.target,settings:snapshot.settings,listings:[],
      ...(snapshot.schema_version === 2 ? {alternatives:snapshot.alternatives} : {})} : null;
    const draft = el('draft').value.trim() ? parseDraft() : savedInput || {listings:[]};
    if (draft.listings.length >= 100) throw new Error('The draft already contains 100 observations.');
    const color = value('color');
    const split = (id) => value(id) ? value(id).split(',').map((part) => part.trim()).filter(Boolean) : [];
    const year = (id) => {
      if (!value(id)) return null;
      if (!/^(19|20)\d{2}$/.test(value(id))) throw new Error('Release years must be 1900–2099.');
      return Number(value(id));
    };
    if (!draft.target && (!value('artist') || !value('album'))) throw new Error('A new bundle needs an artist and album.');
    const base = draft.target ? draft : {schema_version:2,source:value('source'),
      target:{artist:value('artist'),album:value('album'),colors:color ? [color] : [],formats:['LP'],
        country:optional('target-country'),release_year:year('target-year'),editions:split('target-editions'),
        cover_edition:optional('target-cover'),
        required_components:value('target-component') ? [value('target-component')] : []},
      alternatives:null,settings:{maximum_subtotal:optional('maximum'),gamble_max:optional('gamble'),currency:value('currency'),
        country:optional('country'),postal_code:optional('postal'),tells:color ? [{kind:'color',value:color,required:true}] : []}};
    if (base.schema_version === 1 && (value('case-country') || value('case-year') || value('case-editions'))) {
      throw new Error('This is a v1 bundle. Explicitly change schema_version to 2 in JSON before adding v2 case fields.');
    }
    const input = {...base,listings:[...draft.listings,{id:value('case-id'),title:value('case-title'),observed_at:value('observed'),
        details_observed_at:optional('details-observed'),current_price:optional('price'),currency:value('currency'),
        shipping_cost:optional('shipping'),shipping_currency:value('currency'),price_kind:'fixed_price',
        delivery_country:optional('quote-country'),delivery_postal_code:optional('quote-postal'),
        ...(base.schema_version === 2 ? {country:optional('case-country'),release_year:year('case-year'),editions:split('case-editions')} : {})}]};
    writeDraft(input); message(draft.target ? 'Case added; existing target, settings and profiles are retained. Review JSON, then Save.' :
      'Case added with the form target and settings. Review JSON, then Save.');
  } catch (error) { message(error.message, true); }
});
run(() => request('/api/snapshot'), 'Local workspace ready.');
</script></body></html>"""
