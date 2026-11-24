import { api, ApiError } from './api.js';

const OWNERSHIP_STAGES = {
  queued: '검색 순서를 기다리는 중입니다',
  clue_extraction: '입력한 설명에서 단서를 찾는 중입니다',
  candidate_search: '유사한 물품을 찾고 확인 질문을 준비하는 중입니다',
  question_ready: '확인 질문이 준비되었습니다',
};

const ANALYSIS_STAGES = {
  queued: '사진 분석 순서를 기다리는 중입니다',
  preparing: '사진을 준비하는 중입니다',
  inferring: '사진에서 물건의 특징을 분석하는 중입니다',
  verifying: '사진 분석 결과를 확인하는 중입니다',
};

export function ownershipPendingText(stage) {
  return OWNERSHIP_STAGES[stage] || 'AI가 분석하는 중입니다';
}

export function analysisPendingText(registration) {
  const parts = [ANALYSIS_STAGES[registration.analysis_stage] || '사진을 분석하는 중입니다'];
  if (registration.analysis_attempt >= 2) parts.push(`재시도 중 (${registration.analysis_attempt}번째 시도)`);
  if (Number.isFinite(registration.analysis_elapsed_ms)) {
    parts.push(`경과 시간 약 ${Math.max(0, Math.round(registration.analysis_elapsed_ms / 1000))}초`);
  }
  return parts.join(' · ');
}

export function aiStatusText(status) {
  const parts = [];
  // configured only describes settings. It is not a provider health check.
  if (status.configured === false) parts.push('AI 서비스가 설정되지 않았습니다.');
  for (const [key, label] of [['searches', '검색'], ['analyses', '사진 분석']]) {
    const jobs = status[key];
    if (jobs) parts.push(`${label}: 처리 중 ${jobs.running}건, 대기 ${jobs.queued}건.`);
  }
  const recent = status.recent_calls;
  if (recent?.calls > 0) {
    const failures = Object.entries(recent.outcomes || {})
      .reduce((total, [outcome, count]) => total + (outcome === 'ok' ? 0 : count), 0);
    parts.push(`최근 ${recent.window_seconds}초 동안 AI 호출 ${recent.calls}건, 실패 ${failures}건.`);
    if (Number.isFinite(recent.latency_ms?.p50)) {
      parts.push(`호출 소요 시간 중앙값 ${(recent.latency_ms.p50 / 1000).toFixed(1)}초.`);
    }
  } else {
    parts.push('최근 AI 호출 기록이 아직 없습니다.');
  }
  return parts.join(' ');
}

function pollInterval(value, fallback) {
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function wait(milliseconds, signal) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

export async function waitForOwnership(result, { signal, onProgress } = {}) {
  signal?.throwIfAborted();
  while (result.status === 'pending') {
    if (!result.attempt_id) throw new ApiError(502, 'INVALID_AI_RESPONSE');
    onProgress?.(result);
    await wait(pollInterval(result.retry_after_ms, 700), signal);
    result = await api(`/ownership/attempts/${result.attempt_id}`, { signal });
  }
  signal?.throwIfAborted();
  return result;
}

export async function analyzeAndOpenRegistration(id, { signal, onProgress, onOpening } = {}) {
  const path = `/registrations/${id}`;
  // A previous response may have been lost. Resume an existing analysis or open locker.
  let registration = await api(path, { signal });
  if (registration.status === 'awaiting_deposit') return registration;
  if (!['running', 'succeeded'].includes(registration.analysis_status)) {
    registration = await api(`${path}/analysis`, { method: 'POST', signal });
  }
  while (registration.analysis_status === 'running') {
    onProgress?.(registration);
    await wait(pollInterval(registration.analysis_retry_after_ms, 1000), signal);
    registration = await api(path, { signal });
  }
  if (registration.analysis_status !== 'succeeded') {
    throw new ApiError(409, registration.analysis_error_code || 'ANALYSIS_FAILED');
  }
  signal?.throwIfAborted();
  onOpening?.();
  return api(`${path}/deposit/open`, { method: 'POST', signal });
}
