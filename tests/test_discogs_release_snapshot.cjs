'use strict';
// Synthetic marketplace DOM only. No browser/network/provider or owner targets.
const assert = require('node:assert/strict');
const { test, before } = require('node:test');
const { JSDOM } = require('jsdom');
let collectReleaseMarketplaceDom;
before(async () => { ({ collectReleaseMarketplaceDom } = await import('../scripts/discogs_release_snapshot.mjs')); });
const URL = 'https://www.discogs.com/sell/release/111';
const pagination = '<nav class="pagination"><span class="pagination_total">1 – 2 of 2</span><form action="/sell/release/111"><select name="sort"><option value="price">Price</option></select><select name="limit"><option value="25" selected>25</option></select></form></nav>';
function row(id) { return `<tr><td class="item_description"><a class="item_description_title" href="/sell/item/${id}">Synthetic album</a><a class="item_release_link" href="/release/111">Release</a><p class="item_condition"><span>Very Good Plus (VG+) <span class="has-tooltip">Help</span></span><span class="item_sleeve_condition">Sleeve: Very Good Plus (VG+)</span></p></td><td class="seller_info"><a href="/seller/synthetic/profile">Synthetic seller</a><strong>Seller display</strong><li><strong>100.0%</strong><a class="section_link">100 ratings</a></li><strong>Promo</strong></td><td class="item_price"><span class="price" data-currency="USD">$20.00</span><span class="item_shipping">+$5.00 shipping</span></td></tr>`; }
function html() { return `${pagination}<div id="pjax_container"><table class="mpitems"><thead><tr><th>Item</th></tr></thead><tbody>${row('222')}${row('333')}</tbody></table></div>${pagination}`; }
function read({ markup = html(), url = URL, id = '111', page = 1, alter } = {}) {
  const dom = new JSDOM(markup, { url, runScripts: 'outside-only' });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
  dom.window.HTMLElement.prototype.getClientRects = function () { return [{width: 10,height: 10}]; };
  Object.defineProperty(dom.window.document, 'readyState', { value: 'complete', configurable: true });
  if (alter) alter(dom.window);
  const collector = dom.window.eval(`(${collectReleaseMarketplaceDom.toString()})`);
  const output = JSON.parse(JSON.stringify(collector(page === 1 ? id : { id, page }))); dom.window.close(); return output;
}
function blocked(result) { assert.equal(result.complete,false);assert.notEqual(result.status,'observed');assert.deepEqual(result.rows,[]);assert.equal(result.purchase_allowed,false); }
test('serialized collector reads exact release rows and matching complete counts without actions', () => {
  const result=read();assert.equal(result.complete,true);assert.equal(result.release_id,111);assert.equal(result.total,2);
  assert.deepEqual(result.rows.map(r=>r.listing_id),['222','333']);assert.equal(result.rows[0].fields.price,'$20.00 USD');
  assert.equal(result.rows[0].fields.seller,'Synthetic seller');assert.equal(result.rows[0].fields.media,'Very Good Plus (VG+)' );
  assert.equal(result.has_next,false);assert.equal(result.purchase_allowed,false);assert.ok(Object.values(result.assertions).every(Boolean));
});
test('strict release URL and ID prevent collecting another release or query-filtered page', () => {
  for (const url of [URL+'?page=1',URL+'?condition=Mint',URL+'#x',URL.replace('111','112'),URL.replace('https:','http:'),URL.replace('www.discogs.com','evil.test'),URL.replace('/111','/%31%31%31')]) blocked(read({url}));
  for(const id of ['0','111 ','1e2','9007199254740992',111,null]) blocked(read({id}));
  assert.equal(read({url:URL+'/'}).complete,true);
});
test('verified empty shape requires matching zero counts, table header, page size and release forms', () => {
  const markup=html().replace(row('222'),'').replace(row('333'),'').replaceAll('1 – 2 of 2','0 – 0 of 0');
  const result=read({markup});assert.equal(result.complete,true);assert.equal(result.total,0);assert.deepEqual(result.rows,[]);assert.equal(result.purchase_allowed,false);
  blocked(read({markup:markup.replace(/<nav[^>]*>[\s\S]*?<\/nav>/,'')}));
});
test('partial pages, mismatched counts, missing counts and additional pages never complete', () => {
  const partial = read({ markup: html().replaceAll('1 – 2 of 2', '1 – 2 of 3')
    .replaceAll('</nav>', '<a rel="next" href="/sell/release/111?page=2">Next</a></nav>') });
  assert.equal(partial.status, 'incomplete'); assert.equal(partial.complete, false);
  assert.equal(partial.error_code, 'additional_page_required'); assert.equal(partial.has_next, true);
  assert.deepEqual([partial.range_start, partial.range_end, partial.total], [1, 2, 3]);
  assert.deepEqual(partial.rows.map(row => row.listing_id), ['222', '333']);
  assert.equal(partial.rows[0].fields.price, '$20.00 USD');
  assert.ok(Object.values(partial.assertions).every(Boolean));
  assert.equal(partial.purchase_allowed, false);
  for(const markup of [html().replace('1 – 2 of 2','1 – 1 of 1'),html().replace(pagination,''),html().replace('</nav>','<a rel="next">Next</a></nav>'),html().replace('</nav>','<button aria-label="Next page">›</button></nav>')]) blocked(read({markup}));
});
test('partial report still rejects wrong identity, bad rows, inconsistent range and mutation', () => {
  const markup = html().replaceAll('1 – 2 of 2', '1 – 2 of 3');
  for (const changed of [markup.replace('/release/111','/release/112'),
    markup.replace('/sell/item/333','/sell/item/222'),
    markup.replace('data-currency="USD"','data-currency="?"'),
    markup.replace(row('333'),''),
    markup.replace('1 – 2 of 3','2 – 3 of 3')]) blocked(read({markup:changed}));
  const late=read({markup,alter:w=>{const labels=w.document.querySelectorAll('.pagination_total');
    let reads=0;Object.defineProperty(labels[0],'textContent',{get(){reads++;return reads===1?'1 – 2 of 3':'1 – 2 of 4';}});}});
  blocked(late);assert.equal(late.error_code,'observation_changed');
});
test('sanitized loaded-page shape reports 25 of 47 distinct rows without complete coverage', () => {
  // Structure from a user-opened local Rocky page; IDs and seller fields are synthetic.
  const nav = pagination.replace('1 – 2 of 2', '1 – 25 of 47')
    .replace('</nav>', '<a rel="next" href="/sell/release/111?page=2">Next</a></nav>');
  const rows = Array.from({length: 25}, (_, index) => row(String(1000 + index))).join('');
  const markup = `${nav}<div id="pjax_container"><table class="mpitems"><thead><tr><th>Item</th></tr></thead><tbody>${rows}</tbody></table></div>${nav}`;
  const result = read({markup});
  assert.equal(result.status, 'incomplete'); assert.equal(result.complete, false);
  assert.deepEqual([result.range_start, result.range_end, result.total], [1, 25, 47]);
  assert.equal(result.rows.length, 25); assert.equal(new Set(result.rows.map(r => r.listing_id)).size, 25);
  assert.equal(result.has_next, true); assert.equal(result.purchase_allowed, false);
});
test('bounded next-page identity is read from matching visible controls and later rows stay fragments', () => {
  const first = read({markup:html().replaceAll('1 – 2 of 2', '1 – 2 of 3')
    .replaceAll('</nav>', '<a rel="next" href="/sell/release/111?page=2">Next</a></nav>')});
  assert.equal(first.next_url, URL + '?page=2');
  const secondNav = pagination.replace('1 – 2 of 2', '3 – 3 of 3');
  const secondMarkup = `${secondNav}<div id="pjax_container"><table class="mpitems"><thead><tr><th>Item</th></tr></thead><tbody>${row('444')}</tbody></table></div>${secondNav}`;
  const second = read({markup: secondMarkup, url: URL + '?page=2', page: 2});
  assert.deepEqual([second.page, second.range_start, second.range_end, second.total], [2, 3, 3, 3]);
  assert.equal(second.complete, false); assert.equal(second.has_next, false);
  assert.deepEqual(second.rows.map(value => value.listing_id), ['444']);
  blocked(read({markup: secondMarkup, url: URL + '?page=2'}));
  blocked(read({markup: secondMarkup, url: URL + '?page=3', page: 2}));
  blocked(read({markup: html().replaceAll('1 – 2 of 2', '1 – 2 of 3')
    .replaceAll('</nav>', '<a rel="next" href="https://other.example/sell/release/111?page=2">Next</a></nav>')}));
});
test('missing/duplicate/extra listing rows fail closed even when displayed counts match', () => {
  for(const markup of [html().replace(row('333'),''),html().replace('/sell/item/333','/sell/item/222'),html().replace('</tbody>',row('444')+'</tbody>')]) blocked(read({markup}));
});
test('every row must carry exact selected release identity, not matching title or master', () => {
  for(const markup of [html().replace('/release/111','/release/112'),html().replace('/release/111','/master/111'),html().replace('/release/111','https://evil.test/release/111'),html().replace('/release/111','/release/111?other=112')]) blocked(read({markup}));
});
test('loading/error/missing containers and hidden required fields never become zero', () => {
  for(const markup of [html().replace('id="pjax_container"','id="pjax_container" aria-busy="true"'),'<p>Service unavailable</p>',html().replace('class="mpitems"','class="other"'),html().replace('class="price"','class="price" hidden'),html().replace('<strong>100.0%</strong>','')]) blocked(read({markup}));
  blocked(read({alter:w=>Object.defineProperty(w.document,'readyState',{value:'loading'})}));
});
test('duplicate required fields or wrong listing href never silently select the first', () => {
  for(const markup of [html().replace('100.0%</strong>','100.0%</strong><strong>99%</strong>'),html().replace('/sell/item/222','/sell/item/222?x=1'),html().replace('class="price" data-currency="USD"','class="price" data-currency="US$"')]) blocked(read({markup}));
});
test('optional shipping absence remains unknown and never becomes a free delivered quote', () => {
  const r=read({markup:html().replaceAll('<span class="item_shipping">+$5.00 shipping</span>','')});assert.equal(r.complete,true);assert.equal(r.rows[0].fields.shipping,null);assert.equal(r.purchase_allowed,false);
});
test('exceptions and oversized fields return bounded failure, never raw page data', () => {
  const secret='x'.repeat(500);const r=read({markup:html().replace('Synthetic seller',secret)});blocked(r);assert.ok(!JSON.stringify(r).includes(secret));
  const failure=read({alter:w=>{w.document.querySelectorAll=()=>{throw Error('PRIVATE')};}});blocked(failure);assert.ok(!JSON.stringify(failure).includes('PRIVATE'));
});
test('mutation during bounded read is detected rather than committed as complete', () => {
  const r=read({alter:w=>{const counts=w.document.querySelectorAll('.pagination_total');let reads=0;Object.defineProperty(counts[0],'textContent',{get(){reads++;return reads===1?'1 – 2 of 2':'1 – 3 of 3';}});}});blocked(r);assert.equal(r.error_code,'observation_changed');
});

test('verified responsive hidden pagination and numeric release slug remain structurally checked', () => {
  const markup=html().replace('<nav class="pagination">','<nav class="pagination" style="display:none">').replaceAll('/release/111"','/release/111-Synthetic-Album"');
  // Keep form actions canonical while only release detail links have a slug.
  const fixed=markup.replaceAll('action="/sell/release/111-Synthetic-Album"','action="/sell/release/111"');
  assert.equal(read({markup:fixed}).complete,true);
  blocked(read({markup:fixed.replace('1 – 2 of 2','1 – 1 of 1')}));
  blocked(read({markup:html().replaceAll('class="pagination"','class="pagination" style="display:none"')}));
});
test('page size is the named limit select, never the sort select or an arbitrary first control', () => {
  blocked(read({markup:html().replaceAll('name="limit"','name="other"')}));
  blocked(read({markup:html().replace('value="25"','value="1"')}));
  blocked(read({markup:html().replace('action="/sell/release/111"','action="/sell/release/112"')}));
});
test('same-count row replacement during observation is rejected', () => {
  const r=read({alter:w=>{const seller=w.document.querySelector('td.seller_info');Object.defineProperty(seller.querySelector('li strong'),'innerText',{get(){w.document.querySelectorAll('a.item_description_title')[1].setAttribute('href','/sell/item/444');return '100%';}});}});
  // This change precedes row two's observation, so it is a consistent new snapshot, not a race.
  assert.equal(r.complete,true); assert.deepEqual(r.rows.map(row=>row.listing_id),['222','444']);
  const late=read({alter:w=>{const sellers=w.document.querySelectorAll('td.seller_info');Object.defineProperty(sellers[1].querySelector('li strong'),'innerText',{get(){w.document.querySelector('a.item_description_title').setAttribute('href','/sell/item/444');return '100%';}});}});
  blocked(late);assert.equal(late.error_code,'observation_changed');
});

test('only the verified responsive release identity anchor may be structurally hidden', () => {
  const hidden=html().replaceAll('class="item_release_link"','class="item_release_link" style="display:none"');
  assert.equal(read({markup:hidden}).complete,true);
  blocked(read({markup:hidden.replace('/release/111','/release/112')}));
  blocked(read({markup:hidden.replace('class="item_release_link"','class="unrelated"')}));
  blocked(read({markup:hidden.replace('Release</a>','Release</a><a class="item_release_link" href="/release/111">Duplicate</a>')}));
  blocked(read({markup:html().replace('100.0%','Loading...')}));
});

test('direct grade nodes exclude nested help descriptions and reject ambiguous or unknown grades', () => {
  const help='<span class="help">'+('Long grading guidance. '.repeat(30))+'</span>';
  const markup=html().replaceAll('>Help</span>','>'+('Long grading guidance. '.repeat(30))+'</span>').replaceAll('Sleeve: Very Good Plus (VG+)</span>','Sleeve: Very Good Plus (VG+)'+help+'</span>');
  const r=read({markup});assert.equal(r.complete,true);assert.equal(r.rows[0].fields.media,'Very Good Plus (VG+)');assert.equal(r.rows[0].fields.sleeve,'Very Good Plus (VG+)');
  blocked(read({markup:html().replace('<span>Very Good Plus (VG+) ','<span>Mint (M) Very Good Plus (VG+) ')}));
  blocked(read({markup:html().replace('<span>Very Good Plus (VG+) ','<span>Unknown ')}));
});
test('media value span and feedback list scope exclude unrelated labels, bold seller name and promotion', () => {
  const markup=html().replaceAll('<p class="item_condition">','<p class="item_condition"><span class="desktop-label">Media:</span><span class="mobile-label">Condition:</span>').replaceAll('+$5.00 shipping</span>','+$5.00 shipping</span><span class="item_shipping" style="display:none">Hidden responsive shipping</span>');
  const r=read({markup});assert.equal(r.complete,true);assert.equal(r.rows[0].fields.media,'Very Good Plus (VG+)');assert.equal(r.rows[0].fields.seller_rating,'100.0%');assert.equal(r.rows[0].fields.shipping,'+$5.00 shipping');
  blocked(read({markup:html().replace('<span>Very Good Plus','<span class="unknown">Very Good Plus')}));
  blocked(read({markup:html().replace('<li><strong>100.0%</strong>','<li><strong>Unavailable</strong>')}));
});


test('bounded verified seller description remains untrusted data independent of VG+ grade', () => {
  const comment='<p class="hide_mobile">NO DISC 1, disc 2 only.</p>';
  const markup=html().replace('</p></td>', '</p>'+comment+'</td>');
  const result=read({markup}); assert.equal(result.complete,true);
  assert.equal(result.rows[0].fields.seller_description,'NO DISC 1, disc 2 only.');
  assert.equal(result.rows[0].fields.media,'Very Good Plus (VG+)');
  assert.equal(result.rows[1].fields.seller_description,null);
  assert.equal(result.purchase_allowed,false);
  for(const changed of [comment.replace('class="hide_mobile"','class="hide_mobile" hidden'),
    comment+comment, comment.replace('NO DISC 1, disc 2 only.','x'.repeat(2001)),
    comment.replace('NO DISC 1, disc 2 only.','Includes both <a>read more</a>'),
    comment.replace('class="hide_mobile"','class="hide_mobile" style="overflow:hidden"')]) {
    const r=read({markup:markup.replace(comment,changed)}); assert.equal(r.complete,true);
    assert.equal(r.rows[0].fields.seller_description,null);
  }
});
