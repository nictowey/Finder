// Real browser rendering of the shipped UI, with synthetic intercepted data only.
import {chromium, webkit} from './cloud_browser/node_modules/playwright/index.mjs';
import fs from 'node:fs';
import assert from 'node:assert/strict';

const source = fs.readFileSync('src/finder/discogs_watch_ui.py', 'utf8');
const html = source.split('PAGE = r"""')[1].split('"""', 1)[0].replaceAll('@@NONCE@@', 'test');
const fixture = {
  csrf: 'synthetic', monitor_enabled: true,
  watches: [{
    release_id: 1, public_title: 'Synthetic Artist · Favourite Record', currency: 'USD',
    item_price_ceiling: '40.00', any_price: false, enabled: true, removed: false, revision: 1,
    alert_initial_low: true, alert_price_drops: true, alert_count_increases: false,
    status: 'ok', last_success: 1794229200, lowest_item_price: '35.00', count: 2,
    notifications: {accepted: 5, accepted_current_revision: 2, pending: 0, in_flight: 0, uncertain: 0},
  }],
};

for (const [engineName, engine] of Object.entries({chromium, webkit})) {
  const browser = await engine.launch({headless: true});
  try {
    for (const width of [390, 320, 960]) {
      const page = await browser.newPage({viewport: {width, height: 844}, isMobile: width < 600, hasTouch: width < 600, serviceWorkers: 'block'});
      let saves = 0, reads = 0, activityReads = 0;
      const activityBodies = [];
      let acceptDialogs = false, saveMode = 'ok', failRead = false, holdSave = false, releaseSave;
      const confirmations = [], saveBodies = [];
      let snapshot = structuredClone(fixture);
      page.on('dialog', async dialog => {
        confirmations.push(dialog.message());
        if (acceptDialogs) await dialog.accept(); else await dialog.dismiss();
      });
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://sniper.example') return route.abort();
        if (url.pathname === '/watches') return route.fulfill({contentType: 'text/html', body: html});
        if (url.pathname === '/watches/state' && route.request().method() === 'GET') {
          reads++;
          if (failRead) return route.fulfill({status: 503, json: {error: 'State unavailable.'}});
          return route.fulfill({json: snapshot});
        }
        if (url.pathname === '/watches/state' && route.request().method() === 'POST') {
          activityReads++;
          const body = route.request().postDataJSON();
          activityBodies.push(body);
          assert.deepEqual(Object.keys(body).sort(), ['action', 'cursor', 'release_id']);
          assert.equal(body.release_id, 1);
          assert.equal(body.action, 'activity');
          const revision = snapshot.watches[0].revision;
          const episode = body.cursor ? body.cursor.episode - 1 : 40;
          return route.fulfill({json: {activity: {release_id: 1, current_revision: revision,
            rows: Array.from({length: 20}, (_, i) => ({revision, episode: episode - i,
              observed_at: 1794229200, state: i ? 'pending' : 'accepted', kind: 'availability', settings_scope: 'current'})),
            has_more: episode > 20, next_cursor: episode > 20 ? {revision, episode: episode - 19} : null}}});
        }
        if (url.pathname === '/watches/save') {
          saves++;
          const body = route.request().postDataJSON();
          saveBodies.push(body);
          if (saveMode === 'conflict') return route.fulfill({status: 409, json: {error: 'Saved watch changed. Refresh saved watches.'}});
          if (body.action === 'preferences') {
            assert.deepEqual(Object.keys(body).sort(), ['action', 'alert_count_increases', 'alert_price_drops', 'csrf', 'release', 'revision']);
            const watch = snapshot.watches.find(w => String(w.release_id) === body.release);
            if (body.revision !== watch.revision) return route.fulfill({status: 409, json: {error: 'Saved watch changed. Refresh saved watches.'}});
            if (watch.alert_price_drops !== body.alert_price_drops || watch.alert_count_increases !== body.alert_count_increases) {
              watch.alert_price_drops = body.alert_price_drops;
              watch.alert_count_increases = body.alert_count_increases;
              watch.revision++;
            }
          } else if (body.action) {
            assert.deepEqual(Object.keys(body).sort(), ['action', 'csrf', 'release', 'revision']);
            const watch = snapshot.watches.find(w => String(w.release_id) === body.release);
            assert.equal(body.revision, watch.revision);
            watch.removed = body.action === 'remove';
            watch.enabled = false;
            watch.revision++;
          }
          if (holdSave) await new Promise(resolve => {releaseSave = resolve;});
          if (saveMode === 'lost') return route.abort('failed');
          if (saveMode === 'unverified') return route.fulfill({json: {saved: false}});
          return route.fulfill({json: {saved: true}});
        }
        return route.abort();
      });
      await page.addInitScript(() => localStorage.setItem('allow-pinch-zoom', 'true'));
      await page.goto('https://sniper.example/watches');
      await page.getByText('Synthetic Artist · Favourite Record').waitFor();
      assert.equal(activityReads, 0);
      await page.locator('[data-activity] summary').click();
      await page.locator('.activity li').first().waitFor();
      assert.equal(await page.locator('.activity li').count(), 20);
      assert.equal(activityReads, 1);
      assert.deepEqual(activityBodies[0], {action: 'activity', release_id: 1, cursor: null});
      await page.locator('[data-activity-older]').focus();
      await page.keyboard.press('Enter');
      await page.getByText('End of retained alert records', {exact: true}).waitFor();
      assert.equal(await page.locator('.activity li').count(), 20);
      assert.equal(await page.locator('[data-activity-older]').isDisabled(), true);
      assert.match(await page.locator('[data-activity]').innerText(), /Observation used:/);
      assert.match(await page.locator('[data-activity]').innerText(), /Not confirmed phone delivery/);
      assert.equal(await page.locator('[data-activity-status]').getAttribute('aria-live'), 'polite');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.locator('[data-activity-refresh]').click();
      await page.getByText('Page 1 · Older retained records available.', {exact: true}).waitFor();
      assert.equal(activityBodies.at(-1).cursor, null);
      await page.locator('[data-activity] summary').click();
      assert.equal(saves, 0);
      // These are computed-style/layout checks, not proof of physical iPhone autozoom.
      const viewport = await page.locator('meta[name="viewport"]').getAttribute('content');
      assert.match(viewport, /viewport-fit=cover/);
      assert.doesNotMatch(viewport, /user-scalable|maximum-scale|minimum-scale/);
      const fields = await page.locator('input,select').evaluateAll(nodes => nodes.map(node => ({
        id: node.id, font: parseFloat(getComputedStyle(node).fontSize), labeled: node.labels.length > 0,
      })));
      for (const field of fields) {
        assert.ok(field.font >= 16, field.id + ' has a readable computed font');
        assert.ok(field.labeled, field.id + ' has an accessible label');
      }
      for (const selector of ['html', 'body']) assert.equal(await page.locator(selector).evaluate(node => getComputedStyle(node).touchAction), 'pan-x pan-y');
      assert.equal(await page.locator('#allow-pinch-zoom,#zoom-option').count(), 0);
      if (engineName === 'chromium' && width === 390) {
        // Actual two-contact input through Chromium, not a synthetic DOM event or
        // forced page-scale change. Positive control must zoom with a temporary test-only CSS override.
        // This does not establish physical iPhone Safari/PWA gesture behavior.
        const cdp = await page.context().newCDPSession(page);
        const contacts = distance => [
          {id: 1, x: width / 2 - distance, y: 400},
          {id: 2, x: width / 2 + distance, y: 400},
        ];
        const pinch = async () => {
          await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: contacts(30)});
          for (let distance = 36; distance <= 126; distance += 6) {
            await cdp.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: contacts(distance)});
            await page.waitForTimeout(20);
          }
          await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
          await page.waitForTimeout(250);
        };
        const initialScale = await page.evaluate(() => visualViewport.scale);
        await pinch();
        assert.ok(Math.abs(await page.evaluate(() => visualViewport.scale) - initialScale) < 0.02, 'pan-only mode suppresses pinch');
        const zoomControl = await page.addStyleTag({content: 'html,body{touch-action:auto!important}'});
        try {
          await pinch();
          assert.ok(await page.evaluate(() => visualViewport.scale) > initialScale + 0.2, 'positive control: test-only override permits real pinch zoom');
        } finally {
          await zoomControl.evaluate(node => node.remove());
        }
        for (const selector of ['html', 'body']) assert.equal(await page.locator(selector).evaluate(node => getComputedStyle(node).touchAction), 'pan-x pan-y');
        // Reset only after measuring the real gesture, to keep later tests independent.
        await cdp.send('Emulation.setPageScaleFactor', {pageScaleFactor: 1});
        await page.evaluate(() => scrollTo(0, 0));
        await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{id: 1, x: 12, y: 700}]});
        for (let y = 680; y >= 300; y -= 20) {
          await cdp.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{id: 1, x: 12, y}]});
          await page.waitForTimeout(20);
        }
        await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
        await page.waitForFunction(() => scrollY > 50);
        await page.waitForTimeout(300);
        await page.evaluate(() => scrollTo(0, 0));
        await page.locator('#nav-add').tap();
        await page.locator('#add-view').waitFor({state: 'visible'});
        assert.equal(await page.locator('#nav-add').getAttribute('aria-current'), 'page');
        await page.locator('#nav-watches').tap();
        await page.locator('#watches-view').waitFor({state: 'visible'});
        assert.equal(await page.locator('#nav-watches').getAttribute('aria-current'), 'page');
        assert.equal(saves, 0);
        await cdp.detach();
      }
      assert.equal(await page.locator('#search').evaluate(node => getComputedStyle(node).touchAction), 'manipulation');
      await page.locator('#search').focus();
      assert.equal(await page.locator('#search').evaluate(node => node === document.activeElement), true);
      assert.equal(await page.locator('#search').evaluate(node => getComputedStyle(node).outlineStyle), 'solid');

      assert.equal(await page.locator('#add-view').isVisible(), false);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator('.check-summary').isVisible(), true);
      assert.equal(await page.locator('.check-summary time').isVisible(), true);
      assert.equal(await page.locator('details .check-summary').count(), 0);
      assert.match(await page.locator('#monitor-status').innerText(), /setting: enabled · running status not verified/);
      assert.match(await page.locator('.card > .meta').innerText(), /Last observed item minimum/);
      assert.match(await page.locator('.card > .meta').innerText(), /Last observed copies for sale/);
      assert.match(await page.locator('.notice').innerText(), /No media\/sleeve grade or US-seller filtering/);
      assert.equal(await page.locator('.notice').isVisible(), true);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-watches.png`, fullPage: true});
      // Presentation controls use only the current synthetic snapshot, never a save.
      snapshot.watches.push({...structuredClone(fixture.watches[0]), release_id: 2, public_title: 'Alpha', enabled: false, last_success: 1794229300});
      snapshot.watches.push({...structuredClone(fixture.watches[0]), release_id: 3, public_title: 'Unknown', last_success: null});
      snapshot.watches.push({...structuredClone(fixture.watches[0]), release_id: 4, public_title: 'Removed', enabled: false, removed: true});
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      const visibleReleases = () => page.locator('#watches [data-release]').evaluateAll(nodes => nodes.map(node => Number(node.dataset.release)));
      await page.locator('#watch-sort').selectOption('name');
      assert.deepEqual(await visibleReleases(), [2, 1, 3]);
      await page.locator('#watch-sort').selectOption('checked');
      assert.deepEqual(await visibleReleases(), [2, 1, 3]);
      await page.locator('#watch-filter').selectOption('paused');
      assert.deepEqual(await visibleReleases(), [2]);
      assert.equal(await page.locator('#shown-count').innerText(), 'Showing 1 of 3 watches');
      await page.locator('#search').fill('missing');
      assert.deepEqual(await visibleReleases(), []);
      await page.getByRole('button', {name: 'Clear search and filters'}).click();
      assert.deepEqual(await visibleReleases(), [2, 1, 3]);
      await page.locator('#watch-filter').selectOption('enabled');
      await page.locator('#search').fill('Synthetic');
      await page.locator('#nav-add').click();
      await page.locator('#title').fill('Filter navigation draft');
      await page.locator('#nav-watches').click();
      await page.goBack();
      await page.locator('#add-view').waitFor({state: 'visible'});
      assert.equal(await page.locator('#title').inputValue(), 'Filter navigation draft');
      await page.goForward();
      await page.locator('#watches-view').waitFor({state: 'visible'});
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      assert.equal(await page.locator('#watch-filter').inputValue(), 'enabled');
      assert.equal(await page.locator('#watch-sort').inputValue(), 'checked');
      assert.equal(await page.locator('#search').inputValue(), 'Synthetic');
      assert.deepEqual(await visibleReleases(), [1]);
      assert.equal(saves, 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      for (const id of ['watch-filter', 'watch-sort']) assert.ok((await page.locator('#' + id).boundingBox()).height >= 44);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-filters.png`, fullPage: true});
      await page.locator('#watch-filter').selectOption('all');
      await page.locator('#watch-sort').selectOption('release');
      await page.locator('#search').fill('');
      const states = [
        {name: 'waiting', watch: {status: 'waiting', last_success: null, lowest_item_price: null, count: null}, result: 'No result recorded for current settings'},
        {name: 'error', watch: {status: 'error'}, result: 'Failed'},
        {name: 'unknown', watch: {status: 'unknown'}, result: 'Result could not be confirmed'},
        {name: 'paused', watch: {enabled: false}, result: 'Successful'},
        {name: 'disabled', watch: {}, monitor: false, result: 'Successful'},
      ];
      for (const scenario of states) {
        snapshot = structuredClone(fixture);
        Object.assign(snapshot.watches[0], scenario.watch);
        snapshot.monitor_enabled = scenario.monitor ?? true;
        const beforeReads = reads;
        await page.locator('#refresh').click();
        await page.waitForFunction(expected => document.querySelector('.check-summary').textContent.includes('Latest recorded check: ' + expected), scenario.result);
        // Some scenarios retain the previous result, so wait for the whole refresh too.
        await page.locator('#refresh:not(:disabled)').waitFor();
        assert.equal(reads, beforeReads + 1);
        assert.equal(saves, 0);
        assert.equal(await page.locator('.check-summary').isVisible(), true);
        assert.equal(await page.locator('.check-summary time').count(), scenario.name === 'waiting' ? 0 : 1);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (scenario.name === 'paused') assert.match(await page.locator('.check-summary').innerText(), /Watch paused/);
        if (scenario.name === 'disabled') {
          assert.match(await page.locator('#monitor-status').innerText(), /Monitor setting: disabled/);
          assert.match(await page.locator('.check-summary').innerText(), /Monitor disabled/);
        }
        await page.screenshot({path: `/artifacts/${engineName}-${width}-${scenario.name}.png`, fullPage: true});
      }
      snapshot = structuredClone(fixture);
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      await page.locator('#nav-add').click();
      await page.locator('#release').fill('99');
      await page.locator('#title').fill('My draft');
      await page.locator('#price').fill('12.00');
      for (const id of ['release', 'title', 'price', 'currency']) {
        await page.locator('#' + id).focus();
        assert.equal(await page.locator('#' + id).evaluate(node => node === document.activeElement), true);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      }
      // Simulate enlarged form text without disabling browser zoom or mutating saved data.
      const largeText = await page.addStyleTag({content: 'input,select{font-size:24px!important}'});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      for (const selector of ['summary', 'label.check']) {
        for (const box of await page.locator('#add-view ' + selector).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height))) assert.ok(box >= 44);
      }
      await largeText.evaluate(node => node.remove());

      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-add.png`, fullPage: true});
      await page.locator('#nav-watches').click();
      await page.goBack();
      await page.locator('#add-view').waitFor({state: 'visible'});
      assert.equal(await page.locator('#title').inputValue(), 'My draft');
      await page.goForward();
      await page.locator('#watches-view').waitFor({state: 'visible'});
      assert.equal(await page.locator('#watches-view').isVisible(), true);
      await page.locator('#nav-add').click();
      await page.locator('#save').click();
      await page.waitForURL(url => url.hash === '#watches');
      assert.equal(saves, 1);
      await page.locator('[data-action=edit]').click();
      await page.locator('#title').waitFor({state: 'visible'});
      assert.equal(await page.locator('#release').inputValue(), '1');
      await page.locator('#cancel').click();
      await page.waitForURL(url => url.hash === '#watches');
      await page.locator('#add-view').waitFor({state: 'hidden'});
      assert.equal(await page.locator('#add-view').isVisible(), false);
      // Reversible removal and recovery use synthetic state only. No real watch is changed.
      snapshot = structuredClone(fixture);
      snapshot.watches.push({...structuredClone(snapshot.watches[0]), release_id: 2, public_title: 'Synthetic Paused Record', enabled: false, revision: 4});
      snapshot.watches.push({...structuredClone(snapshot.watches[0]), release_id: 3, public_title: 'Synthetic Removed Record', enabled: false, removed: true, revision: 7});
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      assert.match(await page.locator('#count').innerText(), /1 enabled · 1 paused/);
      assert.equal(await page.locator('#watches .card').count(), 2);
      assert.equal(await page.locator('#removed-watches .card').count(), 1);
      await page.locator('#nav-add').click();
      await page.locator('#release').fill('99');
      await page.locator('#title').fill('Keep my unsaved removal draft');
      await page.locator('#price').fill('12');
      await page.locator('#nav-watches').click();
      const beforeRemove = saves;
      await page.locator('#watches [data-release="1"] [data-action="remove"]').click();
      assert.equal(saves, beforeRemove);
      assert.match(confirmations.at(-1), /Synthetic Artist · Favourite Record/);
      assert.match(confirmations.at(-1), /Discogs release 1/);
      assert.match(confirmations.at(-1), /restoring keeps the watch paused/);
      acceptDialogs = true;
      holdSave = true;
      await page.locator('#watches [data-release="1"] [data-action="remove"]').click();
      await page.waitForFunction(() => document.querySelector('#refresh').disabled);
      await page.locator('#watches [data-release="1"] [data-action="remove"]').evaluate(button => button.click());
      while (!releaseSave) await new Promise(resolve => setTimeout(resolve, 5));
      assert.equal(saves, beforeRemove + 1);
      holdSave = false;
      releaseSave();releaseSave = undefined;
      await page.locator('#refresh:not(:disabled)').waitFor();
      assert.deepEqual(saveBodies.at(-1), {action: 'remove', release: '1', revision: 1, csrf: 'synthetic'});
      assert.match(await page.locator('#status').innerText(), /^Watch removed/);
      assert.match(await page.locator('#count').innerText(), /0 enabled · 1 paused/);
      assert.equal(await page.locator('#watches [data-release="1"]').count(), 0);
      await page.locator('#nav-removed').click();
      assert.equal(await page.locator('#removed-watches .card').count(), 2);
      assert.equal(await page.locator('#removed-watches [data-action="toggle"]').count(), 0);
      assert.equal(await page.locator('#removed-watches [data-action="edit"]').count(), 0);
      assert.match(await page.locator('#removed-watches [data-release="1"]').textContent(), /not confirmed phone delivery/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-removed.png`, fullPage: true});
      await page.goBack();
      await page.locator('#watches-view').waitFor({state: 'visible'});
      await page.goForward();
      await page.locator('#removed-view').waitFor({state: 'visible'});
      assert.equal(saves, beforeRemove + 1);
      await page.locator('#removed-search').fill('no match');
      assert.match(await page.locator('#removed-watches').innerText(), /No removed watches match/);
      await page.locator('#removed-search').fill('1');
      await page.locator('#removed-watches [data-release="1"] [data-action="restore"]').click();
      await page.locator('#refresh-removed:not(:disabled)').waitFor();
      assert.equal(saves, beforeRemove + 2);
      assert.deepEqual(saveBodies.at(-1), {action: 'restore', release: '1', revision: 2, csrf: 'synthetic'});
      assert.match(await page.locator('#status').innerText(), /restored and paused/);
      await page.locator('#nav-watches').click();
      assert.equal(await page.locator('#watches [data-release="1"] .pill').innerText(), 'Paused');
      assert.equal(await page.locator('#watches [data-release="1"] [data-action="toggle"]').innerText(), 'Resume');
      await page.screenshot({path: `/artifacts/${engineName}-${width}-restored-paused.png`, fullPage: true});
      await page.locator('#nav-add').click();
      assert.equal(await page.locator('#title').inputValue(), 'Keep my unsaved removal draft');
      await page.locator('#release').fill('https://www.discogs.com/release/3-Synthetic');
      assert.equal(await page.locator('#removed-hint').isVisible(), true);
      assert.equal(await page.locator('#save').isDisabled(), true);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-removed-duplicate.png`, fullPage: true});
      await page.locator('#view-removed').click();
      await page.locator('#removed-view').waitFor({state: 'visible'});
      assert.equal(await page.locator('#removed-search').inputValue(), '3');
      assert.equal(saves, beforeRemove + 2);
      // Lost POST: the state may already have changed. No second POST until a fresh decision.
      saveMode = 'lost';
      await page.locator('#removed-watches [data-release="3"] [data-action="restore"]').click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('No automatic retry'));
      assert.equal(saves, beforeRemove + 3);
      assert.equal(await page.locator('#removed-watches [data-action="restore"]').isDisabled(), true);
      assert.match(await page.locator('#status').innerText(), /check whether it was restored/);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-restore-uncertain.png`, fullPage: true});
      saveMode = 'ok';
      await page.locator('#refresh-removed').click();
      await page.locator('#refresh-removed:not(:disabled)').waitFor();
      assert.equal(saves, beforeRemove + 3);
      assert.equal(await page.locator('#removed-watches .card').count(), 0);
      await page.locator('#nav-watches').click();
      assert.equal(await page.locator('#watches [data-release="3"] .pill').innerText(), 'Paused');
      // Stale revision: keep the state and require manual refresh, without replay.
      saveMode = 'conflict';
      const beforeConflictReads = reads;
      await page.locator('#watches [data-release="1"] [data-action="remove"]').click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Saved watch changed'));
      assert.equal(saves, beforeRemove + 4);
      assert.equal(reads, beforeConflictReads);
      assert.equal(await page.locator('#watches [data-release="1"] [data-action="remove"]').isDisabled(), true);
      snapshot.watches[0].revision++;
      saveMode = 'ok';
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      // A confirmed removal remains confirmed even when the next read fails.
      failRead = true;
      await page.locator('#watches [data-release="1"] [data-action="remove"]').click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Watch removed. State unavailable.'));
      assert.equal(saves, beforeRemove + 5);
      assert.equal(await page.locator('#watches [data-release="1"] [data-action="remove"]').isDisabled(), true);
      assert.equal(saveBodies.at(-1).revision, 4);
      failRead = false;
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      assert.equal(saves, beforeRemove + 5);
      await page.locator('#nav-add').click();
      assert.equal(await page.locator('#title').inputValue(), 'Keep my unsaved removal draft');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // Separate preference flow on a retained synthetic paused watch. The
      // existing removal/edit draft remains intact throughout.
      await page.locator('#nav-watches').click();
      await page.locator('#search').fill('');
      await page.locator('#watch-filter').selectOption('all');
      const preferenceCard = '#watches [data-release="3"]';
      const countChoice = preferenceCard + ' [data-preference="alert_count_increases"]';
      const applyPreferences = preferenceCard + ' [data-action="preferences"]';
      const resetPreferences = preferenceCard + ' [data-action="reset-preferences"]';
      const openPreferences = async () => {
        const panel = page.locator(preferenceCard + ' [data-preference-panel]');
        if (!(await panel.evaluate(node => node.open))) await panel.locator('summary').click();
      };
      await openPreferences();
      const originalCount = snapshot.watches.find(w => w.release_id === 3).alert_count_increases;
      const beforePreferences = saves;
      await page.locator(countChoice).setChecked(!originalCount);
      assert.equal(saves, beforePreferences);
      for (const filter of ['enabled', 'paused', 'all']) await page.locator('#watch-filter').selectOption(filter);
      for (const sort of ['name', 'checked', 'release']) await page.locator('#watch-sort').selectOption(sort);
      await page.locator('#search').fill('no matches');
      await page.locator('#search').fill('');
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      await openPreferences();
      assert.equal(await page.locator(countChoice).isChecked(), !originalCount);
      await page.locator(resetPreferences).click();
      await openPreferences();
      assert.equal(await page.locator(countChoice).isChecked(), originalCount);
      await page.locator(countChoice).setChecked(!originalCount);
      holdSave = true;
      await page.locator(applyPreferences).click();
      await page.waitForFunction(() => document.querySelector('#refresh').disabled);
      await page.locator(applyPreferences).evaluate(button => button.click());
      while (!releaseSave) await new Promise(resolve => setTimeout(resolve, 5));
      assert.equal(saves, beforePreferences + 1);
      holdSave = false; releaseSave(); releaseSave = undefined;
      await page.locator('#refresh:not(:disabled)').waitFor();
      await openPreferences();
      assert.match(await page.locator('#status').innerText(), /Alert preferences saved/);
      assert.equal(await page.locator(countChoice).isChecked(), !originalCount);
      await page.locator(countChoice).setChecked(originalCount);
      const draftRevision = snapshot.watches.find(w => w.release_id === 3).revision;
      snapshot.watches.find(w => w.release_id === 3).revision++;
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      await openPreferences();
      await page.locator(applyPreferences).click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('Saved watch changed'));
      assert.equal(saveBodies.at(-1).revision, draftRevision);
      assert.equal(saves, beforePreferences + 2);
      assert.equal(await page.locator(applyPreferences).isDisabled(), true);
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      await openPreferences();
      assert.equal(await page.locator(countChoice).isChecked(), originalCount);
      await page.locator(resetPreferences).click();
      await openPreferences();
      await page.locator(countChoice).setChecked(originalCount);
      saveMode = 'lost';
      await page.locator(applyPreferences).click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('No automatic retry'));
      assert.equal(saves, beforePreferences + 3);
      assert.equal(await page.locator(applyPreferences).isDisabled(), true);
      saveMode = 'ok';
      await page.locator('#refresh').click();
      await page.locator('#refresh:not(:disabled)').waitFor();
      await openPreferences();
      assert.equal(saves, beforePreferences + 3);
      await page.locator(resetPreferences).click();
      await openPreferences();
      assert.equal(await page.locator(countChoice).isChecked(), originalCount);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({path: `/artifacts/${engineName}-${width}-preferences.png`, fullPage: true});
      await page.locator('#nav-add').click();
      assert.equal(await page.locator('#title').inputValue(), 'Keep my unsaved removal draft');
      await page.close();
      console.log(`PASS ${engineName} width ${width}: search, status filters, stable sorting, check results, navigation and Back/Forward, readable input fonts and labels, focus, enlarged text, unrestricted zoom metadata, draft guards, remove cancel and repeated click, removed recovery, paused restore, duplicate add, lost response, revision conflict and failed read without replay, preference save/reset/filter/draft/conflict/uncertain recovery, overflow`);
    }
  } finally {
    await browser.close();
  }
}
