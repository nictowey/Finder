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
  padding:10px; margin:5px 0 0; font:inherit; } textarea { min-height:90px; resize:vertical;
  font:13px/1.5 ui-monospace,monospace; } input:focus,textarea:focus,select:focus,button:focus-visible,a:focus-visible {
  outline:3px solid #afa5ed; outline-offset:2px; } button,.button { display:inline-block;
  background:var(--accent); color:white; border:1px solid var(--accent); border-radius:7px;
  padding:10px 15px; font:600 14px/1.3 system-ui,sans-serif; cursor:pointer; text-decoration:none; }
.secondary { background:white; color:var(--accent); } button:disabled { opacity:.5; cursor:wait; }
.actions { display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin-top:15px; }
summary { cursor:pointer; font-weight:650; } details > .grid { margin-top:18px; }
.full { grid-column:1/-1; } [hidden] { display:none !important; } .check input { display:inline-block; width:auto; margin-right:8px; } .check { margin:18px 0 8px; } #draft { min-height:240px; } .sign-row { display:grid; grid-template-columns:1fr 1fr 2fr auto; gap:9px; align-items:end; margin-bottom:12px; } .status { padding:12px 8px; min-height:48px; font-weight:600; position:sticky; top:0; z-index:2; background:var(--paper); }
.error { color:#9a2538; } .case { margin:15px 0; } .case-head { display:flex; gap:12px;
  justify-content:space-between; align-items:start; } .badge { padding:4px 10px; border-radius:30px;
  background:var(--tint); font-size:13px; white-space:nowrap; } dl { display:grid;
  grid-template-columns:170px 1fr; gap:7px 14px; margin:14px 0; } dt { color:var(--muted); }
dd { margin:0; overflow-wrap:anywhere; } ul { margin:8px 0; padding-left:22px; }
pre { overflow:auto; font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
.empty { padding:35px 15px; text-align:center; color:var(--muted); } .verdict[aria-pressed="true"] {
  background:var(--ink); border-color:var(--ink); color:white; } .case p { overflow-wrap:anywhere; }
@media(max-width:680px) { main { padding:20px 15px 40px; } header,.case-head { display:block; }
  .grid,.sign-row { grid-template-columns:1fr; } dl { grid-template-columns:1fr; gap:2px; }
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
<div id="status" class="status" role="status" aria-live="polite"></div>
<section class="panel" aria-labelledby="input-title"><h2 id="input-title">1. Your local pressing profile</h2>
<p class="muted">Save a profile before adding candidates. Blank facts stay unknown. Your price caps are your thresholds, not market values.</p>
<form id="profile-form"><div class="grid" id="profile-fields"></div>
<label class="check"><input type="checkbox" id="detailed"> Enable detailed profile fields and authored comparisons (v2)</label>
<p class="muted">Existing v1 inputs keep their original meaning until you explicitly enable and save v2. This does not rewrite old observations.</p>
<div class="grid" id="profile-detail-fields"></div>
<h3>Owner settings</h3><div class="grid" id="settings-fields"></div>
<h3>Signs to check</h3><p class="muted">Add signs deliberately. A disc color does not automatically become a required sign. Required signs, supporting signs and common-version anti-signs are separate choices.</p>
<div id="clue-rows"></div><button type="button" id="add-clue" class="secondary">Add a sign</button>
<div class="actions"><button type="submit" id="save-profile">Save local profile</button><button type="button" id="reload-profile" class="secondary">Reload saved profile (discard profile edits)</button></div>
<p id="profile-state" class="muted"></p></form></section>
<section class="panel" aria-labelledby="comparison-title"><h2 id="comparison-title">2. Optional comparison profiles</h2>
<p class="notice">These are your authored local comparisons. Coverage always remains incomplete. They cannot verify the market or a physical pressing.</p>
<p id="comparison-help" class="muted"></p><div id="comparison-list"></div>
<button id="new-comparison" class="secondary">Add comparison profile</button>
<form id="comparison-form" hidden><h3 id="comparison-heading">Comparison profile</h3><div id="comparison-fields" class="grid"></div>
<div class="actions"><button type="submit" id="save-comparison">Save comparison</button><button type="button" id="remove-comparison" class="secondary">Remove this comparison</button><button type="button" id="cancel-comparison" class="secondary">Close comparison (discard edits)</button></div></form></section>
<section class="panel" aria-labelledby="candidate-title"><h2 id="candidate-title">3. Candidate observation</h2>
<p id="candidate-mode" class="muted">Add a new case after saving your local profile.</p>
<p class="muted">Enter only what you observed. Unknown price, shipping, currency, format and details are supported. Supply actual observation times with a timezone. Saving does not advance timestamps or edit old history.</p>
<form id="case-form"><div id="candidate-fields" class="grid"></div><div id="candidate-detail-fields" class="grid"></div>
<div class="actions"><button type="submit" id="save-candidate">Save candidate observation</button><button type="button" id="new-candidate" class="secondary">Start new case (discard candidate edits)</button><button type="button" id="rebase-candidate" class="secondary">Keep draft and use refreshed saved state</button></div>
<p id="candidate-state" class="muted"></p></form></section>
<section class="panel"><details id="advanced"><summary>Advanced: observation JSON import and export</summary>
<p class="muted">Whole-bundle import replaces profile/settings and adds observations. Use the separate forms for ordinary edits. Review-record exports are archival and cannot be restored through this importer.</p>
<div class="actions"><button id="example" class="secondary">Load synthetic example</button><button id="profile-to-json" class="secondary">Copy saved profile to JSON</button>
<label>Import observation JSON<input id="file" type="file" accept="application/json,.json"></label></div>
<label for="draft">Observation bundle JSON (maximum 256 KiB, 100 case IDs)</label><textarea id="draft" spellcheck="false"></textarea>
<div class="actions"><button id="save">Save observation bundle</button><a class="button secondary" href="/api/export" download="finder-local-review.json">Export review record</a></div>
</details><div class="actions"><button id="refresh" class="secondary">Refresh saved review (keep drafts)</button></div>
<p class="muted">Saved forms return after page reload. Unsaved edits stay in this page only; export or save them before closing it.</p></section>
<section aria-labelledby="review-title"><h2 id="review-title">4. Review saved cases</h2>
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
let profileState = {revision:0, base:null, clean:''};
let comparisonState = null;
let candidateState = {revision:0, current:null, id:null, clean:''};
let draftRevision = 0;
let draftClean = '';
const value = (id) => el(id).value.trim();
const clone = (value) => JSON.parse(JSON.stringify(value));
function message(text, isError=false) {
  el('status').textContent = text; el('status').classList.toggle('error', isError);
}
function setPending(value) {
  pending = value;
  document.querySelectorAll('button, input[type=file]').forEach((node) => { node.disabled = value; });
  el('detailed').disabled=value || Boolean(snapshot && snapshot.target && snapshot.schema_version === 2);
  el('cases').setAttribute('aria-busy', String(value));
}
function bytes(text) { return new TextEncoder().encode(text).length; }
function parseDraft() {
  const text = el('draft').value;
  if (bytes(text) > maxBytes) throw new Error('The JSON draft exceeds 256 KiB.');
  const result = JSON.parse(text);
  if (!result || Array.isArray(result) || typeof result !== 'object' ||
      !Array.isArray(result.listings) || result.listings.length > 100) {
    throw new Error('Supply an observation bundle with at most 100 case entries.');
  }
  return result;
}
function writeDraft(input) {
  const text = JSON.stringify(input, null, 2);
  if (bytes(text) > maxBytes) throw new Error('The JSON draft exceeds 256 KiB.');
  el('draft').value = text; draftRevision = snapshot ? snapshot.revision : 0;
}
async function request(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
      headers:body === undefined ? {} : {'Content-Type':'application/json', 'X-Finder-CSRF':csrf},
      body, signal:controller.signal, cache:'no-store', credentials:'omit', redirect:'error'});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || 'Local request failed.');
      error.status=response.status; error.code=data.code; throw error; }
    return data;
  } finally { clearTimeout(timeout); }
}
function capture(form) {
  return JSON.stringify([...el(form).querySelectorAll('input,select,textarea')].map((node) =>
    [node.id, node.type === 'checkbox' ? node.checked : node.value]));
}
function profileFrom(data) {
  if (!data || !data.target) return null;
  return {schema_version:data.schema_version, source:data.source, target:clone(data.target),
    settings:clone(data.settings), ...(data.schema_version === 2 ? {alternatives:clone(data.alternatives)} : {})};
}
function refreshEditors(saved, own={}) {
  const profileCapture=capture('profile-form');
  if (own.profile !== undefined) {
    if (own.profile === profileCapture) hydrateProfile(saved);
    else { profileState.revision=saved.revision; profileState.base=profileFrom(saved); profileState.clean=own.profile; }
  } else if (profileCapture === profileState.clean) hydrateProfile(saved);
  if (!comparisonState) renderComparisonList();
  if (own.comparison !== undefined && own.comparison === capture('comparison-form')) closeComparison();
  else if (own.comparison !== undefined && comparisonState) {
    comparisonState.revision=saved.revision; comparisonState.base=profileFrom(saved);
    comparisonState.id=own.removed ? null : own.comparisonId; comparisonState.clean=own.comparison;
  }
  const candidateDirty = capture('case-form') !== candidateState.clean;
  if (!candidateDirty && candidateState.id === null) candidateState.revision=saved.revision;
  if (own.candidate !== undefined) {
    const row = saved.rows.find((row) => row.id === own.candidateId);
    if (row) {
      const sameId=value('candidate-id') === own.candidateId;
      candidateState.revision=saved.revision; candidateState.current=sameId ? row.current_token : null;
      candidateState.id=sameId ? row.id : null;
      if (own.candidate === capture('case-form')) {
        hydrateCandidate(row, true);
        el('candidate-state').textContent='Observation saved. Use Record another observation on the saved case for a genuinely new observation.';
      } else candidateState.clean=own.candidate;
    }
  }
  if (!el('draft').value.trim() || own.draft !== undefined) draftRevision=saved.revision;
  if (own.draft !== undefined) draftClean=own.draft;
  updateEditorHints();
}
async function run(action, success, own={}) {
  if (pending) return;
  setPending(true); const generation=++requestGeneration;
  try {
    const data = await action();
    if (generation !== requestGeneration) return;
    snapshot=data; render(); refreshEditors(data, own); message(success);
  } catch (error) {
    if (generation !== requestGeneration) return;
    if (error.status === 409 && error.code !== 'observation_collision') {
      try { const data=await request('/api/snapshot');
        if (generation !== requestGeneration) return;
        snapshot=data; render(); refreshEditors(data);
      } catch (_) { message('Saved state changed and refresh failed. Your drafts are retained. Refresh before saving again.', true); return; }
    }
    message(error.name === 'AbortError' ?
      'The request timed out. Refresh to check saved state before retrying; your drafts are retained.' :
      (error.message || 'The local server is unavailable. Your drafts are retained.'), true);
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
    'Your workspace is empty. Save a local pressing profile to begin.';
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
    const observe = textNode('button', 'Record another observation', 'secondary');
    observe.type='button'; observe.disabled=pending;
    observe.addEventListener('click', () => {
      if (pending) return;
      if (capture('case-form') !== candidateState.clean) {
        message('Your candidate has unsaved edits. Save it or use Start new case to discard the draft before selecting a saved case.', true); return;
      }
      hydrateCandidate(row, false);
      message('Saved case copied into one draft. Enter the actual new observation time and review every carried-forward claim. Details keep their original time until you explicitly change it.');
    });
    actions.append(observe); card.append(actions);
    const details = document.createElement('details');
    details.append(textNode('summary', 'Assessment and judgment record'),
      textNode('pre', JSON.stringify({review:row.review, judgment:row.judgment}, null, 2)));
    card.append(details); el('cases').append(card);
  });
}
const basicFacts = [
  ['artist','Artist','text'], ['album','Album','text'],
  ['colors','Disc colors (one per line; blank = unknown)','list'],
  ['catalog_numbers','Catalog numbers (one per line)','list'],
  ['barcodes','Barcodes (one per line)','list'], ['formats','Formats (one per line; blank = unknown)','list']
];
const detailedFacts = [
  ['country','Release country (pressing fact)','optional'], ['release_year','Release year (1900–2099)','integer'],
  ['editions','Editions (one per line)','list'], ['cover_edition','Named cover (optional)','optional'],
  ['required_components','Package requirement','components']
];
const settingsFields = [
  ['maximum_subtotal','Likely-match subtotal cap (blank = unset)','optional'],
  ['gamble_max','Unclear-case subtotal cap (blank = unset)','optional'], ['currency','Cap currency','text'],
  ['country','Saved destination country (two-letter code)','optional'], ['postal_code','Saved destination postal code','optional'],
  ['condition_ids','Accepted condition IDs (one per line; blank = no condition filter)','list'],
  ['alert_mode','Comparison mode','select',['review_leads','strict']],
  ['auction_alert_minutes','Auction alert window in minutes (0 = off)','integer']
];
const candidateFields = [
  ['id','Local case ID','text'], ['title','Candidate title','text'],
  ['observed_at','Observed at (actual time with timezone)','text'],
  ['details_observed_at','Details observed at (blank = unknown)','optional'],
  ...basicFacts.map(([key,label,type]) => [key,`Candidate ${label.toLowerCase()}`,type === 'text' ? 'optional' : type]),
  ['current_price','Price (blank = unknown)','optional'], ['currency','Price currency (blank = unknown)','optional'],
  ['shipping_cost','Shipping amount (blank = unknown)','optional'], ['shipping_currency','Shipping currency (independent of price)','optional'],
  ['price_kind','Price kind','select',['unknown','fixed_price','current_bid']],
  ['condition_id','Condition ID (optional)','optional'], ['listing_ends_at','Auction/listing ends at (optional, with timezone)','optional'],
  ['delivery_country','Quote destination country (optional)','optional'], ['delivery_postal_code','Quote destination postal code (optional)','optional']
];
const candidateDetails = detailedFacts.filter(([key]) => ['country','release_year','editions'].includes(key));
function buildFields(container, prefix, fields) {
  fields.forEach(([key,label,type,options]) => {
    const wrapper=textNode('label',label); const control=document.createElement(type === 'list' ? 'textarea' :
      ['select','components'].includes(type) ? 'select' : 'input');
    control.id=`${prefix}-${key}`;
    if (type === 'list') { control.rows=2; control.maxLength=2048; }
    else if (type === 'select' || type === 'components') {
      (type === 'components' ? ['', 'signed_insert'] : options).forEach((item) => {
        const option=textNode('option',item ? readable(item) : 'None supplied'); option.value=item; control.append(option);
      });
    } else { control.type='text'; control.maxLength=key === 'id' ? 64 : key === 'title' || key === 'album' ? 300 : 160; }
    if (['id','title','artist','album'].includes(key) && type === 'text') control.required=true;
    if (type === 'integer') control.inputMode='numeric';
    if (key.includes('observed_at') || key === 'listing_ends_at') control.placeholder='2026-10-03T03:00:00Z';
    wrapper.append(control); el(container).append(wrapper);
  });
}
function fillFields(prefix, fields, data) {
  fields.forEach(([key,,type]) => {
    const saved=data && data[key];
    el(`${prefix}-${key}`).value=type === 'list' ? (saved || []).join('\n') :
      type === 'components' ? (saved || [])[0] || '' : saved == null ? '' : String(saved);
  });
}
function readFields(prefix, fields) {
  return Object.fromEntries(fields.map(([key,,type]) => {
    const text=value(`${prefix}-${key}`);
    if (type === 'integer' && text && !/^\d+$/.test(text)) throw new Error('Use whole numbers for years and auction minutes.');
    return [key,type === 'list' ? text.split('\n').map((item) => item.trim()).filter(Boolean) :
      type === 'components' ? (text ? [text] : []) : type === 'integer' ? (text ? Number(text) : null) :
      type === 'optional' ? text || null : text];
  }));
}
buildFields('profile-fields','profile',[['source','Input origin','select',['manual','synthetic']]]);
buildFields('profile-fields','target',basicFacts);
buildFields('profile-detail-fields','target',detailedFacts);
buildFields('settings-fields','settings',settingsFields);
buildFields('comparison-fields','comparison',[['id','Local comparison ID','text'],...basicFacts,...detailedFacts]);
buildFields('candidate-fields','candidate',candidateFields);
buildFields('candidate-detail-fields','candidate',candidateDetails);
let clueNumber=0;
function addClue(clue={kind:'keyword',value:'',required:false}, category='supporting') {
  if (el('clue-rows').children.length >= 24) throw new Error('At most 12 target signs and 12 anti-signs are supported.');
  const row=document.createElement('div'); row.className='sign-row';
  const index=++clueNumber;
  const group=textNode('label','Role'); const role=document.createElement('select'); role.id=`sign-${index}-role`;
  [['required','Required sign'],['supporting','Supporting sign'],['anti','Common-version anti-sign']].forEach(([key,label]) => {
    const option=textNode('option',label); option.value=key; role.append(option);
  }); role.value=category; group.append(role);
  const kindLabel=textNode('label','Kind'); const kind=document.createElement('select'); kind.id=`sign-${index}-kind`;
  ['keyword','color','catalog_number','barcode','label','country','numbered'].forEach((key) => {
    const option=textNode('option',readable(key)); option.value=key; kind.append(option);
  }); kind.value=clue.kind; kindLabel.append(kind);
  const textLabel=textNode('label','Exact sign'); const input=document.createElement('input');
  input.id=`sign-${index}-value`; input.value=clue.value; input.maxLength=80; input.required=true; textLabel.append(input);
  // Preserve an imported anti-sign's supported required flag unless its role is explicitly changed.
  row.dataset.antiRequired=String(category === 'anti' && clue.required);
  role.addEventListener('change', () => { row.dataset.antiRequired='false'; });
  const remove=textNode('button','Remove','secondary'); remove.type='button';
  remove.addEventListener('click', () => { if (!pending) row.remove(); });
  row.append(group,kindLabel,textLabel,remove); el('clue-rows').append(row);
}
function readClues() {
  const tells=[], anti_tells=[];
  [...el('clue-rows').children].forEach((row) => {
    const [role,kind,input]=row.querySelectorAll('select,input');
    if (!input.value.trim()) throw new Error('Enter each sign value or remove its empty row.');
    const clue={kind:kind.value,value:input.value.trim(),required:role.value === 'required' ||
      (role.value === 'anti' && row.dataset.antiRequired === 'true')};
    (role.value === 'anti' ? anti_tells : tells).push(clue);
  });
  return {tells,anti_tells};
}
function toggleDetails() {
  el('profile-detail-fields').hidden=!el('detailed').checked;
  el('profile-detail-fields').querySelectorAll('input,select,textarea').forEach((node) => { node.disabled=!el('detailed').checked; });
}
function hydrateProfile(data) {
  const profile=profileFrom(data); const target=profile ? profile.target : {};
  fillFields('profile',[['source','','select']],{source:profile ? profile.source : 'manual'});
  fillFields('target',basicFacts,target); fillFields('target',detailedFacts,target);
  fillFields('settings',settingsFields,profile ? profile.settings : {currency:'USD',alert_mode:'review_leads',auction_alert_minutes:120});
  el('detailed').checked=Boolean(profile && profile.schema_version === 2);
  el('detailed').disabled=el('detailed').checked; toggleDetails();
  el('clue-rows').replaceChildren();
  if (profile) {
    (profile.settings.tells || []).forEach((clue) => addClue(clue,clue.required ? 'required' : 'supporting'));
    (profile.settings.anti_tells || []).forEach((clue) => addClue(clue,'anti'));
  }
  profileState={revision:data ? data.revision : 0,base:profile,clean:capture('profile-form')};
}
function readProfile() {
  const base=profileState.base || {};
  const version=el('detailed').checked ? 2 : 1;
  return {...clone(base),schema_version:version,source:value('profile-source'),
    target:{...(base.target || {}),...readFields('target',basicFacts),
      ...(version === 2 ? readFields('target',detailedFacts) : {})},
    settings:{...(base.settings || {}),...readFields('settings',settingsFields),...readClues()},
    ...(version === 2 ? {alternatives:base.alternatives || null} : {})};
}
function updateEditorHints() {
  if (!snapshot) return;
  el('profile-state').textContent=profileState.revision !== snapshot.revision ?
    'Saved profile changed while you were editing. Your edits are retained; compare them with the saved facts below, then reload the saved profile before editing again.' :
    `Editing ${snapshot.target ? 'saved' : 'new'} local profile · ${el('detailed').checked ? 'v2 detailed' : 'v1 original'} fields`;
  el('comparison-help').textContent=snapshot.schema_version === 2 && snapshot.target ?
    'Add, edit or remove comparisons below. Comparison IDs and facts must be distinct; artist and album must match your target.' :
    'Enable detailed fields above and save the local profile to add comparisons.';
  el('candidate-detail-fields').hidden=snapshot.schema_version !== 2;
  if (candidateState.revision !== snapshot.revision) el('candidate-state').textContent=
    'The saved profile changed. Your draft is retained. Review the latest saved facts, then explicitly use refreshed saved state before saving.';
}
function requireRevision(revision) {
  if (!snapshot || revision !== snapshot.revision) throw new Error('Saved profile changed. Your edits are retained. Review the current saved profile before retrying.');
}
function renderComparisonList() {
  el('comparison-list').replaceChildren();
  ((snapshot && snapshot.alternatives) || []).forEach((profile) => {
    const row=document.createElement('div'); row.className='actions';
    row.append(textNode('span',`${profile.id} · ${profile.colors.join(', ') || 'No disc color supplied'}`));
    const edit=textNode('button',`Edit ${profile.id}`,'secondary'); edit.type='button'; edit.disabled=pending;
    edit.addEventListener('click', () => beginComparison(profile)); row.append(edit); el('comparison-list').append(row);
  });
}
function beginComparison(profile=null) {
  if (pending) return;
  if (!snapshot || !snapshot.target || snapshot.schema_version !== 2) {
    message('Enable detailed fields and save your local profile first.',true); return;
  }
  if (comparisonState && capture('comparison-form') !== comparisonState.clean) {
    message('Your comparison has unsaved edits. Save it or close it before selecting another comparison.',true); return;
  }
  const data=profile || {artist:snapshot.target.artist,album:snapshot.target.album,formats:[]};
  fillFields('comparison',[['id','','text'],...basicFacts,...detailedFacts],data);
  el('comparison-id').readOnly=Boolean(profile);
  comparisonState={revision:snapshot.revision,base:profileFrom(snapshot),id:profile ? profile.id : null,clean:capture('comparison-form')};
  el('comparison-form').hidden=false; el('remove-comparison').hidden=!profile;
  el('comparison-heading').textContent=profile ? `Edit comparison ${profile.id}` : 'New comparison';
}
function closeComparison() { comparisonState=null; el('comparison-form').hidden=true; renderComparisonList(); }
function hydrateCandidate(row=null, saved=false) {
  const data=row ? clone(row.listing.source_metadata.manual_input) : {price_kind:'unknown'};
  fillFields('candidate',candidateFields,data); fillFields('candidate',candidateDetails,data);
  el('candidate-id').readOnly=Boolean(row);
  candidateState={revision:snapshot ? snapshot.revision : 0,current:row ? row.current_token : null,id:row ? row.id : null,clean:capture('case-form')};
  el('candidate-mode').textContent=row ? (saved ? `Saved case ${row.id}. Edits are an unsaved observation draft.` :
    `Record another observation for ${row.id}. Earlier history remains immutable.`) : 'Add a new local case. Its ID must not already be saved.';
  el('candidate-state').textContent=row ? 'The displayed timestamps are the saved source times. Only change them when you actually have a new observation.' : '';
}
function readCandidate() {
  return {...readFields('candidate',candidateFields),...(snapshot.schema_version === 2 ? readFields('candidate',candidateDetails) : {})};
}
el('detailed').addEventListener('change', () => { toggleDetails(); updateEditorHints(); });
el('add-clue').addEventListener('click', () => { if (!pending) { try { addClue(); } catch (error) { message(error.message,true); } } });
el('profile-form').addEventListener('submit', (event) => {
  event.preventDefault(); if (pending) return;
  try {
    requireRevision(profileState.revision); const profile=readProfile(); const submitted=capture('profile-form');
    run(() => request('/api/profile',JSON.stringify({profile,expected_revision:profileState.revision})),
      'Local profile saved. Candidate history and judgments are retained.',{profile:submitted});
  } catch (error) { message(error.message,true); }
});
el('reload-profile').addEventListener('click', () => { if (!pending && snapshot) { hydrateProfile(snapshot); updateEditorHints(); message('Saved profile loaded; profile edits discarded.'); } });
el('new-comparison').addEventListener('click', () => beginComparison());
el('cancel-comparison').addEventListener('click', () => { if (!pending) closeComparison(); });
function saveComparison(remove=false) {
  if (pending || !comparisonState) return;
  try {
    requireRevision(comparisonState.revision);
    const profile=clone(comparisonState.base);
    const entry=readFields('comparison',[['id','','text'],...basicFacts,...detailedFacts]);
    profile.alternatives=(profile.alternatives || []).filter((item) => item.id !== comparisonState.id);
    if (!remove) profile.alternatives.push(entry);
    const submitted=capture('comparison-form');
    run(() => request('/api/profile',JSON.stringify({profile,expected_revision:comparisonState.revision})),
      remove ? 'Comparison removed. Coverage remains incomplete.' : 'Comparison saved. Coverage remains incomplete.',{comparison:submitted,comparisonId:entry.id,removed:remove});
  } catch (error) { message(error.message,true); }
}
el('comparison-form').addEventListener('submit', (event) => { event.preventDefault(); saveComparison(); });
el('remove-comparison').addEventListener('click', () => saveComparison(true));
el('new-candidate').addEventListener('click', () => { if (!pending) hydrateCandidate(); });
el('rebase-candidate').addEventListener('click', () => {
  if (pending || !snapshot || !snapshot.target) return;
  const row=snapshot.rows.find((row) => row.id === value('candidate-id'));
  candidateState.revision=snapshot.revision; candidateState.current=row ? row.current_token : null;
  candidateState.id=row ? row.id : null;
  el('candidate-id').readOnly=Boolean(row);
  el('candidate-state').textContent='Your draft is retained against the refreshed state. Check source times and claims before saving; no historical observation will be changed.';
});
el('case-form').addEventListener('submit', (event) => {
  event.preventDefault(); if (pending) return;
  try {
    if (!snapshot || !snapshot.target) throw new Error('Save your local pressing profile first.');
    requireRevision(candidateState.revision); const observation=readCandidate();
    const submitted=capture('case-form');
    run(() => request('/api/candidate',JSON.stringify({observation,expected_revision:candidateState.revision,expected_current:candidateState.current})),
      'Candidate observation saved locally. No target settings were changed.',{candidate:submitted,candidateId:observation.id});
  } catch (error) { message(error.message,true); }
});
el('refresh').addEventListener('click', () => run(() => request('/api/snapshot'), 'Saved review refreshed; unsaved edits retained.'));
el('save').addEventListener('click', () => {
  if (pending) return;
  try { parseDraft(); requireRevision(draftRevision); } catch (error) { message(error.message,true); return; }
  const draft=el('draft').value;
  // Preserve original nested JSON so duplicate keys still reach strict server validation.
  const envelope='{"expected_revision":'+draftRevision+',"bundle":'+draft+'}';
  run(() => request('/api/import',envelope), 'Observation bundle saved. The JSON draft is retained.', {draft});
});
el('draft').addEventListener('input', () => { if (!el('draft').value.trim()) draftRevision=snapshot ? snapshot.revision : 0; });
el('profile-to-json').addEventListener('click', () => {
  if (pending || !snapshot || !snapshot.target) return;
  writeDraft({...profileFrom(snapshot),listings:[]}); message('Saved profile copied to advanced JSON with no candidate observations.');
});
el('file').addEventListener('change', async (event) => {
  if (pending) return;
  const file=event.target.files[0]; if (!file) return;
  if (file.size > maxBytes) { message('The file exceeds 256 KiB; your draft is unchanged.',true); return; }
  const previous=el('draft').value; const revision=snapshot ? snapshot.revision : 0; setPending(true);
  try {
    const text=await file.text(); if (bytes(text) > maxBytes) throw new Error('The file exceeds 256 KiB.');
    const input=JSON.parse(text);
    if (!input || ![1,2].includes(input.schema_version) || !Array.isArray(input.listings)) throw new Error('Import an observation bundle, not an exported review record.');
    if (el('draft').value !== previous) throw new Error('Your draft changed while the file loaded; import it again.');
    el('draft').value=text; draftRevision=revision; message('File loaded into the JSON draft. Review it, then save the bundle.');
  } catch (error) { message(error.message,true); }
  finally { setPending(false); event.target.value=''; }
});
el('example').addEventListener('click', () => {
  if (pending) return;
  const time='2026-01-02T03:04:05Z';
  writeDraft({schema_version:2,source:'synthetic',target:{artist:'Example Ensemble',album:'Offline Horizons',
    colors:['Blue'],formats:['LP'],country:'US',release_year:2024,editions:['Limited Edition'],required_components:[]},
    alternatives:[{id:'invented-black',artist:'Example Ensemble',album:'Offline Horizons',colors:['Black'],formats:['LP']}],
    settings:{maximum_subtotal:'30.00',gamble_max:'15.00',currency:'USD',country:'US',postal_code:'00000',
      tells:[{kind:'color',value:'Blue',required:true}]},
    listings:[['blue','blue','20.00'],['unclear','LP','10.00'],['black','black','8.00']].map(([id,color,price]) => ({
      id:`example-${id}`,title:`Example Ensemble Offline Horizons ${color} vinyl`,observed_at:time,
      details_observed_at:time,current_price:price,currency:'USD',shipping_cost:'4.00',shipping_currency:'USD',
      price_kind:'fixed_price',delivery_country:'US',delivery_postal_code:'00000'}))});
  message('Invented example loaded with fixed historical fixture times. Those times are intentionally stale; use actual times only for genuinely new observations.');
});
window.addEventListener('beforeunload', (event) => {
  if (capture('profile-form') !== profileState.clean || capture('case-form') !== candidateState.clean ||
      (comparisonState && capture('comparison-form') !== comparisonState.clean) || el('draft').value !== draftClean) {
    event.preventDefault(); event.returnValue='';
  }
});
hydrateProfile(null); hydrateCandidate();
run(() => request('/api/snapshot'), 'Local workspace ready.');

</script></body></html>"""
