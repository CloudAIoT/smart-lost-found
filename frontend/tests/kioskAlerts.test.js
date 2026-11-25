import assert from 'node:assert/strict';
import test from 'node:test';
import { createKioskAlerts } from '../src/kioskAlerts.js';

const since = '2026-11-25T00:00:00.000Z';
const kioskA = '00000000-0000-0000-0000-000000000001';
const kioskB = '00000000-0000-0000-0000-000000000002';
const alert = (id, reportedAt = '2026-11-25T00:10:00.000Z') => ({
  id, kiosk_id: kioskA, code: 'TAMPER', severity: 'warning', locker_number: 2,
  message: '같은 경고 메시지', reported_at: reportedAt,
});

function mockApi(t, response) {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(options.method, 'GET');
    const parsed = new URL(url, 'http://test');
    assert.equal(parsed.pathname, '/api/alerts');
    const query = Object.fromEntries(parsed.searchParams);
    requests.push(query);
    return response(query, requests.length, options);
  });
  return requests;
}

function mockFeed(t, read) {
  return mockApi(t, (query) => {
    const list = read().filter((a) => a.kiosk_id === query.kiosk_id && Date.parse(a.reported_at) >= Date.parse(query.since))
      .sort((a, b) => Date.parse(b.reported_at) - Date.parse(a.reported_at) || a.id.localeCompare(b.id));
    return Response.json(list.slice(Number(query.offset), Number(query.offset) + Number(query.limit)));
  });
}

test('first poll uses entry time and keeps it across polls and kiosks', async (t) => {
  const before = Date.now();
  const alerts = createKioskAlerts();
  const after = Date.now();
  const requests = mockFeed(t, () => [alert('new', '2099-01-01T00:00:00Z')]);
  await alerts.load(kioskA);
  await alerts.load(kioskA);
  await alerts.load(kioskB);
  assert.equal(requests.length, 3);
  const entryTime = Date.parse(requests[0].since);
  assert.ok(entryTime >= before && entryTime <= after);
  assert.ok(requests.every((r) => r.since === requests[0].since));
});

test('since excludes earlier alerts while retaining alerts on the entry boundary', async (t) => {
  mockFeed(t, () => [alert('old', '2026-11-24T23:59:59Z'), alert('boundary', since), alert('new')]);
  const alerts = createKioskAlerts(since);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['new', 'boundary']);
});

test('duplicates in one response and repeated polls are displayed only once', async (t) => {
  mockFeed(t, () => [alert('same'), alert('same'), alert('other')]);
  const alerts = createKioskAlerts(since);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['other', 'same']);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['other', 'same']);
});

test('dismissed and overflow ids stay remembered independently of the visible list', async (t) => {
  let feed = Array.from({ length: 8 }, (_, i) => alert(`alert-${i}`));
  mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  assert.equal((await alerts.load(kioskA)).length, 5);
  for (const a of alerts.get(kioskA)) alerts.dismiss(kioskA, a.id);
  assert.deepEqual(await alerts.load(kioskA), []);
  feed = [alert('new'), ...feed];
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['new']);
});

test('same message and timestamp with a new id is a new alert', async (t) => {
  let feed = [alert('first')];
  mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  alerts.dismiss(kioskA, 'first');
  feed = [alert('first'), alert('second')];
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['second']);
});

test('late arrival before the latest reported_at is still displayed', async (t) => {
  let feed = [alert('latest')];
  const requests = mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  feed = [...feed, alert('late', '2026-11-25T00:05:00+00:00')];
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['late', 'latest']);
  assert.ok(requests.every((r) => r.since === since));
});

test('full known pages do not prevent discovery of a late arrival on a later page', async (t) => {
  let feed = Array.from({ length: 200 }, (_, i) => alert(`known-${String(i).padStart(3, '0')}`));
  const requests = mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  for (const a of alerts.get(kioskA)) alerts.dismiss(kioskA, a.id);
  feed = [...feed, alert('late-page-two', '2026-11-25T00:01:00Z')];
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['late-page-two']);
  assert.deepEqual(requests.map((r) => Number(r.offset)), [0, 200, 0, 200]);
  assert.ok(requests.every((r) => r.limit === '200' && r.since === since));
});

test('more than two pages are read without a total-result cap', async (t) => {
  const feed = Array.from({ length: 405 }, (_, i) => alert(`alert-${String(i).padStart(3, '0')}`));
  const requests = mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  assert.deepEqual(requests.map((r) => Number(r.offset)), [0, 200, 400]);
  for (const a of alerts.get(kioskA)) alerts.dismiss(kioskA, a.id);
  assert.deepEqual(await alerts.load(kioskA), []);
});

test('kiosk sessions retain separate visible and dismissed alerts', async (t) => {
  mockFeed(t, () => [alert('a'), { ...alert('b'), kiosk_id: kioskB }]);
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  alerts.dismiss(kioskA, 'a');
  assert.deepEqual((await alerts.load(kioskB)).map((a) => a.id), ['b']);
  assert.deepEqual(await alerts.load(kioskA), []);
  assert.deepEqual(alerts.get(kioskB).map((a) => a.id), ['b']);
});

test('a late response after kiosk change is aborted without remembering its ids', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  mockApi(t, async (_query, call) => {
    if (call === 1) await pending;
    return Response.json([alert('late-response')]);
  });
  const alerts = createKioskAlerts(since);
  const controller = new AbortController();
  const request = alerts.load(kioskA, { signal: controller.signal });
  controller.abort();
  release();
  await assert.rejects(request, { name: 'AbortError' });
  assert.deepEqual(alerts.get(kioskA), []);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['late-response']);
});

test('a failed second page does not consume ids and the next poll recovers', async (t) => {
  const firstPage = Array.from({ length: 200 }, (_, i) => alert(`alert-${i}`));
  mockApi(t, (query, call) => {
    if (call === 2) return Response.json({ detail: { code: 'SERVER_ERROR' } }, { status: 500 });
    return Response.json(Number(query.offset) === 0 ? firstPage : [alert('last')]);
  });
  const alerts = createKioskAlerts(since);
  await assert.rejects(alerts.load(kioskA), { code: 'SERVER_ERROR' });
  assert.deepEqual(alerts.get(kioskA), []);
  assert.equal((await alerts.load(kioskA)).length, 5);
});

test('dismissing an alert during a pending poll does not bring it back', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  mockApi(t, async (_query, call) => {
    if (call === 2) await pending;
    return Response.json([alert('existing')]);
  });
  const alerts = createKioskAlerts(since);
  await alerts.load(kioskA);
  const request = alerts.load(kioskA);
  alerts.dismiss(kioskA, 'existing');
  release();
  assert.deepEqual(await request, []);
});

test('a malformed response leaves the session intact and can be retried', async (t) => {
  mockApi(t, (_query, call) => Response.json(call === 1 ? { invalid: true } : [alert('retry')]));
  const alerts = createKioskAlerts(since);
  await assert.rejects(alerts.load(kioskA), /INVALID_ALERT_RESPONSE/);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['retry']);
});

test('server resolution updates an already-seen warning and survives repeated polls', async (t) => {
  let feed = [alert('existing')];
  mockFeed(t, () => feed);
  const alerts = createKioskAlerts(since);
  assert.equal((await alerts.load(kioskA)).length, 1);
  feed = [{ ...alert('existing'), resolved_at: since }];
  assert.deepEqual(await alerts.load(kioskA), []);
  assert.deepEqual(await alerts.load(kioskA), []);
  feed.push(alert('new-id'));
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['new-id']);
});

test('alarms use the durable operation-status query and never consume warning display slots', async (t) => {
  mockFeed(t, () => [...Array.from({ length: 6 }, (_, i) => ({ ...alert(`alarm-${i}`), severity: 'alarm' })), alert('warning')]);
  const alerts = createKioskAlerts(since);
  assert.deepEqual((await alerts.load(kioskA)).map((a) => a.id), ['warning']);
});
