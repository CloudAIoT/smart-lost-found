import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

export function useKioskOperation(kioskId) {
  const [snapshot, setSnapshot] = useState(null);
  const [revision, setRevision] = useState(0);
  const suspend = useCallback(() => {
    setSnapshot((previous) => ({ kioskId, data: { ...previous?.data, blocked: true }, error: false }));
    setRevision((value) => value + 1);
  }, [kioskId]);

  useEffect(() => {
    if (!kioskId) return;
    const controller = new AbortController();
    let polling = false;
    const poll = async () => {
      if (polling || controller.signal.aborted) return;
      polling = true;
      const request = new AbortController();
      const abort = () => request.abort();
      controller.signal.addEventListener('abort', abort, { once: true });
      const deadline = setTimeout(abort, 5000);
      try {
        const data = await api(`/kiosks/${kioskId}/operation-status`, { signal: request.signal });
        if (typeof data?.blocked !== 'boolean' || !Array.isArray(data.alarms)
          || !Number.isInteger(data.active_alarm_count) || data.active_alarm_count < 0
          || data.blocked !== (data.active_alarm_count > 0) || data.kiosk_id !== kioskId) {
          throw new Error('INVALID_OPERATION_STATUS');
        }
        if (!controller.signal.aborted) setSnapshot({ kioskId, data, error: false });
      } catch {
        if (!controller.signal.aborted) setSnapshot((previous) => ({
          kioskId, data: previous?.kioskId === kioskId ? previous.data : null, error: true,
        }));
      } finally {
        clearTimeout(deadline);
        controller.signal.removeEventListener('abort', abort);
        polling = false;
      }
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [kioskId, revision]);

  const current = snapshot?.kioskId === kioskId ? snapshot : null;
  return { locked: !!kioskId && (!current?.data || current.error || current.data.blocked),
    blocked: !!current?.data?.blocked, error: !!current?.error,
    alarms: current?.data?.alarms || [], count: current?.data?.active_alarm_count || 0, suspend };
}

export default function KioskOperationGuard({ operation, children }) {
  const dialog = useRef(null);
  useEffect(() => {
    if (operation.locked && !dialog.current.open) dialog.current.showModal();
    if (!operation.locked && dialog.current.open) dialog.current.close();
  }, [operation.locked]);

  return <>
    <div inert={operation.locked} aria-hidden={operation.locked || undefined} className="contents">{children}</div>
    <dialog ref={dialog} aria-labelledby="operation-title" aria-describedby="operation-description"
      onCancel={(event) => event.preventDefault()}
      className="m-auto w-[calc(100%_-_2rem)] max-w-lg rounded-2xl border border-rose-500/60 bg-slate-950 p-8 text-slate-100 backdrop:bg-slate-950/90">
      <h2 id="operation-title" className="text-xl font-bold text-white">
        {operation.blocked ? '관리자의 확인이 필요합니다!' : operation.error ? '운영 상태를 확인할 수 없습니다' : '운영 상태를 확인하고 있습니다'}
      </h2>
      <p id="operation-description" className="mt-4 text-sm text-slate-300">
        {operation.blocked
          ? '장치 이상으로 이용이 일시 중단되었습니다. 관리자가 문제를 해결할 때까지 기다려 주세요.'
          : '서버에서 이용 가능 여부를 확인한 뒤 자동으로 안내합니다. 잠시 기다려 주세요.'}
      </p>
      {operation.blocked && <p className="mt-3 text-sm">{operation.count > 0 ? `미해결 alarm ${operation.count}건 · ` : ''}관리자 조치 완료 대기</p>}
      {operation.alarms.length > 0 && <ul className="mt-4 max-h-48 overflow-auto space-y-2 text-sm">
        {operation.alarms.map((alert) => <li key={alert.id}>
          {alert.locker_number != null ? `${alert.locker_number}번 보관함 · ` : ''}{alert.message || alert.code}
        </li>)}
      </ul>}
      {operation.blocked && operation.error && <p className="mt-3 text-amber-300 text-sm">서버 연결을 확인 중입니다. 관리자 조치 여부를 확인할 때까지 대기합니다.</p>}
      <p className="mt-5 text-xs text-slate-400">진행 중인 작업은 자동으로 완료되거나 취소되지 않습니다.</p>
    </dialog>
  </>;
}
