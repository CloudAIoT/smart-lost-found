import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, CircleCheck, RefreshCw, TriangleAlert } from 'lucide-react';
import { api, ApiError } from './api.js';

/* ============================================================
 * 관리자 장치 관리 (/admin/devices)
 * ------------------------------------------------------------
 * 첫 화면은 모든 키오스크의 알람·경고 현황이고, 키오스크를 선택하면 같은 화면 안에서
 * 그 키오스크의 장치 상태와 경고 조치 화면으로 바뀐다(별도 URL을 쓰지 않는다).
 * 장치 상태 표는 App.jsx가 renderDeviceStatus로 넣어 준다.
 * ============================================================ */

const PAGE_SIZE = 50;
const POLL_MS = 5000;
// 집계 API가 없는 백엔드에서 미조치 경고를 직접 세는 상한. /api/alerts의 limit 최대값이다.
const OVERVIEW_ALERT_LIMIT = 200;
// 집계 API가 아직 배포되지 않은 백엔드에서만 폴백한다. 권한·서버 오류는 그대로 알린다.
const OVERVIEW_FALLBACK_CODES = new Set(['HTTP_404', 'HTTP_405', 'HTTP_501']);

function message(error) {
  return ({ ADMIN_UNAUTHORIZED: '관리자 계정 또는 비밀번호를 확인해 주세요.',
    ADMIN_NOT_CONFIGURED: '서버에 관리자 인증 정보가 설정되지 않았습니다.',
    ALERT_NOT_FOUND: '경고를 찾을 수 없습니다. 목록을 새로고침해 주세요.',
    KIOSK_NOT_FOUND: '등록되지 않은 키오스크입니다. 목록을 새로고침해 주세요.',
    INVALID_ALERT_RESPONSE: '경고 목록 응답이 올바르지 않습니다.',
    INVALID_OVERVIEW_RESPONSE: '키오스크 현황 응답이 올바르지 않습니다.',
    NETWORK_ERROR: '서버에 연결할 수 없습니다. 조치 완료 여부를 다시 확인해 주세요.',
  })[error.code] || `요청을 처리하지 못했습니다. (${error.code || 'UNKNOWN'})`;
}

function moment(value) {
  return value ? new Date(value).toLocaleString('ko-KR') : '';
}

/* ------------------------------------------------------------
 * 현황 집계
 * ---------------------------------------------------------- */
// 키오스크 하나의 현황. 집계 API 응답(summary)이 없으면 경고가 없는 키오스크로 둔다.
function overviewRow(kiosk, summary) {
  const alarm = Number(summary?.alarm_count) || 0;
  const warning = Number(summary?.warning_count) || 0;
  const devices = summary?.devices;
  return {
    id: kiosk.id,
    name: kiosk.name || kiosk.id,
    location: kiosk.location_description || '',
    alarm,
    warning,
    // 운영 중단 판정은 백엔드 값을 먼저 쓴다. 없으면 미조치 알람 존재 여부와 같다.
    blocked: typeof summary?.blocked === 'boolean' ? summary.blocked : alarm > 0,
    lastAlertAt: summary?.last_alert_at || null,
    devices: devices && typeof devices === 'object' ? devices : null,
  };
}

// 미조치 경고 목록을 키오스크별로 센다. 집계 API가 없는 백엔드에서 쓰는 폴백이다.
function countByKiosk(alerts) {
  const summaries = new Map();
  for (const alert of alerts) {
    const row = summaries.get(alert.kiosk_id)
      || { kiosk_id: alert.kiosk_id, alarm_count: 0, warning_count: 0, last_alert_at: null };
    if (alert.severity === 'alarm') row.alarm_count += 1;
    else row.warning_count += 1;
    if (!row.last_alert_at || Date.parse(alert.reported_at) > Date.parse(row.last_alert_at)) {
      row.last_alert_at = alert.reported_at;
    }
    summaries.set(alert.kiosk_id, row);
  }
  return summaries;
}

// 목록에 있는 모든 키오스크를 그대로 두고 집계를 붙인다. 경고가 없어도 화면에서 빠지지 않는다.
function mergeOverview(kiosks, summaries) {
  const known = new Set(kiosks.map((kiosk) => kiosk.id));
  const rows = kiosks.map((kiosk) => overviewRow(kiosk, summaries.get(kiosk.id)));
  for (const [id, summary] of summaries) {
    if (!known.has(id)) rows.push(overviewRow({ id, name: summary.name, location_description: summary.location_description }, summary));
  }
  return rows;
}

const TONES = {
  blocked: { label: '운영 중단', badge: 'bg-rose-950/50 border-rose-500/50 text-rose-300', border: 'border-rose-500/50' },
  warning: { label: '경고 있음', badge: 'bg-amber-950/40 border-amber-500/40 text-amber-300', border: 'border-amber-500/40' },
  normal: { label: '정상', badge: 'bg-emerald-950/40 border-emerald-500/40 text-emerald-300', border: 'border-slate-700/60' },
};

function tone(row) {
  if (!row) return TONES.normal;
  if (row.blocked || row.alarm > 0) return TONES.blocked;
  return row.warning > 0 ? TONES.warning : TONES.normal;
}

function deviceText(devices) {
  if (!devices) return '';
  const total = Number(devices.total) || 0;
  const online = Number(devices.online) || 0;
  return total ? ` · 장치 ${online}/${total} 온라인` : '';
}

function SummaryTile({ label, value, valueClass = 'text-slate-200' }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`text-lg font-bold tabular-nums mt-0.5 ${valueClass}`}>{value}</p>
    </div>
  );
}

function KioskCard({ row, onSelect }) {
  const style = tone(row);
  return (
    <button
      onClick={() => onSelect(row.id)}
      className={`h-full text-left p-4 rounded-2xl border bg-slate-900/60 hover:bg-slate-800/80 hover:border-indigo-500/60 transition-all flex flex-col gap-3 ${style.border}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-white truncate">{row.name}</h3>
          <p className="text-[11px] text-slate-400 truncate mt-0.5">{row.location || '위치 설명 없음'}</p>
        </div>
        <span className={`px-2.5 py-1 text-[10px] rounded-full border font-medium whitespace-nowrap shrink-0 ${style.badge}`}>
          {style.label}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-auto">
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-2">
          <p className="text-[10px] text-slate-500">미조치 알람</p>
          <p className={`text-base font-bold tabular-nums ${row.alarm ? 'text-rose-300' : 'text-slate-400'}`}>{row.alarm}건</p>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-2">
          <p className="text-[10px] text-slate-500">미조치 경고</p>
          <p className={`text-base font-bold tabular-nums ${row.warning ? 'text-amber-300' : 'text-slate-400'}`}>{row.warning}건</p>
        </div>
      </div>
      <p className="text-[10px] text-slate-500 truncate">
        {row.lastAlertAt ? `최근 경고 ${moment(row.lastAlertAt)}` : '미조치 경고 없음'}{deviceText(row.devices)}
      </p>
    </button>
  );
}

/* ------------------------------------------------------------
 * 경고 조치 카드
 * ---------------------------------------------------------- */
function AlertCard({ alert, label, note, confirmed, pending, locked, onNote, onConfirm, onResolve }) {
  const alarm = alert.severity === 'alarm';
  return (
    <article className={`border rounded-xl p-4 space-y-3 bg-slate-900/40 ${alarm ? 'border-rose-500/60' : 'border-amber-500/60'}`}>
      <div className="flex items-start justify-between gap-3">
        <h4 className="text-sm font-bold text-white flex items-center gap-1.5 min-w-0">
          <TriangleAlert size={14} className={`shrink-0 ${alarm ? 'text-rose-400' : 'text-amber-400'}`} />
          <span>{label}{alert.locker_number != null ? ` · ${alert.locker_number}번 보관함` : ''}</span>
        </h4>
        <span className={`px-2.5 py-1 text-[10px] rounded-full border font-medium whitespace-nowrap shrink-0 ${alarm ? TONES.blocked.badge : TONES.warning.badge}`}>
          {alarm ? '알람 · 운영 중단' : '경고'}
        </span>
      </div>
      <p className="text-[11px] text-slate-500">{moment(alert.reported_at)}</p>
      {alert.resolved_at ? (
        <div className="text-xs text-emerald-300 space-y-1 border-t border-slate-800 pt-3">
          <p className="flex items-center gap-1.5">
            <CircleCheck size={13} className="shrink-0" /> 조치 완료 · {alert.resolved_by} · {moment(alert.resolved_at)}
          </p>
          <p className="whitespace-pre-wrap text-slate-300">{alert.resolution_note}</p>
        </div>
      ) : (
        <div className="space-y-3 border-t border-slate-800 pt-3">
          <label className="block text-xs text-slate-300">조치 내용
            <textarea
              maxLength={2000} rows={3} value={note} disabled={locked}
              onChange={(event) => onNote(event.target.value)}
              className="block w-full mt-1.5 p-3 rounded-xl bg-slate-800 border border-slate-700 text-slate-100 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
            />
          </label>
          <label className="flex items-start gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={confirmed} disabled={locked} onChange={(event) => onConfirm(event.target.checked)} className="mt-0.5" />
            문제 해결 및 운영 재개 가능 여부를 확인했습니다.
          </label>
          <button
            disabled={locked || !confirmed || !note.trim()} onClick={onResolve}
            className="w-full sm:w-auto px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition-all disabled:opacity-40"
          >
            {pending ? '기록 중...' : alarm ? '조치 완료 및 운영 재개' : '검토 및 조치 완료'}
          </button>
        </div>
      )}
    </article>
  );
}

/* ------------------------------------------------------------
 * 화면
 * ---------------------------------------------------------- */
export default function AdminDashboard({ kiosks, kiosksError, labels, renderDeviceStatus }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // Credentials are kept only in this component's memory, never localStorage or a VITE variable.
  const [authorization, setAuthorization] = useState('');
  const [actor, setActor] = useState('');
  // '' 이면 전체 현황, 값이 있으면 그 키오스크의 상세 뷰다.
  const [selected, setSelected] = useState('');
  const [summaries, setSummaries] = useState([]);
  const [partial, setPartial] = useState(false);
  const [summaryError, setSummaryError] = useState('');
  const [summaryLoading, setSummaryLoading] = useState(false);
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

  // 전체 현황. 집계 API를 먼저 쓰고, 아직 없는 백엔드에서는 미조치 경고 목록으로 센다.
  const loadSummaries = useCallback(async (signal) => {
    let grouped;
    let capped = false;
    try {
      const data = await api('/admin/kiosks/overview', { signal, headers: { Authorization: authorization } });
      if (!Array.isArray(data)) throw new ApiError(0, 'INVALID_OVERVIEW_RESPONSE');
      grouped = new Map(data.map((row) => [row.kiosk_id, row]));
    } catch (err) {
      if (!OVERVIEW_FALLBACK_CODES.has(err?.code)) throw err;
      const alerts = await api(`/alerts?unresolved_only=true&limit=${OVERVIEW_ALERT_LIMIT}`, { signal });
      if (!Array.isArray(alerts)) throw new ApiError(0, 'INVALID_ALERT_RESPONSE');
      capped = alerts.length >= OVERVIEW_ALERT_LIMIT;
      grouped = countByKiosk(alerts);
    }
    return { rows: mergeOverview(kiosks, grouped), capped };
  }, [authorization, kiosks]);

  useEffect(() => {
    if (!authorization) return undefined;
    const controller = new AbortController();
    let polling = false;
    const load = async () => {
      if (polling || controller.signal.aborted) return;
      polling = true;
      setSummaryLoading(true);
      try {
        const result = await loadSummaries(controller.signal);
        if (!controller.signal.aborted) { setSummaries(result.rows); setPartial(result.capped); setSummaryError(''); }
      } catch (err) {
        if (!controller.signal.aborted) setSummaryError(message(err));
      } finally {
        polling = false;
        if (!controller.signal.aborted) setSummaryLoading(false);
      }
    };
    load();
    // 상세 뷰에서는 현황을 주기 조회하지 않는다. 들어올 때와 조치 뒤에 한 번씩만 갱신한다.
    if (selected) return () => controller.abort();
    const timer = setInterval(load, POLL_MS);
    return () => { controller.abort(); clearInterval(timer); };
  }, [authorization, loadSummaries, revision, selected]);

  // 선택한 키오스크의 경고 목록. 상세 뷰에서만 조회한다.
  useEffect(() => {
    setRows([]);
    if (!authorization || !selected) return undefined;
    const controller = new AbortController();
    listing.current = controller;
    let polling = false;
    const load = async () => {
      if (polling || controller.signal.aborted) return;
      polling = true;
      setLoading(true);
      try {
        const query = new URLSearchParams({ kiosk_id: selected, unresolved_only: String(!history),
          limit: String(PAGE_SIZE), offset: String(offset) });
        const data = await api(`/admin/alerts?${query}`, { signal: controller.signal, headers: { Authorization: authorization } });
        if (!Array.isArray(data)) throw new ApiError(0, 'INVALID_ALERT_RESPONSE');
        if (!controller.signal.aborted) { setRows(data); setLoadError(''); }
      } catch (err) {
        if (!controller.signal.aborted) setLoadError(message(err));
      } finally {
        polling = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { controller.abort(); clearInterval(timer); };
  }, [authorization, selected, history, offset, revision]);

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

  const logout = () => {
    listing.current?.abort();
    setAuthorization(''); setSelected(''); setSummaries([]); setRows([]);
    setNotes({}); setConfirmed({}); setError(''); setNotice(''); setLoadError(''); setSummaryError('');
  };

  const open = (id) => { setSelected(id); setHistory(false); setOffset(0); setNotice(''); setError(''); setLoadError(''); };
  const back = () => { setSelected(''); setNotice(''); setError(''); setLoadError(''); };

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

  if (!authorization) return (
    <section className="w-full max-w-lg mx-auto p-6 rounded-2xl bg-slate-950 border border-slate-700">
      <h2 className="text-xl font-bold text-white">장치 관리 · 관리자 로그인</h2>
      <p className="text-xs text-slate-400 mt-1.5">로그인 정보는 이 화면의 메모리에만 유지되므로 새로고침 후 다시 로그인합니다.</p>
      <form onSubmit={login} className="mt-6 space-y-4">
        <label className="block text-sm text-slate-300">관리자 계정
          <input required autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)}
            className="block w-full mt-1.5 bg-slate-800 border border-slate-700 rounded-xl p-3 text-slate-100 focus:outline-none focus:border-indigo-500" />
        </label>
        <label className="block text-sm text-slate-300">비밀번호
          <input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)}
            className="block w-full mt-1.5 bg-slate-800 border border-slate-700 rounded-xl p-3 text-slate-100 focus:outline-none focus:border-indigo-500" />
        </label>
        {error && <p role="alert" className="text-rose-300 text-sm">{error}</p>}
        <button disabled={!!busy} className="w-full bg-indigo-600 hover:bg-indigo-500 rounded-xl p-3 text-sm font-semibold text-white transition-all disabled:opacity-40">
          {busy ? '확인 중...' : '관리자 로그인'}
        </button>
      </form>
    </section>
  );

  const current = selected ? (summaries.find((row) => row.id === selected) || null) : null;
  const currentKiosk = kiosks.find((kiosk) => kiosk.id === selected);
  const style = tone(current);
  const blocked = summaries.filter((row) => row.blocked || row.alarm > 0).length;
  const alarms = summaries.reduce((sum, row) => sum + row.alarm, 0);
  const warnings = summaries.reduce((sum, row) => sum + row.warning, 0);

  return (
    <section className="p-5 sm:p-6 rounded-2xl bg-slate-950 border border-slate-700 space-y-5">
      <div className="flex items-start justify-between gap-3 border-b border-slate-700 pb-4">
        <div className="flex items-center gap-3 min-w-0">
          {selected && (
            <button onClick={back} aria-label="전체 키오스크 현황으로" className="text-slate-400 hover:text-white p-2 rounded-lg bg-slate-800 shrink-0">
              <ArrowLeft size={16} />
            </button>
          )}
          <div className="min-w-0">
            <h2 className="text-xl font-bold text-white truncate">
              {selected ? (current?.name || currentKiosk?.name || '선택한 키오스크') : '키오스크 장치 현황'}
            </h2>
            <p className="text-xs text-slate-400 mt-0.5 truncate">
              {selected
                ? (current?.location || currentKiosk?.location_description || '위치 설명 없음')
                : '모든 키오스크의 알람·경고를 확인하고, 키오스크를 선택해 장치 상태와 조치를 처리합니다.'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {selected && (
            <span className={`px-3 py-1 text-xs rounded-full border font-medium whitespace-nowrap ${style.badge}`}>{style.label}</span>
          )}
          <button disabled={!!busy} onClick={logout} className="text-xs text-slate-400 hover:text-white underline whitespace-nowrap">
            {actor} · 로그아웃
          </button>
        </div>
      </div>

      {kiosksError && <p className="text-xs text-rose-300">{kiosksError}</p>}
      {summaryError && <p role="alert" className="text-xs text-rose-300">{summaryError}</p>}

      {!selected ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <SummaryTile label="전체 키오스크" value={`${summaries.length}대`} />
            <SummaryTile label="운영 중단" value={`${blocked}대`} valueClass={blocked ? 'text-rose-300' : 'text-slate-400'} />
            <SummaryTile label="미조치 알람" value={`${alarms}건`} valueClass={alarms ? 'text-rose-300' : 'text-slate-400'} />
            <SummaryTile label="미조치 경고" value={`${warnings}건`} valueClass={warnings ? 'text-amber-300' : 'text-slate-400'} />
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <button
              disabled={summaryLoading} onClick={() => setRevision((value) => value + 1)}
              className="inline-flex items-center gap-1.5 text-slate-400 hover:text-white underline disabled:opacity-40"
            >
              <RefreshCw size={12} /> 현황 새로고침
            </button>
            <span className="text-[10px] text-slate-500">{summaryLoading ? '갱신 중...' : `${POLL_MS / 1000}초마다 자동 갱신`}</span>
          </div>
          {partial && (
            <p className="text-[11px] text-amber-300">
              미조치 경고가 {OVERVIEW_ALERT_LIMIT}건을 넘어 일부만 집계했습니다. 키오스크를 선택해 전체 목록을 확인해 주세요.
            </p>
          )}
          {summaries.length === 0 ? (
            <p className="text-sm text-slate-400">표시할 키오스크가 없습니다.</p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 items-stretch">
              {summaries.map((row) => <KioskCard key={row.id} row={row} onSelect={open} />)}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          {renderDeviceStatus(selected)}
          <div className="space-y-4">
            <div>
              <h3 className="text-base font-bold text-white">경고 검토 및 조치</h3>
              <p className="text-xs text-slate-400 mt-1">
                문제 해결과 운영 재개 가능 여부를 확인한 뒤 조치 내용을 기록하세요. 보관·수령 상태는 변경되지 않습니다.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-300">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={history} disabled={!!busy}
                  onChange={(event) => { setHistory(event.target.checked); setOffset(0); }} />
                조치 완료 이력 포함
              </label>
              <button
                disabled={!!busy || loading} onClick={() => setRevision((value) => value + 1)}
                className="inline-flex items-center gap-1.5 text-slate-400 hover:text-white underline disabled:opacity-40"
              >
                <RefreshCw size={12} /> 경고 새로고침
              </button>
            </div>
            {error && <p role="alert" className="text-xs text-rose-300">{error}</p>}
            {loadError && <p role="alert" className="text-xs text-rose-300">{loadError}</p>}
            {notice && <p role="status" className="text-xs text-emerald-300">{notice}</p>}
            {!loading && !error && !loadError && rows.length === 0 && (
              <p className="text-sm text-slate-400">표시할 경고가 없습니다.</p>
            )}
            <div className="space-y-3">
              {rows.map((alert) => (
                <AlertCard
                  key={alert.id} alert={alert}
                  label={labels[alert.code] || alert.message || alert.code}
                  note={notes[alert.id] || ''} confirmed={!!confirmed[alert.id]}
                  pending={busy === alert.id} locked={!!busy}
                  onNote={(value) => setNotes((state) => ({ ...state, [alert.id]: value }))}
                  onConfirm={(value) => setConfirmed((state) => ({ ...state, [alert.id]: value }))}
                  onResolve={() => resolve(alert)}
                />
              ))}
            </div>
            <div className="flex items-center gap-4 text-xs text-slate-300">
              <button disabled={!!busy || loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
                className="underline disabled:opacity-40">이전 경고</button>
              <span className="tabular-nums text-slate-400">{offset / PAGE_SIZE + 1}페이지</span>
              <button disabled={!!busy || loading || rows.length < PAGE_SIZE} onClick={() => setOffset((value) => value + PAGE_SIZE)}
                className="underline disabled:opacity-40">다음 경고</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
