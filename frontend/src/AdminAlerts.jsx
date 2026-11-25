import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';

const PAGE_SIZE = 50;
function message(error) {
  return ({ ADMIN_UNAUTHORIZED: '관리자 계정 또는 비밀번호를 확인해 주세요.',
    ADMIN_NOT_CONFIGURED: '서버에 관리자 인증 정보가 설정되지 않았습니다.',
    ALERT_NOT_FOUND: '경고를 찾을 수 없습니다. 목록을 새로고침해 주세요.',
    NETWORK_ERROR: '서버에 연결할 수 없습니다. 조치 완료 여부를 다시 확인해 주세요.',
  })[error.code] || `요청을 처리하지 못했습니다. (${error.code || 'UNKNOWN'})`;
}

export default function AdminAlerts({ kiosks, labels, renderDeviceStatus }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // Credentials are kept only in this component's memory, never localStorage or a VITE variable.
  const [authorization, setAuthorization] = useState('');
  const [actor, setActor] = useState('');
  const [kioskId, setKioskId] = useState('');
  const selectedKiosk = kioskId || kiosks[0]?.id || '';
  const [history, setHistory] = useState(false);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const [notes, setNotes] = useState({});
  const [confirmed, setConfirmed] = useState({});
  const mutation = useRef(null);
  const listing = useRef(null);
  useEffect(() => () => mutation.current?.abort(), []);

  useEffect(() => {
    setRows([]);
    if (!authorization || !selectedKiosk) return;
    const controller = new AbortController();
    listing.current = controller;
    let polling = false;
    const load = async () => {
      if (polling || controller.signal.aborted) return;
      polling = true;
      setLoading(true);
      try {
        const query = new URLSearchParams({ kiosk_id: selectedKiosk, unresolved_only: String(!history),
          limit: String(PAGE_SIZE), offset: String(offset) });
        const data = await api(`/admin/alerts?${query}`, { signal: controller.signal, headers: { Authorization: authorization } });
        if (!Array.isArray(data)) throw new Error('INVALID_ALERT_RESPONSE');
        if (!controller.signal.aborted) { setRows(data); setLoadError(''); }
      } catch (err) {
        if (!controller.signal.aborted) setLoadError(message(err));
      } finally {
        polling = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [authorization, selectedKiosk, history, offset, revision]);

  const login = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy('login'); setError('');
    const controller = new AbortController();
    mutation.current = controller;
    try {
      const header = `Basic ${btoa(`${username}:${password}`)}`;
      const data = await api('/admin/session', { signal: controller.signal, headers: { Authorization: header } });
      if (!controller.signal.aborted) { setAuthorization(header); setActor(data.username); setPassword(''); }
    } catch (err) {
      if (!controller.signal.aborted) setError(message(err));
    } finally { if (!controller.signal.aborted) setBusy(''); }
  };

  const resolve = async (alert) => {
    if (busy || !confirmed[alert.id] || !notes[alert.id]?.trim()) return;
    setBusy(alert.id); setError(''); setNotice('');
    listing.current?.abort();
    const controller = new AbortController();
    mutation.current = controller;
    try {
      await api(`/admin/alerts/${alert.id}/resolve`, { method: 'POST', signal: controller.signal,
        headers: { Authorization: authorization }, body: { confirmed: true, note: notes[alert.id].trim() } });
      if (!controller.signal.aborted) {
        setNotice('조치 완료를 기록했습니다. 이 키오스크의 모든 alarm이 해결되면 운영이 재개됩니다.');
        setNotes((value) => ({ ...value, [alert.id]: '' }));
        setConfirmed((value) => ({ ...value, [alert.id]: false }));
        setOffset(0);
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(message(err));
    } finally {
      if (!controller.signal.aborted) { setBusy(''); setRevision((value) => value + 1); }
    }
  };

  if (!authorization) return <section className="w-full max-w-lg mx-auto p-6 rounded-2xl bg-slate-950 border border-slate-700">
    <h2 className="text-xl font-bold text-white">장치 관리 · 관리자 로그인</h2>
    <form onSubmit={login} className="mt-6 space-y-4">
      <label className="block text-sm">관리자 계정<input required autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} className="block w-full mt-1 bg-slate-800 rounded p-3" /></label>
      <label className="block text-sm">비밀번호<input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="block w-full mt-1 bg-slate-800 rounded p-3" /></label>
      {error && <p role="alert" className="text-rose-300 text-sm">{error}</p>}
      <button disabled={!!busy} className="w-full bg-indigo-600 rounded p-3 disabled:opacity-40">{busy ? '확인 중...' : '관리자 로그인'}</button>
    </form>
  </section>;

  return <div className="space-y-6">
    <section className="p-5 rounded-2xl bg-slate-950 border border-slate-700 space-y-4">
      <div className="flex justify-between gap-3"><h2 className="text-xl font-bold text-white">경고 검토 및 조치</h2>
        <button disabled={!!busy} onClick={() => { listing.current?.abort(); setAuthorization(''); setRows([]); setNotes({}); setConfirmed({}); setError(''); setNotice(''); }} className="text-sm underline">{actor} · 로그아웃</button></div>
      <label className="block text-sm">조치 대상 키오스크<select value={selectedKiosk} disabled={!!busy} onChange={(event) => { setKioskId(event.target.value); setOffset(0); setNotice(''); }} className="block w-full mt-1 p-3 bg-slate-800 rounded">
        {kiosks.map((kiosk) => <option key={kiosk.id} value={kiosk.id}>{kiosk.name}</option>)}
      </select></label>
      <div className="flex gap-4 text-sm">
        <label><input type="checkbox" checked={history} disabled={!!busy} onChange={(event) => { setHistory(event.target.checked); setOffset(0); }} /> 조치 완료 이력 포함</label>
        <button disabled={!!busy || loading} onClick={() => setRevision((value) => value + 1)} className="underline">경고 새로고침</button>
      </div>
      <p className="text-sm text-slate-400">문제 해결과 운영 재개 가능 여부를 확인한 뒤 조치 내용을 기록하세요. 보관·수령 상태는 변경되지 않습니다.</p>
      {error && <p role="alert" className="text-rose-300">{error}</p>}
      {loadError && <p role="alert" className="text-rose-300">{loadError}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {!loading && !error && !loadError && rows.length === 0 && <p>표시할 경고가 없습니다.</p>}
      {rows.map((alert) => <article key={alert.id} className={`border rounded-xl p-4 space-y-3 ${alert.severity === 'alarm' ? 'border-rose-500/60' : 'border-amber-500/60'}`}>
        <h3 className="font-bold">{alert.severity} · {labels[alert.code] || alert.message || alert.code}
          {alert.locker_number != null ? ` · ${alert.locker_number}번 보관함` : ''}</h3>
        <p className="text-xs text-slate-400">{alert.reported_at ? new Date(alert.reported_at).toLocaleString('ko-KR') : ''}</p>
        {alert.resolved_at ? <div className="text-sm text-emerald-300">
          <p>조치 완료 · {alert.resolved_by} · {new Date(alert.resolved_at).toLocaleString('ko-KR')}</p><p className="whitespace-pre-wrap">{alert.resolution_note}</p>
        </div> : <>
          <label className="block text-sm">조치 내용<textarea maxLength={2000} value={notes[alert.id] || ''} disabled={!!busy}
            onChange={(event) => setNotes((value) => ({ ...value, [alert.id]: event.target.value }))} className="block w-full mt-1 p-3 rounded bg-slate-800" /></label>
          <label className="block text-sm"><input type="checkbox" checked={!!confirmed[alert.id]} disabled={!!busy}
            onChange={(event) => setConfirmed((value) => ({ ...value, [alert.id]: event.target.checked }))} /> 문제 해결 및 운영 재개 가능 여부를 확인했습니다.</label>
          <button disabled={!!busy || !confirmed[alert.id] || !notes[alert.id]?.trim()} onClick={() => resolve(alert)}
            className="px-4 py-3 rounded bg-indigo-600 disabled:opacity-40">{busy === alert.id ? '기록 중...' : alert.severity === 'alarm' ? '조치 완료 및 운영 재개' : '검토 및 조치 완료'}</button>
        </>}
      </article>)}
      <div className="flex gap-4 items-center text-sm">
        <button disabled={!!busy || loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>이전 경고</button>
        <span>{offset / PAGE_SIZE + 1}페이지</span>
        <button disabled={!!busy || loading || rows.length < PAGE_SIZE} onClick={() => setOffset((value) => value + PAGE_SIZE)}>다음 경고</button>
      </div>
    </section>
    {renderDeviceStatus(selectedKiosk)}
  </div>;
}
