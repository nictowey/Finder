// Real browser native forms, synthetic HTTPS interception, no local server.
// Every application request is fulfilled in-process or aborted. CI additionally
// runs this entire process with Docker --network none, non-root and read-only.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {chromium, webkit} from '../cloud_browser/node_modules/playwright/index.mjs';

const origin = 'https://watch.example';
const attacker = 'https://attacker.example';
const child = spawn('/opt/venv/bin/python', ['tests/device_session_browser/wsgi_bridge.py'], {
  env: {...process.env, PYTHONPATH: 'src'}, stdio: ['pipe', 'pipe', 'inherit'],
});
const pending = new Map();
let nextID = 0;
createInterface({input: child.stdout}).on('line', line => {
  const data = JSON.parse(line);
  const handler = pending.get(data.id);
  pending.delete(data.id);
  handler?.resolve(data);
});
child.on('exit', code => {
  for (const {reject} of pending.values()) reject(new Error(`Synthetic bridge exited ${code}`));
  pending.clear();
});
function dispatch(data) {
  return new Promise((resolve, reject) => {
    const id = ++nextID;
    pending.set(id, {resolve, reject});
    child.stdin.write(JSON.stringify({id, ...data}) + '\n');
  });
}

try {
  const failures = [];
  for (const [name, engine] of Object.entries({chromium, webkit})) {
    let browser;
    try {
      browser = await engine.launch({headless: true});
      const context = await browser.newContext({serviceWorkers: 'block', acceptDownloads: false});
      const attempts = [];
      const unexpected = [];
      let policy = 'legacy';
      let attackHTML = '';
      await context.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin === attacker && url.pathname === '/attack' && request.method() === 'GET') {
          await route.fulfill({status: 200, headers: {'content-type': 'text/html',
            'referrer-policy': 'origin', 'cache-control': 'no-store'}, body: attackHTML});
          return;
        }
        if (url.origin !== origin || url.search || ![
          '/session/login', '/session/start', '/session', '/session/logout',
          '/session/revoke-all', '/manifest.webmanifest', '/session/icon.png', '/watches',
        ].includes(url.pathname)) {
          // Browsers may independently request a favicon; it is never fetched.
          if (url.origin !== origin || url.pathname !== '/favicon.ico') unexpected.push(url.origin);
          await route.abort('blockedbyclient');
          return;
        }
        const response = await dispatch({path: url.pathname, method: request.method(),
          headers: await request.allHeaders(),
          body: (request.postDataBuffer() ?? Buffer.alloc(0)).toString('base64'), policy});
        let fixtureStatus = response.status;
        const fixtureHeaders = {...response.headers};
        if (request.method() === 'POST' && url.pathname === '/session/start' && response.status === 303) {
          // Playwright routes only the first URL of a redirect chain. In this
          // network-disabled fixture, preserve the REAL application's decision
          // and Set-Cookie, but do not ask the browser to follow that redirect.
          assert.equal(response.headers.location, '/session');
          fixtureStatus = 200;
          delete fixtureHeaders.location;
        }
        if (request.method() === 'POST') {
          attempts.push({origin: response.origin, status: response.status,
            fixtureStatus, location: response.headers.location ?? null});
          console.log(JSON.stringify({phase: 'native-post', browser: name, policy,
            origin: response.origin, application_status: response.status,
            fixture_delivery_status: fixtureStatus,
            expected_location: response.headers.location ?? null}));
        }
        await route.fulfill({status: fixtureStatus, headers: fixtureHeaders,
          body: Buffer.from(response.body, 'base64')});
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      async function submit(name) {
        const [response] = await Promise.all([
          page.waitForResponse(res => res.url() === origin + '/session/start' &&
            res.request().method() === 'POST'),
          page.getByRole('button', {name}).click(),
        ]);
        await response.finished();
        return response.status();
      }

      // Reproduce the real browser failure. We alter only the RESPONSE policy,
      // never request Origin, Sec-Fetch headers, native form data or fetch logic.
      await page.goto(origin + '/session/login');
      assert.equal(await submit('Remember this device for 30 days'), 403);
      assert.equal(attempts.at(-1).status, 403, `${name}: legacy native POST must be denied`);
      assert.equal(attempts.at(-1).origin, 'null', `${name}: no-referrer produces opaque Origin`);
      assert.equal((await context.cookies()).length, 0, `${name}: rejected login cannot set a session`);

      // Current application response must fix the actual native POST. Do not
      // override the policy here: this fails against the old application code.
      policy = 'current';
      const login = await page.goto(origin + '/session/login');
      assert.equal(await submit('Remember this device for 30 days'), 200);
      assert.equal(attempts.at(-1).location, '/session');
      assert.equal(attempts.at(-1).fixtureStatus, 200);
      // Separate explicit navigation, not evidence of native 303 redirect handling.
      const account = await page.goto(origin + '/session');
      assert.equal(account.status(), 200);
      assert.equal(page.url(), origin + '/session');
      assert.equal(attempts.at(-1).origin, origin, `${name}: native form must emit canonical Origin`);
      assert.equal(attempts.at(-1).status, 303, `${name}: current native form must create session`);
      assert.equal(login.headers()['referrer-policy'], 'same-origin');
      const cookies = await context.cookies(origin);
      const cookie = cookies.find(item => item.name === '__Host-sniper_session');
      assert.ok(cookie, `${name}: browser must retain the synthetic response session cookie`);
      assert.equal(cookie.httpOnly, true);
      assert.equal(cookie.secure, true);
      assert.equal(cookie.sameSite, 'Strict');
      assert.equal(cookie.domain, 'watch.example');
      assert.equal(cookie.path, '/');
      assert.ok(cookie.expires > Date.now() / 1000 + 29 * 86400);
      assert.equal(await page.evaluate(() => document.cookie.includes('__Host-sniper_session')), false);

      // Obtain a genuine one-use bootstrap challenge, then submit it from an
      // actual different-origin native form. Even simulated cached Basic cannot
      // defeat the strict Origin check. No Origin is injected by test code.
      await page.goto(origin + '/session/login');
      const csrf = await page.locator('input[name="csrf"]').inputValue();
      assert.match(csrf, /^[A-Za-z0-9_-]+$/);
      attackHTML = `<form method="post" action="${origin}/session/start">` +
        `<input type="hidden" name="csrf" value="${csrf}"><button>Submit cross-origin</button></form>`;
      await page.goto(attacker + '/attack');
      assert.equal(await submit('Submit cross-origin'), 403);
      assert.equal(attempts.at(-1).origin, attacker, `${name}: foreign origin evidence must be browser-generated`);
      assert.equal(attempts.at(-1).status, 403, `${name}: cross-origin native form must be denied`);
      assert.deepEqual(unexpected, [], 'No unexpected application network destination');
      console.log(JSON.stringify({status: 'passed', browser: name, version: browser.version(),
        legacy_native_origin: attempts[0].origin, legacy_status: attempts[0].status,
        current_native_origin: attempts[1].origin, current_status: attempts[1].status,
        foreign_native_origin: attempts[2].origin, foreign_status: attempts[2].status,
        fixture_redirect_normalized: true, native_redirect_verified: false,
        explicit_cookie_navigation_status: 200, restart_persistence_verified: false,
        cookie_secure: true, cookie_http_only: true, cookie_same_site: 'Strict'}));
      await context.close();
    } catch (error) {
      failures.push(new Error(`${name}: ${error.message}`));
      console.error(JSON.stringify({status: 'failed', browser: name, error: error.message}));
    } finally {
      if (browser) await browser.close();
    }
  }
  if (failures.length) throw new AggregateError(failures, 'Native form browser checks failed');
} finally {
  child.stdin.end();
  child.kill('SIGTERM');
}
