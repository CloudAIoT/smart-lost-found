import { api } from './api.js';

const PAGE_SIZE = 200;
const DISPLAY_LIMIT = 5;

// 키오스크 화면 진입마다 하나 생성한다. 업무 화면·키오스크 변경에도 since는 고정한다.
export function createKioskAlerts(since = new Date().toISOString()) {
  const sessions = new Map();
  const sessionFor = (kioskId) => {
    if (!sessions.has(kioskId)) sessions.set(kioskId, { seen: new Set(), alerts: [] });
    return sessions.get(kioskId);
  };

  return {
    get(kioskId) {
      return sessionFor(kioskId).alerts;
    },
    dismiss(kioskId, id) {
      const session = sessionFor(kioskId);
      session.alerts = session.alerts.filter((alert) => alert.id !== id || alert.severity === 'alarm');
      return session.alerts;
    },
    async load(kioskId, { signal } = {}) {
      const session = sessionFor(kioskId);
      const received = [];
      // reported_at 내림차순이다. 이미 본 페이지 뒤에도 늦게 도착한 경고가 있을 수 있다.
      for (let offset = 0; ; offset += PAGE_SIZE) {
        const query = new URLSearchParams({ kiosk_id: kioskId, since, limit: String(PAGE_SIZE), offset: String(offset) });
        const list = await api(`/alerts?${query}`, { signal });
        if (!Array.isArray(list)) throw new Error('INVALID_ALERT_RESPONSE');
        received.push(...list);
        if (list.length < PAGE_SIZE) break;
      }
      signal?.throwIfAborted();

      // 모든 페이지 조회 성공 후 반영해 중간 실패·취소 시 다음 폴링에서 다시 받을 수 있게 한다.
      const fresh = [];
      // Resolution is a state update even for an ID already received or dismissed.
      const resolved = new Set(received.filter((alert) => alert.resolved_at).map((alert) => alert.id));
      session.alerts = session.alerts.filter((alert) => !resolved.has(alert.id));
      for (const alert of received) {
        if (alert.id == null || session.seen.has(alert.id)) continue;
        session.seen.add(alert.id);
        if (alert.resolved_at || alert.severity === 'alarm') continue;
        fresh.push(alert);
      }
      if (fresh.length > 0) session.alerts = [...fresh, ...session.alerts].slice(0, DISPLAY_LIMIT);
      return session.alerts;
    },
  };
}
