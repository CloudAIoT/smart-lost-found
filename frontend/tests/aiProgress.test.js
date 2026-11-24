import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import test from 'node:test';
import { api } from '../src/api.js';
import { aiStatusText, analysisPendingText, analyzeAndOpenRegistration, ownershipPendingText, waitForOwnership } from '../src/aiProgress.js';

const path = '/registrations/reg-1';
const draft = { id: 'reg-1', status: 'draft', analysis_status: 'not_started' };
const running = {
  ...draft, status: 'analyzing', analysis_status: 'running', analysis_stage: 'inferring',
  analysis_elapsed_ms: 4200, analysis_attempt: 2, analysis_retry_after_ms: 1,
};
const ready = { ...draft, status: 'ready', analysis_status: 'succeeded' };
const opened = { ...ready, status: 'awaiting_deposit', locker_number: 2 };
const pending = {
  status: 'pending', attempt_id: 'attempt-1', stage: 'clue_extraction', elapsed_ms: 9000,
  retry_after_ms: 1,
};
const question = { status: 'question', attempt_id: 'attempt-1', question: { id: 'question-1' } };

function mockApi(t, responses) {
  let index = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const expected = responses[index++];
    assert.ok(expected, `Unexpected ${options.method} ${url}`);
    assert.equal(options.method, expected[0]);
    assert.equal(url, `/api${expected[1]}`);
    const body = typeof expected[2] === 'function' ? await expected[2](options) : expected[2];
    return Response.json(body, { status: expected[3] || 200 });
  });
  t.after(() => assert.equal(index, responses.length, 'Every expected API request must be made exactly once'));
  return () => index;
}

test('slow photo analysis polls through stages and opens only after success', async (t) => {
  mockApi(t, [
    ['GET', path, draft],
    ['POST', `${path}/analysis`, { ...running, analysis_stage: 'queued' }],
    ['GET', path, running],
    ['GET', path, { ...running, analysis_stage: 'verifying' }],
    ['GET', path, ready],
    ['POST', `${path}/deposit/open`, opened],
  ]);
  const stages = [];
  const result = await analyzeAndOpenRegistration(draft.id, {
    onProgress: (reg) => stages.push(reg.analysis_stage), onOpening: () => stages.push('opening'),
  });
  assert.deepEqual(stages, ['queued', 'inferring', 'verifying', 'opening']);
  assert.equal(result.locker_number, 2);
});

test('immediately successful analysis opens without polling', async (t) => {
  mockApi(t, [
    ['GET', path, draft], ['POST', `${path}/analysis`, ready], ['POST', `${path}/deposit/open`, opened],
  ]);
  assert.deepEqual(await analyzeAndOpenRegistration(draft.id), opened);
});

test('already successful analysis is reused without another AI request', async (t) => {
  mockApi(t, [['GET', path, ready], ['POST', `${path}/deposit/open`, opened]]);
  assert.deepEqual(await analyzeAndOpenRegistration(draft.id), opened);
});

for (const code of ['ANALYSIS_TIMED_OUT', 'ANALYSIS_FAILED', 'OWNERSHIP_AI_RATE_LIMITED']) {
  test(`a background ${code} stops polling and never opens a locker`, async (t) => {
    mockApi(t, [
      ['GET', path, draft], ['POST', `${path}/analysis`, running],
      ['GET', path, { ...draft, analysis_status: 'failed', analysis_error_code: code }],
    ]);
    await assert.rejects(analyzeAndOpenRegistration(draft.id), { code });
  });
}

test('retry after a network failure resumes the existing job without starting another', async (t) => {
  mockApi(t, [
    ['GET', path, draft], ['POST', `${path}/analysis`, running],
    ['GET', path, () => { throw new TypeError('offline'); }],
    ['GET', path, running], ['GET', path, ready], ['POST', `${path}/deposit/open`, opened],
  ]);
  await assert.rejects(analyzeAndOpenRegistration(draft.id), { code: 'NETWORK_ERROR' });
  assert.deepEqual(await analyzeAndOpenRegistration(draft.id), opened);
});

test('retry of a failed analysis starts it again with the same registration', async (t) => {
  mockApi(t, [
    ['GET', path, { ...draft, analysis_status: 'failed', analysis_error_code: 'ANALYSIS_TIMED_OUT' }],
    ['POST', `${path}/analysis`, running], ['GET', path, ready], ['POST', `${path}/deposit/open`, opened],
  ]);
  assert.deepEqual(await analyzeAndOpenRegistration(draft.id), opened);
});

test('retry after a lost locker-open response does not send a second open command', async (t) => {
  mockApi(t, [['GET', path, opened]]);
  assert.deepEqual(await analyzeAndOpenRegistration(draft.id), opened);
});

test('leaving during analysis cancels the wait before any further polling or opening', async (t) => {
  const controller = new AbortController();
  mockApi(t, [['GET', path, running]]);
  await assert.rejects(analyzeAndOpenRegistration(draft.id, {
    signal: controller.signal, onProgress: () => controller.abort(),
  }), { name: 'AbortError' });
});

test('a late analysis success after leaving cannot open a locker', async (t) => {
  const controller = new AbortController();
  mockApi(t, [
    ['GET', path, running],
    ['GET', path, ({ signal }) => {
      assert.equal(signal, controller.signal);
      controller.abort();
      return ready; // Even a transport that delivers after abort must not advance the flow.
    }],
  ]);
  await assert.rejects(analyzeAndOpenRegistration(draft.id, { signal: controller.signal }), { name: 'AbortError' });
});

test('photo polling honors the interval returned by the server', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests = mockApi(t, [
    ['GET', path, { ...running, analysis_retry_after_ms: 1400 }],
    ['GET', path, ready], ['POST', `${path}/deposit/open`, opened],
  ]);
  const result = analyzeAndOpenRegistration(draft.id);
  await setImmediate();
  t.mock.timers.tick(1399);
  await setImmediate();
  assert.equal(requests(), 1);
  t.mock.timers.tick(1);
  assert.deepEqual(await result, opened);
});

test('search waits the advertised interval and returns the question', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests = mockApi(t, [['GET', '/ownership/attempts/attempt-1', question]]);
  const stages = [];
  const result = waitForOwnership({ ...pending, retry_after_ms: 700 }, {
    onProgress: (progress) => stages.push(progress.stage),
  });
  t.mock.timers.tick(699);
  await setImmediate();
  assert.equal(requests(), 0);
  t.mock.timers.tick(1);
  assert.deepEqual(await result, question);
  assert.deepEqual(stages, ['clue_extraction']);
});

test('search timeout propagates its server error and stops polling', async (t) => {
  mockApi(t, [['GET', '/ownership/attempts/attempt-1', { detail: { code: 'OWNERSHIP_AI_TIMEOUT' } }, 502]]);
  await assert.rejects(waitForOwnership(pending), { code: 'OWNERSHIP_AI_TIMEOUT' });
});

test('restarting search cancels its timer without another request', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests = mockApi(t, []);
  const controller = new AbortController();
  const result = waitForOwnership({ ...pending, retry_after_ms: 700 }, { signal: controller.signal });
  controller.abort();
  await assert.rejects(result, { name: 'AbortError' });
  t.mock.timers.tick(1000);
  await setImmediate();
  assert.equal(requests(), 0);
});

test('a late response from an abandoned search cannot replace the new result', async (t) => {
  const controller = new AbortController();
  mockApi(t, [['GET', '/ownership/attempts/attempt-1', () => {
    controller.abort();
    return question;
  }]]);
  await assert.rejects(waitForOwnership(pending, { signal: controller.signal }), { name: 'AbortError' });
  assert.deepEqual(await waitForOwnership({ ...question, attempt_id: 'new-attempt' }), {
    ...question, attempt_id: 'new-attempt',
  });
});

test('search terminal outcomes pass through without another request', async (t) => {
  mockApi(t, []);
  for (const status of ['question', 'photo_confirmation', 'no_candidates', 'needs_clues', 'search_limit', 'completed']) {
    assert.equal((await waitForOwnership({ status })).status, status);
  }
});

test('a pending response without an attempt id fails instead of polling forever', async (t) => {
  mockApi(t, []);
  await assert.rejects(waitForOwnership({ status: 'pending' }), { code: 'INVALID_AI_RESPONSE' });
});

test('AI status shows the documented metrics and never infers health from configured', () => {
  const text = aiStatusText({
    configured: true,
    searches: { accepted: 2, running: 1, queued: 1 },
    analyses: { accepted: 1, running: 1, queued: 0 },
    recent_calls: {
      window_seconds: 300, calls: 3, latency_ms: { p50: 2400, p95: 30000, max: 30000 },
      outcomes: { ok: 2, OWNERSHIP_AI_TIMEOUT: 1 },
    },
  });
  assert.match(text, /검색: 처리 중 1건, 대기 1건/);
  assert.match(text, /사진 분석: 처리 중 1건, 대기 0건/);
  assert.match(text, /300초 동안 AI 호출 3건, 실패 1건/);
  assert.match(text, /중앙값 2.4초/);
  assert.doesNotMatch(text, /정상|평균/);
  assert.match(aiStatusText({ configured: false, recent_calls: { calls: 0, latency_ms: null } }), /설정되지 않았습니다/);
  assert.match(aiStatusText({ configured: true, recent_calls: { calls: 0, latency_ms: null } }), /기록이 아직 없습니다/);
});

test('progress describes backend stages, retries and elapsed time', () => {
  assert.match(ownershipPendingText('queued'), /순서/);
  assert.match(ownershipPendingText('clue_extraction'), /단서/);
  assert.match(ownershipPendingText('candidate_search'), /유사한 물품/);
  assert.match(ownershipPendingText('question_ready'), /질문/);
  assert.match(analysisPendingText(running), /특징.*재시도 중 \(2번째 시도\).*4초/);
});

test('API body read failures are reported as a network error', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({ text: async () => { throw new TypeError('connection reset'); } }));
  await assert.rejects(api('/ai/status'), { code: 'NETWORK_ERROR' });
});
