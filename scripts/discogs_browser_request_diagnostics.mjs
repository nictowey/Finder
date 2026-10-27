/** Dormant, aggregate-only diagnostics. Not imported by a production launcher.
 * Explicit enablement observes existing events; it never makes a request,
 * navigates, retries, reads a URL/header/body/input, or solves a challenge. */
import { performance } from 'node:perf_hooks';

export const REQUEST_DIAGNOSTIC_EVENT_CAP = 128;
export const REQUEST_DIAGNOSTIC_DURATION_MS = 60_000;
const FAILURE_KEYS = ['dns', 'timeout', 'connection', 'tls', 'blocked', 'cancelled', 'other', 'observation_error'];
const STATUS_KEYS = ['success_2xx', 'redirect_3xx', 'unauthorized_401', 'forbidden_403',
  'rate_limited_429', 'other_4xx', 'server_5xx', 'other_status'];
const codes = Object.freeze({
  dns: new Set(['NS_ERROR_UNKNOWN_HOST', 'ERR_NAME_NOT_RESOLVED']),
  timeout: new Set(['NS_ERROR_NET_TIMEOUT', 'ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT']),
  connection: new Set(['NS_ERROR_CONNECTION_REFUSED', 'NS_ERROR_NET_RESET', 'NS_ERROR_OFFLINE',
    'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET', 'ERR_INTERNET_DISCONNECTED']),
  tls: new Set(['SEC_ERROR_UNKNOWN_ISSUER', 'SEC_ERROR_EXPIRED_CERTIFICATE',
    'SSL_ERROR_BAD_CERT_DOMAIN', 'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_DATE_INVALID', 'ERR_SSL_PROTOCOL_ERROR']),
  blocked: new Set(['NS_ERROR_BLOCKED_BY_POLICY', 'NS_ERROR_CONTENT_BLOCKED', 'ERR_BLOCKED_BY_CLIENT', 'ERR_BLOCKED_BY_RESPONSE']),
  cancelled: new Set(['NS_BINDING_ABORTED', 'NS_ERROR_ABORT', 'ERR_ABORTED']),
});
function failureCategory(value) {
  if (typeof value !== 'string' || value.length > 512) return 'other';
  const token = /^(?:net::)?([A-Z][A-Z0-9_]{2,80})(?:$|[\s:])/u.exec(value)?.[1];
  return Object.keys(codes).find(key => codes[key].has(token)) ?? 'other';
}
function statusCategory(value) {
  if (!Number.isInteger(value) || value < 100 || value > 599) return 'other_status';
  if (value === 401) return 'unauthorized_401';
  if (value === 403) return 'forbidden_403';
  if (value === 429) return 'rate_limited_429';
  if (value >= 500) return 'server_5xx';
  if (value >= 400) return 'other_4xx';
  if (value >= 300) return 'redirect_3xx';
  return value >= 200 ? 'success_2xx' : 'other_status';
}

/** Logs at most one fixed-shape summary, on stop/cap/attachment failure.
 * `clock` is a monotonic local clock, not a page or request timing reader.
 * No live attachment is authorized by the existence of this module. */
export function startBrowserRequestDiagnostics(context, { enabled = false,
  report = value => console.log(value), clock = () => performance.now() } = {}) {
  if (typeof enabled !== 'boolean') throw new TypeError('diagnostics_enabled_boolean_required');
  const failures = Object.fromEntries(FAILURE_KEYS.map(key => [key, 0]));
  const statuses = Object.fromEntries(STATUS_KEYS.map(key => [key, 0]));
  let observing = false, handling = false, reason = enabled ? null : 'disabled', seen = 0, timer;
  let first = null, last = null, ended = null, start = null, lastClock = null, timingAvailable = true;
  const readClock = () => {
    try {
      const value = clock();
      if (Number.isFinite(value) && (lastClock === null || value >= lastClock)) {
        lastClock = value; return value;
      }
    } catch {}
    timingAvailable = false; return null;
  };
  const elapsed = () => {
    const value = readClock();
    if (start === null || value === null || value < start) { timingAvailable = false; return null; }
    return Math.floor(Math.min(value - start, REQUEST_DIAGNOSTIC_DURATION_MS) / 100) * 100;
  };
  const snapshot = () => ({
    schema: 'sniper_browser_request_diagnostics_v1', enabled, state: observing ? 'observing' : 'stopped',
    stop_reason: reason, observed_events: seen, event_cap: REQUEST_DIAGNOSTIC_EVENT_CAP,
    duration_cap_ms: REQUEST_DIAGNOSTIC_DURATION_MS,
    elapsed_ms: enabled ? observing ? elapsed() : ended : null,
    first_event_ms: first, last_event_ms: last, timing_available: enabled && timingAvailable,
    failure_counts: { ...failures }, response_counts: { ...statuses },
  });
  const detach = () => {
    for (const [name, listener] of [['requestfailed', failed], ['response', response]]) {
      try { context.off(name, listener); } catch { /* Closed handlers remain inert. */ }
    }
  };
  const stop = (stopReason = 'manual') => {
    if (!observing) return;
    observing = false; reason = stopReason; ended = elapsed(); clearTimeout(timer);
    detach();
    try { report(JSON.stringify(snapshot())); } catch { /* No raw exception output. */ }
  };
  const record = (counts, category) => {
    if (!observing || seen >= REQUEST_DIAGNOSTIC_EVENT_CAP) return;
    counts[category]++; seen++;
    const value = elapsed(); if (seen === 1) first = value; last = value;
    if (seen === REQUEST_DIAGNOSTIC_EVENT_CAP) stop('event_cap');
  };
  const canObserve = () => {
    if (!observing) return false;
    const value = elapsed();
    if (value === null) { stop('clock_unavailable'); return false; }
    if (value >= REQUEST_DIAGNOSTIC_DURATION_MS) { stop('duration_cap'); return false; }
    return true;
  };
  const failed = request => {
    if (handling) return;
    handling = true;
    try {
      if (!canObserve()) return;
      try { record(failures, failureCategory(request.failure()?.errorText)); }
      catch { record(failures, 'observation_error'); }
    } finally { handling = false; }
  };
  const response = result => {
    if (handling) return;
    handling = true;
    try {
      if (!canObserve()) return;
      try { record(statuses, statusCategory(result.status())); }
      catch { record(failures, 'observation_error'); }
    } finally { handling = false; }
  };
  const control = Object.freeze({ stop: () => stop(), snapshot });
  if (!enabled) return control; // No context/clock/report access while disabled.
  if (typeof report !== 'function' || typeof clock !== 'function' ||
      !context || typeof context.on !== 'function' || typeof context.off !== 'function') {
    throw new TypeError('diagnostics_observer_configuration_required');
  }
  start = readClock(); observing = true;
  timer = setTimeout(() => stop('duration_cap'), REQUEST_DIAGNOSTIC_DURATION_MS);
  timer.unref?.();
  try {
    context.on('requestfailed', failed);
    if (observing) context.on('response', response);
  }
  catch { stop('attachment_failed'); }
  finally { if (!observing) detach(); }
  return control;
}
