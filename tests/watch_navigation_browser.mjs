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
    item_price_ceiling: '40.00', any_price: false, enabled: true, revision: 1,
    alert_initial_low: true, alert_price_drops: true, alert_count_increases: false,
    status: 'ok', last_success: 1794229200, lowest_item_price: '35.00', count: 2,
    notifications: {accepted: 5, accepted_current_revision: 2, pending: 0, in_flight: 0, uncertain: 0},
  }],
};

for (const [engineName, engine] of Object.entries({chromium, webkit})) {
  const browser = await engine.launch({headless: true});
  try {
    for (const width of [390, 320, 960]) {
      const page = await browser.newPage({viewport: {width, height: 844}, serviceWorkers: 'block'});
      let saves = 0, reads = 0;
      let snapshot = structuredClone(fixture);
      page.on('dialog', dialog => dialog.dismiss());
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://sniper.example') return route.abort();
        if (url.pathname === '/watches') return route.fulfill({contentType: 'text/html', body: html});
        if (url.pathname === '/watches/state') {
          reads++;
          return route.fulfill({json: snapshot});
        }
        if (url.pathname === '/watches/save') {
          saves++;
          return route.fulfill({json: {saved: true}});
        }
        return route.abort();
      });
      await page.goto('https://sniper.example/watches');
      await page.getByText('Synthetic Artist · Favourite Record').waitFor();
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
      await page.close();
      console.log(`PASS ${engineName} width ${width}: visible check results, observation labels, paused/disabled states, navigation, draft, Back/Forward, save, edit, cancel and overflow`);
    }
  } finally {
    await browser.close();
  }
}
