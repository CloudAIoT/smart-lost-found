import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, API_BASE } from './api.js';
import { aiStatusText as formatAiStatus, analysisPendingText, analyzeAndOpenRegistration, ownershipPendingText, waitForOwnership } from './aiProgress.js';
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  Boxes,
  Camera,
  CircleCheck,
  DoorOpen,
  Footprints,
  Globe,
  HandHeart,
  House,
  Info,
  KeyRound,
  Lightbulb,
  LoaderCircle,
  LocateFixed,
  LockOpen,
  MapPin,
  MapPinned,
  Monitor,
  RefreshCw,
  RotateCw,
  TriangleAlert,
} from 'lucide-react';

/* ============================================================
 * 설정
 * ------------------------------------------------------------
 * 백엔드(http://52.78.12.168)에 CORS 설정이 없으므로
 * vite.config.js의 dev 프록시를 거쳐 호출한다.
 * ============================================================ */

const LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_JS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
// CartoDB voyager 타일은 현재 API 키를 요구하므로 OpenStreetMap 기본 타일을 사용한다.
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const DEFAULT_CENTER = [37.5665, 126.978]; // 좌표가 하나도 없을 때의 지도 중심(서울시청)

// 2차 촬영 각도 가이드: 이미지 처리 AI 파트의 실험 결과가 나오면 이 문구를 교체한다.
const SHOT2_GUIDE = '물건의 다른면이 카메라에 노출되도록 물건의 방향을 조정해주세요.';

// 카메라 전원이 꺼져 있는 등 장치 자체의 기술적 오류는 500으로 내려온다.
const CAMERA_HARDWARE_ERROR_MESSAGE = '현재 카메라에 기술적인 문제가 발생하였습니다. 관리자에게 문의하세요.';

// 수령 인증: QR 없이 백엔드가 발급한 8자리 OTP를 입력한다.
// 현재 백엔드는 Base32(A~Z, 2~7) 8자리를 발급하므로 영문 대문자·숫자를 모두 허용한다.
const OTP_LENGTH = 8;
const OTP_PATTERN = /^[A-Z0-9]{8}$/;
const normalizeOtp = (v) => v.toUpperCase().replace(/[^A-Z0-9]/g, '');

// 개발자용 테스트 OTP: `npm run dev`(개발 서버)에서만 동작하며 빌드(배포)본에서는 비활성화된다.
// 백엔드를 호출하지 않고 수령 화면 흐름만 확인한다. 실제 보관함은 열리지 않는다.
const DEV_OTP = import.meta.env.DEV ? 'DEV00000' : null;

/* ============================================================
 * API 유틸
 * ============================================================ */
const ERROR_MESSAGES = {
  NETWORK_ERROR: '서버에 연결할 수 없습니다. 네트워크 또는 프록시 설정을 확인해 주세요.',
  VALIDATION_ERROR: '입력값이 올바르지 않습니다.',
  BUSINESS_API_DISABLED: '서버의 업무 API가 비활성화되어 있습니다.',
  KIOSK_NOT_FOUND: '등록되지 않은 키오스크입니다.',
  CATEGORY_NOT_FOUND: '선택한 카테고리를 찾을 수 없습니다.',
  REGISTRATION_NOT_FOUND: '등록 정보를 찾을 수 없습니다.',
  IMAGE_NOT_FOUND: '촬영된 사진을 찾을 수 없습니다.',
  ITEM_NOT_FOUND: '보관 중인 물건을 찾을 수 없습니다.',
  RETURN_CODE_NOT_FOUND: 'OTP 번호가 일치하지 않습니다. 다시 확인해 주세요.',
  INVALID_REGISTRATION_STATE: '현재 단계에서 처리할 수 없는 요청입니다. 이미 진행 중이거나 처리된 등록입니다.',
  SHOT_OUT_OF_ORDER: '첫 번째 촬영을 먼저 진행해 주세요.',
  TWO_IMAGES_REQUIRED: '사진 두 장이 모두 필요합니다.',
  ANALYSIS_REQUIRED: '사진 분석이 완료되지 않아 보관함을 열 수 없습니다.',
  ANALYSIS_FAILED: '사진 분석에 실패했습니다. 다시 시도해 주세요.',
  ANALYSIS_TIMED_OUT: '사진 분석 시간이 초과되었습니다. 다시 시도해 주세요.',
  INVALID_AI_RESPONSE: 'AI 진행 상태를 확인할 수 없습니다. 다시 시도해 주세요.',
  NO_AVAILABLE_LOCKER: '현재 비어 있는 보관함이 없습니다.',
  ALLOCATION_CONFLICT: '보관함 배정 중 충돌이 발생했습니다. 잠시 후 다시 시도해 주세요.',
  DEPOSIT_NOT_OPENED: '보관함이 아직 열리지 않았습니다.',
  DOOR_NOT_CLOSED: '보관함 문이 닫히지 않았습니다. 문을 닫은 뒤 다시 눌러주세요.',
  ITEM_NOT_STORED: '보관 중인 상태의 물건이 아닙니다.',
  ITEM_NOT_DETECTED: '물건이 감지되지 않았어요. 다시 넣어 주세요.',
  ITEM_STILL_INSIDE: '보관함에 물건이 남아 있어요. 다시 열어 꺼내 주세요.',
  PICKUP_NOT_OPENED: '보관함이 아직 열리지 않았습니다.',
  RETURN_CODE_EXPIRED: 'OTP 유효기간이 지났습니다. 웹에서 다시 발급받아 주세요.',
  RETURN_CODE_ALREADY_USED: '이미 사용된 OTP입니다.',
  RETURN_CODE_UNUSABLE: '사용할 수 없는 OTP입니다. 관리자에게 문의해 주세요.',
  RETURN_CODE_IN_PROGRESS: '해당 OTP로 보관함을 여는 중입니다. 잠시 후 다시 시도해 주세요.',
  RETURN_CODE_WRONG_KIOSK: '이 키오스크에 보관된 물건의 OTP가 아닙니다.',
  RETURN_CODE_CONFLICT: 'OTP 처리 중 충돌이 발생했습니다. 잠시 후 다시 시도해 주세요.',
  DEVICE_UNREACHABLE: '보관함·촬영 장치와 통신할 수 없습니다.',
  INVALID_DEVICE_RESPONSE: '장치 응답이 올바르지 않습니다.',
  DEVICE_ROLE_MISMATCH: '장치 설정이 올바르지 않습니다.',
  DEVICE_RESPONSE_TOO_LARGE: '장치 응답이 너무 큽니다.',
  INVALID_IMAGE: '촬영된 사진이 올바르지 않습니다.',
  IMAGE_STORAGE_FAILED: '사진 저장에 실패했습니다.',
  INVALID_ANALYSIS_RESULT: '사진 분석 결과가 올바르지 않습니다.',
  AI_NOT_CONFIGURED: '사진 분석(AI) 서비스가 설정되지 않았습니다.',
  DEVICE_NOT_CONFIGURED: '장치 연결이 설정되지 않았습니다.',
  CAPTURE_NOT_CONFIGURED: '촬영 장치가 설정되지 않았습니다.',
  IMAGE_STORAGE_NOT_CONFIGURED: '사진 저장소가 설정되지 않았습니다.',
  OWNERSHIP_ATTEMPT_EXPIRED: '확인 시간이 지났습니다. 검색을 다시 시작해 주세요.',
  OWNERSHIP_ATTEMPT_RESTART_REQUIRED: '소유 확인 방식이 업데이트됐습니다. 검색을 다시 시작해 주세요.',
  OWNERSHIP_RETURN_CODE_ALREADY_ISSUED: '이 소유 확인에서 이미 OTP가 발급됐습니다.',
  OWNERSHIP_ITEM_NO_LONGER_AVAILABLE: '이 물건은 현재 수령할 수 없습니다.',
  OWNERSHIP_PHOTO_NOT_PENDING: '사진 확인이 끝났거나 아직 시작되지 않았습니다.',
  OWNERSHIP_PHOTO_UNAVAILABLE: '확인할 등록 사진을 찾을 수 없습니다.',
  OWNERSHIP_PHOTO_LOAD_REQUIRED: '등록 사진을 불러온 다음 다시 확인해 주세요.',
  OWNERSHIP_AI_NOT_CONFIGURED: '분실물 검색 또는 이미지 분석 서비스가 설정되지 않았습니다.',
  OWNERSHIP_AI_RATE_LIMITED: 'AI 요청이 많습니다. 잠시 후 다시 시도해 주세요.',
  OWNERSHIP_AI_TIMEOUT: 'AI 응답 시간이 초과되었습니다. 다시 시도해 주세요.',
  OWNERSHIP_AI_UNREACHABLE: 'AI 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
  OWNERSHIP_AI_REQUEST_FAILED: 'AI 요청을 처리하지 못했습니다. 다시 시도해 주세요.',
  OWNERSHIP_SEARCH_FAILED: '분실물 검색에 실패했습니다. 다시 검색해 주세요.',
  RETURN_CODE_NOT_CONFIGURED: 'OTP 기능이 설정되지 않았습니다.',
};

function errorText(err) {
  const code = err?.code || 'UNKNOWN';
  const msg = ERROR_MESSAGES[code] || '요청을 처리하지 못했습니다.';
  return `${msg} (${code})`;
}

// 장치 경고: GET /api/alerts 응답의 severity('alarm'|'warning')를 그대로 쓴다(backend DeviceAlert).
// code는 FORCED_OPEN, ITEM_MISSING, TAMPER, ITEM_NOT_DEPOSITED, ITEM_NOT_TAKEN, CAPTURE_ITEM_LEFT.
const ALERT_LABELS = {
  FORCED_OPEN: '보관함 강제 개방',
  ITEM_MISSING: '강제 개방 후 물건 없음',
  TAMPER: '보관함 이상 조작 감지',
  ITEM_NOT_DEPOSITED: '투입 미확인',
  ITEM_NOT_TAKEN: '수령 후 물건 남음',
  CAPTURE_ITEM_LEFT: '촬영 공간에 물건 남음',
};

// 분실물 등록 카테고리 선택지(고정 목록). '기타' 선택 시 자연어 입력을 받는다.
const CATEGORY_OPTIONS = [
  '전자기기', '지갑·카드·신분증', '가방·파우치', '의류·패션소품', '열쇠·키링',
  '문구·책·서류', '안경', '우산', '물병·텀블러', '액세서리·귀중품', '기타',
];

// 고정 카테고리 라벨을 백엔드 카테고리(id)에 매칭한다. 일치하는 항목이 없으면 null이며,
// 이 경우에도 라벨 자체는 secondary_description으로 함께 전달한다.
function matchCategoryId(categories, label) {
  if (!label || label === '기타') return null;
  const norm = (s) => String(s || '').replace(/\s/g, '');
  const target = norm(label);
  const found = categories.find((c) => {
    const name = norm(c.name);
    const code = norm(c.code);
    if (name === target || code === target) return true;
    return target.split('·').some((part) => part && (name.includes(part) || code.includes(part)));
  });
  return found ? found.id : null;
}

/* ============================================================
 * 지도·거리 유틸
 * ============================================================ */
let leafletPromise = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = LEAFLET_CSS;
    document.head.appendChild(css);
    const script = document.createElement('script');
    script.src = LEAFLET_JS;
    script.async = true;
    script.onload = () => resolve(window.L);
    script.onerror = () => {
      leafletPromise = null;
      reject(new Error('LEAFLET_LOAD_FAILED'));
    };
    document.head.appendChild(script);
  });
  return leafletPromise;
}

const hasCoords = (k) => typeof k?.latitude === 'number' && typeof k?.longitude === 'number';

function distanceMeters(a, b) {
  const R = 6371000;
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(b[0] - a[0]);
  const dLng = rad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function formatDistance(m) {
  const walk = Math.max(1, Math.round(m / 80));
  const dist = m < 1000 ? `${Math.round(m / 10) * 10}m` : `${(m / 1000).toFixed(1)}km`;
  return `약 ${dist} (도보 ${walk}분)`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

/* ============================================================
 * 전역 스타일 (index.css의 Vite 기본 스타일 상쇄 + 프로토타입 스타일)
 * ============================================================ */
const GLOBAL_STYLE = `
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
html { font-size: 16px !important; line-height: 1.5; color-scheme: dark; }
#root { width: 100% !important; max-width: none !important; margin: 0 !important; border: none !important; text-align: left !important; }
.slf-app, .slf-app * { font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif; }
.slf-app h1, .slf-app h2, .slf-app h3, .slf-app h4 { margin: 0; letter-spacing: normal; line-height: 1.3; }
.slf-app .font-mono { font-family: ui-monospace, Consolas, monospace; }
.kiosk-frame { border: 12px solid #1f2937; border-radius: 28px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.4); }
.scanner-laser { background: linear-gradient(180deg, rgba(239,68,68,0) 0%, rgba(239,68,68,0.8) 50%, rgba(239,68,68,0) 100%); animation: slf-scan 2s infinite ease-in-out; }
@keyframes slf-scan { 0% { top: 0%; } 50% { top: 85%; } 100% { top: 0%; } }
.leaflet-container { background-color: #0f172a !important; border-radius: 0.75rem; }
/* 터치(스마트폰·아이패드 키오스크) 지원: 최소 터치 영역, 입력 시 자동 확대 방지, 더블탭 지연 제거 */
.slf-app button, .slf-app select, .slf-app [role="button"] { min-height: 44px; touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
.slf-app input, .slf-app textarea, .slf-app select { font-size: 16px; }
.slf-app input[type="radio"] { min-height: 0; width: 20px; height: 20px; }
.slf-app button:active:not(:disabled) { transform: scale(0.98); }
`;

/* ============================================================
 * 공통 컴포넌트
 * ============================================================ */
function BackHeader({ onBack, title, desc, badge, badgeClass }) {
  return (
    <div className="flex items-center justify-between border-b border-slate-700 pb-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-slate-400 hover:text-white p-2 rounded-lg bg-slate-800">
          <ArrowLeft size={16} />
        </button>
        <div>
          <h2 className="text-xl font-bold text-white">{title}</h2>
          <p className="text-xs text-slate-400">{desc}</p>
        </div>
      </div>
      <span className={`px-3 py-1 text-xs rounded-full border font-medium whitespace-nowrap ${badgeClass}`}>{badge}</span>
    </div>
  );
}

/* ============================================================
 * 지도 (Leaflet, CDN 동적 로드)
 * ============================================================ */
function KioskMap({ kiosks, userPos, focus }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const markersRef = useRef({});
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadLeaflet()
      .then((L) => {
        if (cancelled || !containerRef.current || mapRef.current) return;
        const map = L.map(containerRef.current).setView(DEFAULT_CENTER, 14);
        L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map);
        layerRef.current = L.layerGroup().addTo(map);
        mapRef.current = map;
        setReady(true);
        setTimeout(() => map.invalidateSize(), 200);
      })
      .catch(() => !cancelled && setLoadError(true));
    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        layerRef.current = null;
      }
      setReady(false);
    };
  }, []);

  // 마커 갱신
  useEffect(() => {
    if (!ready || !window.L) return;
    const L = window.L;
    const layer = layerRef.current;
    layer.clearLayers();
    markersRef.current = {};
    const points = [];
    kiosks.filter(hasCoords).forEach((k) => {
      const pos = [k.latitude, k.longitude];
      const marker = L.marker(pos).addTo(layer);
      marker.bindPopup(
        `<b>${escapeHtml(k.name)}</b><br><span style="font-size:11px;">${escapeHtml(k.location_description || '')}</span>`,
      );
      markersRef.current[k.id] = marker;
      points.push(pos);
    });
    if (userPos) {
      L.circleMarker(userPos, { radius: 8, color: '#6366f1', fillColor: '#818cf8', fillOpacity: 0.9, weight: 3 })
        .addTo(layer)
        .bindPopup('<b>내 위치</b>');
      points.push(userPos);
    }
    if (points.length === 1) mapRef.current.setView(points[0], 15);
    else if (points.length > 1) mapRef.current.fitBounds(points, { padding: [30, 30], maxZoom: 16 });
  }, [ready, kiosks, userPos]);

  // 목록에서 선택한 키오스크로 이동
  useEffect(() => {
    if (!ready || !focus) return;
    const k = kiosks.find((x) => x.id === focus.id);
    if (!hasCoords(k)) return;
    mapRef.current.setView([k.latitude, k.longitude], 16);
    markersRef.current[k.id]?.openPopup();
  }, [ready, focus, kiosks]);

  return (
    <div className="relative w-full flex-1 rounded-xl overflow-hidden">
      <div ref={containerRef} className="absolute inset-0 rounded-xl z-10" />
      {loadError && (
        <div className="absolute inset-0 z-20 flex items-center justify-center text-xs text-rose-300 bg-slate-900/90">
          지도 라이브러리를 불러오지 못했습니다.
        </div>
      )}
    </div>
  );
}

/* ============================================================
 * 웹 포털 - 습득자: 키오스크 위치 조회 (GET /api/kiosks)
 * ============================================================ */
function WebFinder({ kiosks, kiosksError, onReload, onBack, onGoKiosk }) {
  const [userPos, setUserPos] = useState(null);
  const [geoStatus, setGeoStatus] = useState('pending');
  const [focus, setFocus] = useState(null);

  useEffect(() => {
    if (!navigator.geolocation) {
      setGeoStatus('unsupported');
      return;
    }
    // 브라우저는 HTTPS(또는 localhost)에서만 현재 위치를 제공한다.
    if (!window.isSecureContext) {
      setGeoStatus('insecure');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setUserPos([p.coords.latitude, p.coords.longitude]);
        setGeoStatus('ok');
      },
      () => setGeoStatus('denied'),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }, []);

  const sorted = useMemo(() => {
    const list = kiosks.map((k) => ({
      ...k,
      distance: userPos && hasCoords(k) ? distanceMeters(userPos, [k.latitude, k.longitude]) : null,
    }));
    return list.sort((a, b) => (a.distance ?? Infinity) - (b.distance ?? Infinity));
  }, [kiosks, userPos]);

  const geoText = {
    pending: '현재 위치 확인 중...',
    ok: '현재 위치 기준 거리순',
    denied: '위치 권한이 거부되어 거리를 계산할 수 없습니다',
    unsupported: '이 브라우저는 위치 조회를 지원하지 않습니다',
    insecure: 'HTTPS 연결에서만 현재 위치를 사용할 수 있습니다',
  }[geoStatus];

  return (
    <div className="space-y-6">
      <BackHeader
        onBack={onBack}
        title="가장 가까운 키오스크 찾기"
        desc="습득하신 물건을 보관할 인근 스마트 보관함 위치입니다."
        badge="습득자 모드"
        badgeClass="bg-indigo-500/20 text-indigo-400 border-indigo-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 지도 영역 */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-700 rounded-2xl p-3 flex flex-col h-[320px] sm:h-[400px] relative overflow-hidden">
          <div className="flex justify-between items-center mb-2 px-1">
            <span className="text-xs text-slate-400 flex items-center gap-1.5 font-medium">
              <LocateFixed size={14} className="text-indigo-400" /> 내 주변 보관함 지도
            </span>
            <span className="text-[10px] text-slate-500">{geoText}</span>
          </div>
          <KioskMap kiosks={kiosks} userPos={userPos} focus={focus} />
        </div>

        {/* 키오스크 목록 */}
        <div className="space-y-4 flex flex-col h-[360px] sm:h-[460px] overflow-y-auto pr-1">
          <div className="text-xs text-slate-400 font-medium px-1 flex justify-between">
            <span>추천 스마트 키오스크</span>
            <span className="text-indigo-400">총 {kiosks.length}곳</span>
          </div>

          {kiosksError && (
            <div className="p-3.5 bg-rose-950/40 border border-rose-500/40 rounded-xl text-xs text-rose-300 space-y-2">
              <p>{kiosksError}</p>
              <button onClick={onReload} className="inline-flex items-center gap-1 text-rose-200 underline">
                <RefreshCw size={12} /> 다시 불러오기
              </button>
            </div>
          )}
          {!kiosksError && kiosks.length === 0 && (
            <div className="p-3.5 bg-slate-800/60 border border-slate-700 rounded-xl text-xs text-slate-400">
              등록된 키오스크가 없습니다.
            </div>
          )}

          {sorted.map((k, i) => {
            const active = focus?.id === k.id || (!focus && i === 0);
            return (
              <div
                key={k.id}
                role="button"
                tabIndex={0}
                onClick={() => setFocus({ id: k.id, t: Date.now() })}
                onKeyDown={(e) => e.key === 'Enter' && setFocus({ id: k.id, t: Date.now() })}
                className={`p-4 border rounded-xl cursor-pointer transition-all space-y-1.5 shrink-0 ${
                  active ? 'bg-slate-800 border-indigo-500/40' : 'bg-slate-800/60 border-slate-700 hover:bg-slate-800'
                }`}
              >
                <div className="flex flex-wrap justify-between items-start gap-x-2 gap-y-1">
                  <div className="min-w-0">
                    <h4 className="font-bold text-white text-sm break-keep">{k.name}</h4>
                    <span className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 rounded-full px-2 py-0.5 whitespace-nowrap">
                      <Footprints size={11} />
                      {k.distance != null ? formatDistance(k.distance) : '거리 정보 없음'}
                    </span>
                  </div>
                  {!hasCoords(k) && (
                    <span className="text-[11px] bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full whitespace-nowrap shrink-0">
                      좌표 미등록
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-400 flex items-start gap-1">
                  <MapPin size={12} className="text-slate-500 mt-0.5 shrink-0" />
                  <span className="min-w-0 break-keep">{k.location_description || '장소 설명 없음'}</span>
                </p>
                {hasCoords(k) && (
                  <div className="text-right">
                    <span className="text-[11px] text-indigo-400 underline">위치 보기</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="p-4 bg-indigo-950/40 border border-indigo-500/30 rounded-2xl flex flex-col sm:flex-row justify-between items-center gap-3">
        <div className="text-xs text-slate-300">
          <p className="font-bold text-indigo-300 flex items-center gap-1">
            <Lightbulb size={14} /> 현장 키오스크 방문 등록 안내
          </p>
          <p className="text-slate-400 mt-0.5">
            상기 안내된 키오스크로 직접 방문하여 [분실물 등록] 버튼을 누르시면 보관함에 전달하실 수 있습니다.
          </p>
        </div>
        <button
          onClick={onGoKiosk}
          className="w-full sm:w-auto px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold whitespace-nowrap inline-flex items-center justify-center gap-1"
        >
          키오스크 모드로 이동 <ArrowRight size={12} />
        </button>
      </div>
    </div>
  );
}

const OWNERSHIP_SLOW_HINT_MS = 8000;

/* ============================================================
 * 웹 포털 - 분실자
 * ============================================================ */
function WebOwner({ onBack, onResult }) {
  const [description, setDescription] = useState('');
  const [attempt, setAttempt] = useState(null);
  const [optionId, setOptionId] = useState('');
  const [photo, setPhoto] = useState(null);
  const [photoReady, setPhotoReady] = useState(false);
  const [photoError, setPhotoError] = useState(false);
  const [photoRetry, setPhotoRetry] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null); // { stage, elapsedMs } while backend AI search is running
  const [aiStatusText, setAiStatusText] = useState('');
  const photoAttemptId = attempt?.status === 'photo_confirmation' ? attempt.attempt_id : null;
  const requestRef = useRef(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    if (!photoAttemptId) return;
    const controller = new AbortController();
    let url;
    setPhoto(null);
    setPhotoReady(false);
    setPhotoError(false);
    const loadPhoto = async () => {
      try {
        const response = await fetch(`${API_BASE}/ownership/attempts/${photoAttemptId}/photo`, {
          signal: controller.signal,
        });
        const token = response.headers.get('X-Ownership-Photo-Token');
        if (!response.ok || !token) throw new Error('Photo unavailable');
        const jpeg = await response.blob();
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(jpeg);
        setPhoto({ url, token });
      } catch {
        if (!controller.signal.aborted) setPhotoError(true);
      }
    };
    loadPhoto();
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoAttemptId, photoRetry]);

  const accept = async (initial, signal) => {
    const started = Date.now();
    const result = await waitForOwnership(initial, {
      signal,
      onProgress: (progress) => setPending({
        stage: progress.stage, elapsedMs: progress.elapsed_ms ?? (Date.now() - started),
      }),
    });
    signal.throwIfAborted();
    setPending(null);
    setAiStatusText('');
    setOptionId('');
    if (result.status === 'question' || result.status === 'photo_confirmation' || result.decision === 'supported') {
      setAttempt(result);
    } else if (result.status === 'no_candidates' || result.status === 'completed') {
      onResult({ status: result.status === 'no_candidates' ? 'not_found' : 'not_confirmed' });
    } else {
      setError(result.status === 'search_limit'
        ? '검색 가능한 물품 수를 초과했습니다. 잠시 후 다시 시도해 주세요.'
        : '물건 종류나 색상, 잃어버린 장소 같은 단서를 더 적어 주세요.');
    }
  };

  const checkAiStatus = async () => {
    const signal = requestRef.current?.signal;
    if (!signal || signal.aborted) return;
    try {
      const status = await api('/ai/status', { signal });
      if (!signal.aborted) setAiStatusText(formatAiStatus(status));
    } catch (err) {
      if (!signal.aborted) setAiStatusText(errorText(err));
    }
  };

  const submit = async (event, confirmed = true) => {
    event.preventDefault();
    if (requestRef.current || busy || verified || (photoAttemptId && confirmed && (!photoReady || photoError))) return;
    const controller = new AbortController();
    requestRef.current = controller;
    const { signal } = controller;
    setBusy(true);
    setError('');
    setAiStatusText('');
    try {
      if (photoAttemptId) {
        const result = await api(`/ownership/attempts/${photoAttemptId}/photo-confirmation`, {
          method: 'POST',
          body: confirmed ? { confirmed, photo_token: photo.token } : { confirmed },
          signal,
        });
        signal.throwIfAborted();
        const code = result.return_code;
        if (code) {
          onResult({ status: 'success', code: code.code, expiresAt: code.expires_at, kioskName: code.kiosk_name });
        } else {
          await accept(result.attempt, signal);
        }
      } else if (attempt?.question) {
        const result = await api(`/ownership/attempts/${attempt.attempt_id}/answers`, {
          method: 'POST',
          body: { question_id: attempt.question.id, option_id: optionId },
          signal,
        });
        await accept(result, signal);
      } else {
        await accept(await api('/ownership/attempts', {
          method: 'POST', body: { description: description.trim() }, signal,
        }), signal);
      }
    } catch (err) {
      if (signal.aborted) return;
      if (err.code === 'OWNERSHIP_QUESTION_NOT_CURRENT') {
        try {
          await accept(await api(`/ownership/attempts/${attempt.attempt_id}`, { signal }), signal);
        } catch (refreshError) {
          if (!signal.aborted) setError(errorText(refreshError));
        }
      } else {
        setError(errorText(err));
      }
    } finally {
      if (requestRef.current === controller) {
        if (!signal.aborted) {
          setBusy(false);
          setPending(null);
        }
        requestRef.current = null;
        controller.abort();
      }
    }
  };

  const question = attempt?.question;
  const verified = attempt?.decision === 'supported';
  const restart = () => {
    requestRef.current?.abort();
    requestRef.current = null;
    setPending(null);
    setAiStatusText('');
    setBusy(false);
    setAttempt(null);
    setOptionId('');
    setPhoto(null);
    setPhotoReady(false);
    setPhotoError(false);
    setError('');
  };

  return (
    <div className="space-y-6">
      <BackHeader
        onBack={onBack}
        title="잃어버린 물건 찾기"
        desc="물건의 특징이나 잃어버린 장소를 기억나는 대로 적어 주세요."
        badge="분실자 모드"
        badgeClass="bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
      />

      <form onSubmit={submit} className="space-y-4 max-w-xl mx-auto py-2 w-full">
        {verified ? (
          <p className="text-sm text-emerald-300">이미 완료된 소유 확인입니다. 새 OTP가 필요하면 검색을 다시 시작해 주세요.</p>
        ) : pending ? (
          <div role="status" className="space-y-3 text-center py-6">
            <div className="inline-flex items-center gap-2 text-sm text-slate-200">
              <LoaderCircle size={16} className="animate-spin text-emerald-400" />
              {ownershipPendingText(pending.stage)}
            </div>
            <p className="text-xs text-slate-500">경과 시간 약 {Math.max(0, Math.round((pending.elapsedMs ?? 0) / 1000))}초</p>
            {(pending.elapsedMs ?? 0) > OWNERSHIP_SLOW_HINT_MS && (
              <div className="space-y-1.5">
                <p className="text-xs text-amber-300">평소보다 응답이 늦어지고 있어요.</p>
                <button type="button" onClick={checkAiStatus} className="text-xs text-slate-400 underline underline-offset-2">
                  AI 상태 확인
                </button>
                {aiStatusText && <p className="text-xs text-slate-500">{aiStatusText}</p>}
              </div>
            )}
          </div>
        ) : photoAttemptId ? (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-white">사진 속 물건이 본인 물건인가요?</h2>
            {photo && !photoError && (
              <img src={photo.url} alt="보관 중인 후보 물품의 등록 사진"
                className="mx-auto max-h-80 w-full rounded-xl bg-slate-950 object-contain"
                onLoad={() => setPhotoReady(true)} onError={() => setPhotoError(true)} />
            )}
            {photoError ? (
              <div className="flex items-center gap-3 text-sm text-rose-300">
                <p role="alert">등록 사진을 불러오지 못했습니다.</p>
                <button type="button" disabled={busy} onClick={() => setPhotoRetry((count) => count + 1)}
                  className="font-semibold text-slate-200 underline">사진 다시 불러오기</button>
              </div>
            ) : !photoReady && <p role="status" className="text-sm text-slate-400">사진을 불러오는 중입니다.</p>}
          </div>
        ) : question ? (
          <fieldset disabled={busy} key={question.id} className="space-y-2">
            <legend className="text-sm font-semibold text-white mb-3">{question.text}</legend>
            {question.options.map((option) => (
              <label key={option.id} className="flex items-center gap-3 rounded-xl bg-slate-900 border border-slate-700 p-3 text-sm text-slate-200">
                <input
                  type="radio" name="ownership-answer" value={option.id}
                  checked={optionId === option.id} onChange={() => setOptionId(option.id)}
                  className="accent-emerald-500"
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        ) : (
          <div>
            <label htmlFor="lost-description" className="block text-sm font-semibold text-slate-300 mb-2">분실물 설명</label>
            <textarea
              id="lost-description" required maxLength={1000} rows={4} disabled={busy}
              value={description} onChange={(event) => setDescription(event.target.value)}
              placeholder="예: 도서관 근처에서 남색 필통을 잃어버렸어요."
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-emerald-500"
            />
          </div>
        )}

        {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}

        {!verified && !pending && (
          <button
            type="submit" disabled={busy || (photoAttemptId ? !photoReady || photoError : question ? !optionId : !description.trim())}
            className="w-full py-3 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white font-bold rounded-xl text-sm shadow-lg transition-all"
          >
            {busy ? '처리 중...' : photoAttemptId ? '맞아요 · OTP 받기' : question ? '답변 제출' : '분실물 검색'}
          </button>
        )}
        {photoAttemptId && !pending && (
          <button type="button" disabled={busy} onClick={(event) => submit(event, false)}
            className="w-full rounded-xl bg-slate-700 py-3 text-sm font-bold text-white hover:bg-slate-600 disabled:opacity-60">
            아니에요 · 종료
          </button>
        )}
        {(attempt || pending) && (
          <button type="button" onClick={restart} className="text-sm text-slate-300 hover:text-white">
            검색 다시 시작
          </button>
        )}
      </form>
    </div>
  );
}

function WebOwnerSuccess({ result, onGoKiosk }) {
  return (
    <div className="space-y-6 text-center py-6">
      <div className="inline-flex p-3 bg-emerald-500/20 text-emerald-400 rounded-full mb-1">
        <CircleCheck size={30} />
      </div>
      <div>
        <h2 className="text-2xl font-bold text-white">일치하는 분실물을 찾았습니다!</h2>
        <p className="text-xs text-slate-400 mt-1">인증이 완료되었습니다. 아래 인증 수단으로 키오스크에서 보관함을 열 수 있습니다.</p>
      </div>
      <div className="max-w-md mx-auto bg-slate-900 border border-emerald-500/40 rounded-2xl p-6 shadow-xl space-y-4">
        <div className="text-xs text-slate-400 border-b border-slate-800 pb-3 flex justify-between items-center">
          <span>
            보관소: <strong className="text-slate-200">{result.kioskName || '-'}</strong>
          </span>
          {result.expiresAt && (
            <span className="text-emerald-400 font-bold">~{new Date(result.expiresAt).toLocaleString('ko-KR')}</span>
          )}
        </div>
        <div>
          <span className="text-xs text-slate-500 block mb-1">수령용 OTP (8자리)</span>
          <div className="text-3xl font-mono font-bold tracking-[0.3em] text-emerald-400 bg-slate-950 py-3 pl-5 pr-4 rounded-lg border border-slate-800 inline-block">
            {result.code}
          </div>
        </div>
        <p className="text-[11px] text-slate-400 flex items-start justify-center gap-1">
          <TriangleAlert size={12} className="text-amber-400 shrink-0 mt-px" /> OTP는 다시 조회할 수 없습니다. 현장 키오스크에서 [분실물 수령]을 누른 후 8자리 OTP를 입력하세요.
        </p>
      </div>
      <button
        onClick={onGoKiosk}
        className="px-6 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded-xl transition-all shadow-lg inline-flex items-center gap-1"
      >
        키오스크로 이동 <ArrowRight size={12} />
      </button>
    </div>
  );
}

function WebOwnerNotice({ kind, onRetry, onHome }) {
  const messages = {
    not_found: ['찾고 있는 분실물이 없습니다', '설명과 일치하는 보관 물품을 찾지 못했습니다.'],
    not_confirmed: ['소유 확인에 실패했습니다', '입력한 정보와 답변으로 수령할 물품을 확인하지 못했습니다.'],
  };
  const [title, description] = messages[kind];
  return (
    <div className="space-y-6 text-center py-10">
      <div
        className="inline-flex p-3 rounded-full mb-1 bg-amber-500/20 text-amber-400"
      >
        <Info size={36} />
      </div>
      <div>
        <h2 className="text-2xl font-bold text-white">{title}</h2>
        <p className="text-xs text-slate-400 mt-2 max-w-sm mx-auto leading-relaxed">
          {description}
        </p>
      </div>
      <div className="pt-4 flex justify-center gap-3">
        <button onClick={onRetry} className="px-5 py-2.5 bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold rounded-xl transition-all">
          정보 다시 입력하기
        </button>
        <button onClick={onHome} className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-xl transition-all">
          메인으로 돌아가기
        </button>
      </div>
    </div>
  );
}

/* ============================================================
 * 웹 포털 컨테이너
 * ============================================================ */
// 장치 상태 보기(관리자): GET /api/kiosks/{id}/device-status, GET /api/alerts 를 보여주는 간단한 표.
// 프론트 자체 판정 없이 백엔드가 집계한 상태를 그대로 보여준다.
function WebDeviceStatus({ kiosks, kiosksError, onBack }) {
  const [kioskId, setKioskId] = useState('');
  const [rows, setRows] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!kioskId && kiosks.length > 0) setKioskId(kiosks[0].id);
  }, [kiosks, kioskId]);

  const load = useCallback(async () => {
    if (!kioskId) return;
    setLoading(true);
    setError('');
    try {
      const [status, alertList] = await Promise.all([
        api(`/kiosks/${kioskId}/device-status`),
        api(`/alerts?kiosk_id=${kioskId}`),
      ]);
      setRows(Array.isArray(status) ? status : []);
      setAlerts(Array.isArray(alertList) ? alertList.slice(0, 10) : []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [kioskId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-5">
      <BackHeader onBack={onBack} title="장치 상태" desc="키오스크별 보관함 센서 상태와 최근 경고를 확인합니다." />
      {kiosksError && <p className="text-xs text-rose-300">{kiosksError}</p>}
      <select
        value={kioskId}
        onChange={(e) => setKioskId(e.target.value)}
        className="bg-slate-900 border border-slate-700 rounded-md text-sm text-slate-200 px-3 py-2 focus:outline-none"
      >
        {kiosks.map((k) => (
          <option key={k.id} value={k.id}>{k.name}</option>
        ))}
      </select>
      {error && <p className="text-xs text-rose-300">{error}</p>}
      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full text-xs text-left text-slate-300">
          <thead className="text-slate-500 uppercase text-[10px] bg-slate-900/60">
            <tr>
              <th className="py-2 px-3">장치</th>
              <th className="py-2 px-3">연결</th>
              <th className="py-2 px-3">문</th>
              <th className="py-2 px-3">무게(g)</th>
              <th className="py-2 px-3">물건감지</th>
              <th className="py-2 px-3">갱신시각</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-slate-800">
                <td className="py-2 px-3">{r.device === 'capture' ? '촬영 장치' : r.locker_number === 0 ? '보관함 컨트롤러' : `${r.locker_number}번 보관함`}</td>
                <td className="py-2 px-3">
                  <span className={`inline-flex items-center gap-1.5 ${r.connection === 'online' ? 'text-emerald-400' : 'text-rose-400'}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${r.connection === 'online' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                    {r.connection === 'online' ? '온라인' : '오프라인'}
                  </span>
                </td>
                <td className="py-2 px-3">{r.door === 'open' ? '열림' : r.door === 'closed' ? '닫힘' : '-'}</td>
                <td className="py-2 px-3">{r.weight_g ?? '-'}</td>
                <td className="py-2 px-3">{r.item ? '감지됨' : '-'}</td>
                <td className="py-2 px-3 text-slate-500">{r.updated_at ? new Date(r.updated_at).toLocaleTimeString('ko-KR') : '-'}</td>
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr><td colSpan={6} className="py-3 px-3 text-center text-slate-500">표시할 장치 상태가 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="space-y-1.5">
        <h3 className="text-xs font-semibold text-slate-400">최근 경고</h3>
        {alerts.length === 0 ? (
          <p className="text-xs text-slate-500">최근 경고가 없습니다.</p>
        ) : (
          alerts.map((a, i) => {
            const cls = a.severity === 'alarm'
              ? 'bg-rose-950/50 border-rose-500/50 text-rose-300'
              : 'bg-amber-950/40 border-amber-500/40 text-amber-300';
            return (
              <div key={a.id ?? i} className={`flex items-center justify-between gap-2 border rounded-lg px-3 py-2 text-xs ${cls}`}>
                <span className="flex items-center gap-1.5">
                  <TriangleAlert size={13} /> {ALERT_LABELS[a.code] || a.message || a.code || '알 수 없는 경고'}
                  {a.locker_number ? ` · ${a.locker_number}번` : ''}
                </span>
                <span className="text-[10px] text-slate-500 shrink-0">
                  {a.reported_at ? new Date(a.reported_at).toLocaleTimeString('ko-KR') : ''}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function WebPortal({ kiosks, kiosksError, onReload, onGoKiosk }) {
  const [step, setStep] = useState('type');
  const [ownerResult, setOwnerResult] = useState(null);

  const handleOwnerResult = (result) => {
    if (result?.status === 'success') {
      setOwnerResult(result);
      setStep('success');
    } else {
      setStep(result.status);
    }
  };

  return (
    <div className="w-full bg-slate-800/50 border border-slate-700/60 rounded-3xl p-4 sm:p-8 flex-1 flex flex-col justify-between shadow-2xl backdrop-blur">
      {step === 'type' && (
        <div className="space-y-6 my-auto text-center py-8">
          <div>
            <span className="px-3 py-1 bg-indigo-500/10 text-indigo-400 rounded-full text-xs font-semibold uppercase tracking-wider border border-indigo-500/20">
              서비스 선택
            </span>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mt-3">어떤 서비스를 이용하시나요?</h2>
            <p className="text-slate-400 text-sm mt-1">습득물 등록 및 수령 신청을 위해 역할을 선택해 주세요.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl mx-auto pt-4">
            <button
              onClick={() => setStep('finder')}
              className="group p-6 bg-slate-800 hover:bg-slate-700/80 border border-slate-700 hover:border-indigo-500 rounded-2xl transition-all duration-300 text-left flex flex-col justify-between shadow-lg hover:shadow-indigo-500/10"
            >
              <div className="w-12 h-12 bg-indigo-500/20 text-indigo-400 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                <HandHeart size={24} />
              </div>
              <div>
                <span className="text-xs text-indigo-400 font-semibold">습득자 (Finder)</span>
                <h3 className="text-xl font-bold text-white mt-0.5">분실물을 습득했어요</h3>
                <p className="text-xs text-slate-400 mt-2">주변 키오스크 보관함을 조회하고 물건을 안전하게 등록합니다.</p>
              </div>
              <div className="mt-4 flex items-center gap-1 text-xs font-semibold text-indigo-400 group-hover:translate-x-1 transition-transform">
                보관함 위치 조회하기 <ArrowRight size={12} />
              </div>
            </button>
            <button
              onClick={() => setStep('owner')}
              className="group p-6 bg-slate-800 hover:bg-slate-700/80 border border-slate-700 hover:border-emerald-500 rounded-2xl transition-all duration-300 text-left flex flex-col justify-between shadow-lg hover:shadow-emerald-500/10"
            >
              <div className="w-12 h-12 bg-emerald-500/20 text-emerald-400 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                <MapPinned size={24} />
              </div>
              <div>
                <span className="text-xs text-emerald-400 font-semibold">분실자 (Owner)</span>
                <h3 className="text-xl font-bold text-white mt-0.5">물건을 잃어버렸어요</h3>
                <p className="text-xs text-slate-400 mt-2">찾고 있는 물건 정보를 입력하고 본인 확인 후 수령용 OTP 8자리를 받습니다.</p>
              </div>
              <div className="mt-4 flex items-center gap-1 text-xs font-semibold text-emerald-400 group-hover:translate-x-1 transition-transform">
                분실물 찾기 신청하기 <ArrowRight size={12} />
              </div>
            </button>
          </div>
          <button
            onClick={() => setStep('devices')}
            className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 underline underline-offset-2"
          >
            <Monitor size={13} /> 장치 상태 보기 (관리자)
          </button>
        </div>
      )}

      {step === 'devices' && (
        <WebDeviceStatus kiosks={kiosks} kiosksError={kiosksError} onBack={() => setStep('type')} />
      )}

      {step === 'finder' && (
        <WebFinder kiosks={kiosks} kiosksError={kiosksError} onReload={onReload} onBack={() => setStep('type')} onGoKiosk={onGoKiosk} />
      )}
      {step === 'owner' && (
        <WebOwner onBack={() => setStep('type')} onResult={handleOwnerResult} />
      )}
      {step === 'success' && ownerResult && <WebOwnerSuccess result={ownerResult} onGoKiosk={onGoKiosk} />}
      {['not_found', 'not_confirmed'].includes(step) && (
        <WebOwnerNotice kind={step} onRetry={() => setStep('owner')} onHome={() => setStep('type')} />
      )}
    </div>
  );
}

/* ============================================================
 * 현장 키오스크
 * ------------------------------------------------------------
 * 등록: POST /registrations → shots/1 → shots/2 → analysis
 *       → deposit/open → deposit/complete
 * 수령: POST /pickup/open → /pickup/{found_item_id}/complete
 * ============================================================ */
function StepBackBar({ onBack, disabled }) {
  return (
    <button
      onClick={onBack}
      disabled={disabled}
      className="inline-flex items-center gap-1 text-xs font-semibold text-slate-200 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded-lg px-3.5 py-2.5 transition-colors shrink-0"
    >
      <ArrowLeft size={13} /> 뒤로가기
    </button>
  );
}

function ShotBox({ label, imageUrl, scanning }) {
  return (
    <div className="w-40 h-40 bg-slate-900 border-2 border-dashed border-indigo-500/60 rounded-2xl flex flex-col items-center justify-center relative overflow-hidden shadow-inner">
      {imageUrl ? (
        <img src={imageUrl} alt={label} className="absolute inset-0 w-full h-full object-cover" />
      ) : (
        <>
          <Camera size={36} className="text-indigo-400/80 mb-2" />
          <span className="text-[11px] text-indigo-300 font-medium">{label}</span>
        </>
      )}
      {scanning && <div className="scanner-laser absolute left-0 right-0 h-1" />}
    </div>
  );
}

function KioskView({ kiosks, kiosksError, categories, categoriesError, onReload }) {
  const [kioskId, setKioskId] = useState('');
  const [step, setStep] = useState('main');
  const [busy, setBusy] = useState('');
  const [failMessage, setFailMessage] = useState('');
  const [inlineError, setInlineError] = useState('');
  const [now, setNow] = useState(() => new Date());

  // 등록 상태
  const [categoryLabel, setCategoryLabel] = useState(''); // 고정 카테고리 선택지(CATEGORY_OPTIONS)
  const [otherText, setOtherText] = useState(''); // '기타' 선택 시 자연어 입력
  const [registration, setRegistration] = useState(null);
  const [previews, setPreviews] = useState({});
  const [shot2GuideOpen, setShot2GuideOpen] = useState(false); // 1차 촬영 후 방향 안내 팝업
  const [retakeOrder, setRetakeOrder] = useState(null); // 재촬영 대상 사진 번호(1|2) | null
  const [depositBlocked, setDepositBlocked] = useState(false); // 보관 완료 409 ITEM_NOT_DETECTED
  const analysisRequestRef = useRef(null);

  useEffect(() => () => analysisRequestRef.current?.abort(), []);

  // 수령 상태
  const [code, setCode] = useState('');
  const [pickup, setPickup] = useState(null);
  const [pickupReady, setPickupReady] = useState(false); // 센서가 개방→폐쇄를 감지하면 true (수령 완료 버튼 활성화)
  const [pickupBlocked, setPickupBlocked] = useState(false); // ITEM_STILL_INSIDE(409) 또는 경고 목록의 ITEM_NOT_TAKEN

  // 장치 경고·상태
  const [alerts, setAlerts] = useState([]);
  const alertsSinceRef = useRef(null);
  const pickupOpenedAtRef = useRef(null); // 보관함을 연 시각(ISO). 그 이후의 ITEM_NOT_TAKEN 경고만 본다
  const pickupDoorSeenOpenRef = useRef(false); // 수령 중 센서가 '열림'을 감지했는지(개방→폐쇄 판정용)
  const depositOpenedAtRef = useRef(null); // 보관함을 연 시각(ISO). 그 이후의 ITEM_NOT_DEPOSITED 경고만 본다
  const [deviceStatus, setDeviceStatus] = useState([]); // GET /kiosks/{id}/device-status (장치 온라인/오프라인)

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!kioskId && kiosks.length > 0) setKioskId(kiosks[0].id);
  }, [kiosks, kioskId]);

  const kiosk = kiosks.find((k) => k.id === kioskId);

  // 장치 경고 폴링: GET /api/alerts?kiosk_id=&since= (backend recent_alerts).
  // 응답 필드: { id, kiosk_id, code, severity, device, locker_number, message, detail, reported_at }.
  // 키오스크를 바꾸면 이전 키오스크의 경고·since 기준을 비우고 새로 시작한다.
  useEffect(() => {
    setAlerts([]);
    alertsSinceRef.current = null;
    if (!kioskId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const since = alertsSinceRef.current;
        const q = since ? `&since=${encodeURIComponent(since)}` : '';
        const data = await api(`/alerts?kiosk_id=${kioskId}${q}`);
        const list = Array.isArray(data) ? data : [];
        if (cancelled || list.length === 0) return;
        const latest = list.map((a) => a.reported_at).filter(Boolean).sort().pop();
        if (latest) alertsSinceRef.current = latest;
        setAlerts((prev) => {
          const seen = new Set(prev.map((a) => a.id));
          return [...list.filter((a) => !seen.has(a.id)), ...prev].slice(0, 5);
        });
      } catch {
        // 경고 조회 실패는 화면 흐름을 막지 않는다.
      }
    };
    poll();
    const t = setInterval(poll, 8000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [kioskId]);

  // 수령 - 보관함 열림 화면에서, 이미 폴링 중인 경고 목록에 이 보관함의 ITEM_NOT_TAKEN이 있으면
  // 수령 완료 확인 대신 안내를 띄운다. 보관함을 연 시각 이후의 경고만 본다.
  useEffect(() => {
    if (!(step === 'pickup-open' && pickup?.locker_number && !pickup?.dev)) return;
    const hit = alerts.some((a) => a.code === 'ITEM_NOT_TAKEN' && a.locker_number === pickup.locker_number
      && (!pickupOpenedAtRef.current || a.reported_at >= pickupOpenedAtRef.current));
    if (hit) {
      setPickupBlocked(true);
      setPickupReady(false);
    }
  }, [alerts, step, pickup?.locker_number, pickup?.dev]);

  // 수령 - 보관함 열림 화면에서, 장치 상태(door)가 개방→폐쇄로 바뀐 것을 감지하면
  // (남은 물건 경고가 없는 한) '수령 완료' 버튼을 활성화한다. 체크박스 자가 신고 대신 센서 판정을 따른다.
  useEffect(() => {
    if (!(step === 'pickup-open' && pickup?.locker_number && !pickup?.dev)) return;
    const row = deviceStatus.find((d) => d.locker_number === pickup.locker_number);
    if (!row?.door) return;
    if (pickupOpenedAtRef.current && row.updated_at && row.updated_at < pickupOpenedAtRef.current) return;
    if (row.door === 'open') pickupDoorSeenOpenRef.current = true;
    if (row.door === 'closed' && pickupDoorSeenOpenRef.current && !pickupBlocked) setPickupReady(true);
  }, [deviceStatus, step, pickup?.locker_number, pickup?.dev, pickupBlocked]);

  // 등록 - 보관함 개방 화면에서, 경고 목록에 이 보관함의 ITEM_NOT_DEPOSITED가 있으면 투입 안내를 띄운다.
  useEffect(() => {
    if (!(step === 'reg-deposit' && registration?.locker_number)) return;
    const hit = alerts.some((a) => a.code === 'ITEM_NOT_DEPOSITED' && a.locker_number === registration.locker_number
      && (!depositOpenedAtRef.current || a.reported_at >= depositOpenedAtRef.current));
    if (hit) setDepositBlocked(true);
  }, [alerts, step, registration?.locker_number]);

  // 장치 온라인/오프라인 폴링: GET /api/kiosks/{kiosk_id}/device-status (backend kiosk_device_states).
  // 응답 필드: { device, locker_number, connection, door, weight_g, item, reported_at, updated_at }.
  // locker_number 0은 장치(컨트롤러) 자체를 가리킨다.
  useEffect(() => {
    if (!kioskId) {
      setDeviceStatus([]);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await api(`/kiosks/${kioskId}/device-status`);
        if (!cancelled) setDeviceStatus(Array.isArray(data) ? data : []);
      } catch {
        // 장치 상태 조회 실패는 화면 흐름을 막지 않는다.
      }
    };
    poll();
    const t = setInterval(poll, 10000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [kioskId]);

  const reset = () => {
    analysisRequestRef.current?.abort();
    analysisRequestRef.current = null;
    setStep('main');
    setBusy('');
    setFailMessage('');
    setInlineError('');
    setCategoryLabel('');
    setOtherText('');
    setRegistration(null);
    setPreviews({});
    setShot2GuideOpen(false);
    setRetakeOrder(null);
    setDepositBlocked(false);
    setCode('');
    setPickup(null);
    setPickupReady(false);
    pickupDoorSeenOpenRef.current = false;
    setPickupBlocked(false);
  };

  // 요청 실행 래퍼: 진행 중 표시, 실패 시 실패 화면 또는 화면 내 오류 표시
  const run = useCallback(async (message, fn, { inline = false, mapError } = {}) => {
    setBusy(message);
    setInlineError('');
    try {
      await fn();
    } catch (err) {
      const text = mapError ? mapError(err) : errorText(err);
      if (inline) setInlineError(text);
      else {
        setFailMessage(text);
        setStep('fail');
      }
    } finally {
      setBusy('');
    }
  }, []);

  const loadPreview = async (regId, order) => {
    try {
      const data = await api(`/registrations/${regId}/shots/${order}/preview`);
      setPreviews((p) => ({ ...p, [order]: data?.url }));
    } catch {
      // 미리보기 실패는 진행을 막지 않는다.
    }
  };

  // [POST /api/registrations] 등록 건 생성 후 촬영 단계로
  // 카테고리는 고정 목록(CATEGORY_OPTIONS)에서 고르며, 일치하는 백엔드 카테고리가 있으면 id를 함께 보낸다.
  // '기타'는 자연어 입력을 secondary_description으로 보낸다.
  const startRegistration = () =>
    run('등록 정보를 생성하는 중...', async () => {
      const body = {
        kiosk_id: kioskId,
        primary_category_id: matchCategoryId(categories, categoryLabel),
        secondary_description: categoryLabel === '기타' ? otherText.trim() : categoryLabel || null,
      };
      const data = await api('/registrations', { method: 'POST', body });
      setRegistration(data);
      setStep('reg-shot1');
    });

  // [POST /api/registrations/{id}/shots/{1|2}] 촬영
  // 카메라 전원 꺼짐 등 장치 자체 오류는 500으로 내려오며, 이 경우 안내 후 등록을 종료한다.
  // 참고: 현재 백엔드는 같은 사진 번호의 재요청에 기존 저장 결과를 반환하므로(docs/api.md),
  // 재촬영이 실제로 새 사진을 저장하려면 백엔드의 재촬영 지원이 먼저 필요하다.
  const capture = (order) =>
    run(
      `${order}차 촬영 중입니다. 물건을 움직이지 마세요...`,
      async () => {
        const data = await api(`/registrations/${registration.id}/shots/${order}`, { method: 'POST' });
        setRegistration(data);
        await loadPreview(registration.id, order);
        if (retakeOrder === order) {
          setRetakeOrder(null);
          setStep('reg-review');
        } else if (order === 1) {
          setShot2GuideOpen(true);
          setStep('reg-shot2');
        } else {
          setStep('reg-review');
        }
      },
      { mapError: (err) => (err.status === 500 ? CAMERA_HARDWARE_ERROR_MESSAGE : errorText(err)) },
    );

  // 재촬영: 확인 화면에서 특정 사진만 다시 촬영하도록 해당 촬영 단계로 되돌아간다.
  const retakeShot = (order) => {
    setRetakeOrder(order);
    setInlineError('');
    setStep(order === 1 ? 'reg-shot1' : 'reg-shot2');
  };

  // [POST analysis] → poll [GET registration] → [POST deposit/open]
  const analyzeAndOpen = async () => {
    if (analysisRequestRef.current || busy) return;
    const controller = new AbortController();
    analysisRequestRef.current = controller;
    const { signal } = controller;
    setBusy('사진 분석 상태를 확인하는 중...');
    setInlineError('');
    try {
      const reg = await analyzeAndOpenRegistration(registration.id, {
        signal,
        onProgress: (progress) => {
          setRegistration(progress);
          setBusy(analysisPendingText(progress));
        },
        onOpening: () => setBusy('사진 분석이 완료되어 빈 보관함을 여는 중...'),
      });
      signal.throwIfAborted();
      setRegistration(reg);
      setDepositBlocked(false);
      depositOpenedAtRef.current = new Date().toISOString();
      setStep('reg-deposit');
    } catch (err) {
      // Keep the photos and registration so retry can resume a still-running job.
      if (!signal.aborted) setInlineError(errorText(err));
    } finally {
      if (analysisRequestRef.current === controller) {
        if (!signal.aborted) setBusy('');
        analysisRequestRef.current = null;
        controller.abort();
      }
    }
  };

  // [POST deposit/complete] 409 ITEM_NOT_DETECTED면 다시 열기(deposit/reopen)로 유도한다.
  const completeDeposit = () =>
    run(
      '보관함 문 상태를 확인하는 중...',
      async () => {
        try {
          const data = await api(`/registrations/${registration.id}/deposit/complete`, {
            method: 'POST',
            body: { confirmed: true },
          });
          setRegistration(data);
          setDepositBlocked(false);
          setStep('reg-done');
        } catch (err) {
          if (err.code === 'ITEM_NOT_DETECTED') setDepositBlocked(true);
          throw err;
        }
      },
      { inline: true },
    );

  // [POST deposit/reopen] 물건 미감지로 막혔을 때 보관함을 다시 연다.
  const reopenDeposit = () =>
    run(
      '보관함을 다시 여는 중...',
      async () => {
        const data = await api(`/registrations/${registration.id}/deposit/reopen`, { method: 'POST' });
        setRegistration(data);
        setDepositBlocked(false);
        depositOpenedAtRef.current = new Date().toISOString();
      },
      { inline: true },
    );

  // [POST /api/pickup/open]
  const openPickup = () => {
    const normalized = normalizeOtp(code);
    if (!OTP_PATTERN.test(normalized)) {
      setInlineError(`OTP ${OTP_LENGTH}자리를 입력해 주세요.`);
      return;
    }
    if (DEV_OTP && normalized === DEV_OTP) {
      setInlineError('');
      setPickup({ found_item_id: null, kiosk_id: kioskId, locker_number: 0, dev: true });
      setPickupBlocked(false);
      pickupDoorSeenOpenRef.current = false;
      setPickupReady(true); // 개발자 테스트 OTP는 센서 연동이 없어 즉시 활성화한다.
      pickupOpenedAtRef.current = new Date().toISOString();
      setStep('pickup-open');
      return;
    }
    run(
      'OTP를 확인하고 보관함을 여는 중...',
      async () => {
        const data = await api('/pickup/open', { method: 'POST', body: { code: normalized, kiosk_id: kioskId } });
        setPickup(data);
        setPickupBlocked(false);
        setPickupReady(false);
        pickupDoorSeenOpenRef.current = false;
        pickupOpenedAtRef.current = new Date().toISOString();
        setStep('pickup-open');
      },
      { inline: true },
    );
  };

  // [POST /api/pickup/{found_item_id}/complete] 409 ITEM_STILL_INSIDE면 다시 열기(pickup/reopen)로 유도한다.
  const completePickup = () => {
    if (pickup?.dev) {
      setStep('pickup-done');
      return;
    }
    run(
      '보관함 문 상태를 확인하는 중...',
      async () => {
        try {
          const data = await api(`/pickup/${pickup.found_item_id}/complete`, { method: 'POST', body: { confirmed: true } });
          setPickup(data);
          setPickupBlocked(false);
          setStep('pickup-done');
        } catch (err) {
          if (err.code === 'ITEM_STILL_INSIDE') setPickupBlocked(true);
          throw err;
        }
      },
      {
        inline: true,
        // 남아 있는 물건을 알릴 때는 어느 칸인지 같이 말해 주는 편이 찾기 쉽다.
        mapError: (err) =>
          err?.code === 'ITEM_STILL_INSIDE'
            ? `${pickup.locker_number}번 보관함에 물건이 남아 있어요. 다시 열어 꺼내 주세요. (ITEM_STILL_INSIDE)`
            : errorText(err),
      },
    );
  };

  // [POST /api/pickup/{found_item_id}/reopen] 물건이 남아 있을 때 보관함을 다시 연다.
  const reopenPickup = () => {
    if (pickup?.dev || !pickup?.found_item_id) {
      setPickupBlocked(false);
      return;
    }
    run(
      '보관함을 다시 여는 중...',
      async () => {
        const data = await api(`/pickup/${pickup.found_item_id}/reopen`, { method: 'POST' });
        setPickup(data);
        setPickupBlocked(false);
        pickupOpenedAtRef.current = new Date().toISOString();
      },
      {
        inline: true,
        // 남아 있는 물건을 알릴 때는 어느 칸인지 같이 말해 주는 편이 찾기 쉽다.
        mapError: (err) =>
          err?.code === 'ITEM_STILL_INSIDE'
            ? `${pickup.locker_number}번 보관함에 물건이 남아 있어요. 다시 열어 꺼내 주세요. (ITEM_STILL_INSIDE)`
            : errorText(err),
      },
    );
  };

  const disabled = !!busy;
  const noKiosk = !kioskId;

  // 뒤로가기: 재촬영 중에는 확인(reg-review) 화면으로 돌아간다.
  const backTargets = {
    'reg-info': 'main',
    'reg-shot1': retakeOrder === 1 ? 'reg-review' : 'reg-info',
    'reg-shot2': retakeOrder === 2 ? 'reg-review' : 'reg-shot1',
    'reg-review': 'reg-shot2',
    'pickup-scan': 'main',
  };
  const goBack = () => {
    if (busy) return;
    const target = backTargets[step];
    if (!target) return;
    if (target !== 'reg-review') setRetakeOrder(null);
    setInlineError('');
    setStep(target);
  };

  return (
    <div className="w-full max-w-xl mx-auto kiosk-frame bg-slate-950 p-6 flex-1 flex flex-col justify-between text-slate-100 min-h-[580px] my-auto relative">
      {/* Kiosk Header */}
      <div className="flex justify-between items-center border-b border-slate-800 pb-4 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-3 h-3 rounded-full shrink-0 ${noKiosk ? 'bg-rose-500' : 'bg-emerald-500 animate-pulse'}`} />
          {kiosks.length > 1 && step === 'main' ? (
            <select
              value={kioskId}
              onChange={(e) => setKioskId(e.target.value)}
              className="bg-slate-900 border border-slate-700 rounded-md text-sm font-bold text-slate-300 px-3 py-2 focus:outline-none"
            >
              {kiosks.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-xs font-bold tracking-wider text-slate-300 truncate">{kiosk?.name || '키오스크 미연결'}</span>
          )}
        </div>
        <span className="text-[11px] text-slate-500 font-mono whitespace-nowrap">
          {now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      {/* 장치 온라인/오프라인 (GET /api/kiosks/{kiosk_id}/device-status) */}
      {deviceStatus.length > 0 && (
        <div className="flex flex-wrap gap-3 pt-2 text-[11px] text-slate-400">
          {deviceStatus.filter((d) => d.locker_number === 0).map((d) => (
            <span key={d.device} className="inline-flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${d.connection === 'online' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              {d.device === 'capture' ? '촬영 장치' : '보관함 컨트롤러'} {d.connection === 'online' ? '온라인' : '오프라인'}
            </span>
          ))}
        </div>
      )}

      {/* 장치 경고 (GET /api/alerts) */}
      {alerts.length > 0 && (
        <div className="space-y-1.5 pt-3">
          {alerts.map((a, i) => {
            const cls = a.severity === 'alarm'
              ? 'bg-rose-950/50 border-rose-500/50 text-rose-300'
              : 'bg-amber-950/40 border-amber-500/40 text-amber-300';
            return (
              <div key={a.id ?? i} className={`flex items-center justify-between gap-2 border rounded-lg px-3 py-2 text-xs ${cls}`}>
                <span className="flex items-center gap-1.5">
                  <TriangleAlert size={13} /> {ALERT_LABELS[a.code] || a.message || a.code || '알 수 없는 경고'}
                </span>
                <button onClick={() => setAlerts((prev) => prev.filter((_, j) => j !== i))} className="text-[11px] underline shrink-0">
                  확인
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 등록 중 촬영 상자에 물건이 남아 있을 때 안내 (CAPTURE_ITEM_LEFT) */}
      {['reg-info', 'reg-shot1', 'reg-shot2', 'reg-review'].includes(step) && alerts.some((a) => a.code === 'CAPTURE_ITEM_LEFT') && (
        <div className="mt-3 flex items-center gap-2 bg-amber-950/40 border border-amber-500/40 rounded-lg px-3 py-2 text-xs text-amber-300">
          <TriangleAlert size={13} /> 촬영 상자에 물건이 남아 있어요.
        </div>
      )}

      {/* MAIN */}
      {step === 'main' && (
        <div className="my-auto space-y-6 text-center py-6">
          <div>
            <h2 className="text-2xl font-extrabold text-white">무엇을 도와드릴까요?</h2>
            <p className="text-xs text-slate-400 mt-1">원하시는 작업의 버튼을 터치해 주세요.</p>
          </div>
          {noKiosk && (
            <div className="text-xs text-rose-300 bg-rose-950/40 border border-rose-500/40 rounded-xl p-3 space-y-1">
              <p>{kiosksError || '서버에 등록된 키오스크가 없어 이용할 수 없습니다.'}</p>
              <button onClick={onReload} className="underline inline-flex items-center gap-1">
                <RefreshCw size={12} /> 다시 불러오기
              </button>
            </div>
          )}
          <div className="grid grid-cols-2 gap-4 pt-2">
            <button
              disabled={noKiosk}
              onClick={() => setStep('reg-info')}
              className="p-6 bg-slate-900 hover:bg-slate-800 disabled:opacity-40 border-2 border-indigo-500/50 hover:border-indigo-400 rounded-2xl flex flex-col items-center gap-3 transition-all active:scale-95 shadow-lg"
            >
              <div className="w-16 h-16 bg-indigo-600/20 text-indigo-400 rounded-2xl flex items-center justify-center">
                <Archive size={32} />
              </div>
              <span className="font-bold text-base text-white">분실물 등록</span>
              <span className="text-[10px] text-slate-400">습득한 물건 보관하기</span>
            </button>
            <button
              disabled={noKiosk}
              onClick={() => {
                setInlineError('');
                setStep('pickup-scan');
              }}
              className="p-6 bg-slate-900 hover:bg-slate-800 disabled:opacity-40 border-2 border-emerald-500/50 hover:border-emerald-400 rounded-2xl flex flex-col items-center gap-3 transition-all active:scale-95 shadow-lg"
            >
              <div className="w-16 h-16 bg-emerald-600/20 text-emerald-400 rounded-2xl flex items-center justify-center">
                <KeyRound size={32} />
              </div>
              <span className="font-bold text-base text-white">분실물 수령</span>
              <span className="text-[10px] text-slate-400">인증 후 보관함 열기</span>
            </button>
          </div>
        </div>
      )}

      {/* 등록 - 물건 종류 선택 (고정 목록 드롭다운, '기타'는 자연어 입력) */}
      {step === 'reg-info' && (
        <div className="space-y-4 my-auto">
          <div className="flex items-center justify-between gap-2">
            <StepBackBar onBack={goBack} />
            <span className="text-xs text-indigo-400 font-semibold">습득물 등록 · 1/4</span>
          </div>
          <div className="border-b border-slate-800 pb-2">
            <h3 className="font-bold text-white text-base">물건 종류 선택</h3>
            <p className="text-xs text-slate-400">목록에서 물건 종류를 선택해 주세요.</p>
          </div>
          {categoriesError && <p role="alert" className="text-xs text-rose-300">{categoriesError}</p>}
          <select
            value={categoryLabel}
            onChange={(e) => {
              setCategoryLabel(e.target.value);
              if (e.target.value !== '기타') setOtherText('');
            }}
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-3 text-sm text-white focus:outline-none focus:border-indigo-500"
          >
            <option value="">카테고리를 선택해 주세요</option>
            {CATEGORY_OPTIONS.map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
          {categoryLabel === '기타' && (
            <textarea
              value={otherText}
              maxLength={200}
              onChange={(e) => setOtherText(e.target.value)}
              placeholder="예: 보조배터리, 장난감 등 물건의 종류를 적어 주세요."
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white h-20 focus:outline-none focus:border-indigo-500"
            />
          )}
          <button
            disabled={disabled || !categoryLabel || (categoryLabel === '기타' && !otherText.trim())}
            onClick={startRegistration}
            className="w-full py-3.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white font-bold rounded-xl text-sm shadow-lg transition-all inline-flex items-center justify-center gap-1"
          >
            다음: 물건 촬영하기 <Camera size={14} />
          </button>
        </div>
      )}

      {/* 등록 - 1차 촬영 */}
      {step === 'reg-shot1' && (
        <div className="space-y-6 text-center my-auto">
          <div className="flex items-center justify-between gap-2 text-left">
            <StepBackBar onBack={goBack} />
            <span className="text-xs text-indigo-400 font-semibold">
              {retakeOrder === 1 ? '1차 사진 재촬영' : '습득물 등록 · 2/4'}
            </span>
          </div>
          <div>
            <h3 className="text-xl font-bold text-white mt-1">1차 촬영</h3>
            <p className="text-xs text-slate-400 mt-0.5">촬영 공간에 물건을 넣은 뒤 [촬영하기]를 눌러주세요.</p>
          </div>
          <div className="flex justify-center">
            <ShotBox label="촬영 공간에 물건을 넣어주세요" scanning={!!busy} />
          </div>
          <button
            disabled={disabled}
            onClick={() => capture(1)}
            className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white rounded-xl text-xs font-semibold shadow inline-flex items-center gap-1"
          >
            <Camera size={14} /> 촬영하기
          </button>
        </div>
      )}

      {/* 등록 - 2차 촬영 */}
      {step === 'reg-shot2' && (
        <div className="space-y-6 text-center my-auto">
          <div className="flex items-center justify-between gap-2 text-left">
            <StepBackBar onBack={goBack} />
            <span className="text-xs text-indigo-400 font-semibold">
              {retakeOrder === 2 ? '2차 사진 재촬영' : '습득물 등록 · 3/4'}
            </span>
          </div>
          <div>
            <h3 className="text-xl font-bold text-white mt-1">2차 촬영</h3>
            <p className="text-xs text-amber-300 mt-1 flex items-center justify-center gap-1">
              <RotateCw size={12} /> {SHOT2_GUIDE}
            </p>
          </div>
          <div className="flex justify-center gap-4">
            <div className="space-y-1">
              <ShotBox label="1차 사진" imageUrl={previews[1]} />
              <p className="text-[10px] text-slate-500">1차 촬영 완료</p>
            </div>
            <div className="space-y-1">
              <ShotBox label="방향을 바꿔 놓아주세요" scanning={!!busy} />
              <p className="text-[10px] text-slate-500">2차 촬영 대기</p>
            </div>
          </div>
          <button
            disabled={disabled}
            onClick={() => capture(2)}
            className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white rounded-xl text-xs font-semibold shadow inline-flex items-center gap-1"
          >
            <Camera size={14} /> 방향을 바꿨어요, 촬영하기
          </button>
        </div>
      )}

      {/* 등록 - 확인 및 보관함 열기 */}
      {step === 'reg-review' && (
        <div className="space-y-6 text-center my-auto">
          <div className="flex items-center justify-between gap-2 text-left">
            <StepBackBar onBack={goBack} disabled={disabled} />
            <span className="text-xs text-indigo-400 font-semibold">습득물 등록 · 4/4</span>
          </div>
          <div>
            <h3 className="text-xl font-bold text-white mt-1">촬영 사진 확인</h3>
            <p className="text-xs text-slate-400 mt-0.5">
              사진을 확인한 뒤 촬영 공간에서 물건을 꺼내고 [보관함 열기]를 눌러주세요. 사진이 잘못 찍혔다면 [재촬영]을 눌러 다시 찍을 수 있습니다.
            </p>
          </div>
          <div className="flex justify-center gap-4">
            <div className="space-y-1.5">
              <ShotBox label="1차 사진" imageUrl={previews[1]} />
              <button
                disabled={disabled}
                onClick={() => retakeShot(1)}
                className="inline-flex items-center gap-1 text-xs text-slate-200 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded-lg px-3.5 py-2.5 disabled:opacity-40"
              >
                <RotateCw size={12} /> 재촬영
              </button>
            </div>
            <div className="space-y-1.5">
              <ShotBox label="2차 사진" imageUrl={previews[2]} />
              <button
                disabled={disabled}
                onClick={() => retakeShot(2)}
                className="inline-flex items-center gap-1 text-xs text-slate-200 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded-lg px-3.5 py-2.5 disabled:opacity-40"
              >
                <RotateCw size={12} /> 재촬영
              </button>
            </div>
          </div>
          {inlineError && <p role="alert" className="text-sm text-rose-300">{inlineError}</p>}
          <button
            disabled={disabled}
            onClick={analyzeAndOpen}
            className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white font-bold rounded-xl text-xs shadow-lg transition-all inline-flex items-center justify-center gap-1"
          >
            등록 완료 · 보관함 열기 <DoorOpen size={14} />
          </button>
        </div>
      )}

      {/* 등록 - 보관함 개방 (투입 대기) */}
      {step === 'reg-deposit' && (
        <div className="space-y-6 text-center my-auto">
          <div className="inline-flex p-4 bg-indigo-500/20 text-indigo-400 rounded-full animate-bounce">
            <LockOpen size={36} />
          </div>
          <div>
            <span className="text-xs text-indigo-400 font-bold uppercase tracking-wider">보관함 개방</span>
            <h2 className="text-3xl font-extrabold text-white mt-1">{registration?.locker_number}번 보관함이 열렸습니다</h2>
            <p className="text-xs text-slate-400 mt-2">
              {depositBlocked ? '보관함에 물건이 감지되지 않았어요. 물건을 넣고 문을 닫아 주세요.' : '물건을 보관함에 넣고 문을 닫은 뒤 아래 버튼을 눌러주세요.'}
            </p>
          </div>
          {inlineError && <p className="text-xs text-rose-300">{inlineError}</p>}
          {depositBlocked ? (
            <button
              disabled={disabled}
              onClick={reopenDeposit}
              className="px-6 py-2.5 bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-slate-950 text-xs font-bold rounded-xl shadow inline-flex items-center gap-1"
            >
              <RefreshCw size={14} /> 보관함 다시 열기
            </button>
          ) : (
            <button
              disabled={disabled}
              onClick={completeDeposit}
              className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white text-xs font-bold rounded-xl shadow"
            >
              물건을 넣고 문을 닫았습니다
            </button>
          )}
        </div>
      )}

      {/* 등록 - 완료 */}
      {step === 'reg-done' && (
        <div className="space-y-6 text-center my-auto">
          <div className="inline-flex p-4 bg-emerald-500/20 text-emerald-400 rounded-full">
            <CircleCheck size={36} />
          </div>
          <div>
            <span className="text-xs text-emerald-400 font-bold uppercase tracking-wider">보관 완료</span>
            <h2 className="text-2xl font-extrabold text-white mt-1">{registration?.locker_number}번 보관함에 보관되었습니다</h2>
            <p className="text-xs text-slate-400 mt-2">소중한 물건을 맡겨주셔서 감사합니다.</p>
          </div>
          <button
            onClick={reset}
            className="px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white text-sm font-bold rounded-xl border border-slate-700 inline-flex items-center gap-1.5"
          >
            <House size={16} /> 처음 화면으로 돌아가기
          </button>
        </div>
      )}

      {/* 수령 - OTP 8자리 입력 */}
      {step === 'pickup-scan' && (
        <div className="space-y-5 text-center my-auto">
          <div className="flex items-center justify-between gap-2 text-left">
            <StepBackBar onBack={goBack} />
            <span className="text-xs text-emerald-400 font-semibold">분실물 수령</span>
          </div>
          <div>
            <h3 className="text-xl font-bold text-white mt-1">OTP 인증</h3>
            <p className="text-xs text-slate-400 mt-0.5">웹에서 발급받은 OTP 8자리를 입력해 주세요.</p>
          </div>
          <div className="flex justify-center gap-2">
            {Array.from({ length: OTP_LENGTH }, (_, i) => (
              <div
                key={i}
                className={`w-10 h-14 rounded-lg border-2 flex items-center justify-center text-xl font-mono font-bold shadow-inner ${
                  code[i] ? 'border-emerald-500 bg-emerald-950/30 text-emerald-400' : 'border-slate-700 bg-slate-900 text-slate-600'
                }`}
              >
                {code[i] || <KeyRound size={14} />}
              </div>
            ))}
          </div>
          <div className="max-w-xs mx-auto space-y-2">
            <input
              type="text"
              autoCapitalize="characters"
              spellCheck={false}
              autoComplete="one-time-code"
              autoFocus
              value={code}
              maxLength={OTP_LENGTH}
              onChange={(e) => setCode(normalizeOtp(e.target.value).slice(0, OTP_LENGTH))}
              onKeyDown={(e) => e.key === 'Enter' && !disabled && openPickup()}
              placeholder="OTP 8자리 입력"
              className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-center text-sm font-mono tracking-widest text-emerald-400 focus:outline-none focus:border-emerald-500"
            />
            {inlineError && <p className="text-xs text-rose-300">{inlineError}</p>}
            <button
              disabled={disabled || code.length !== OTP_LENGTH}
              onClick={openPickup}
              className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white text-xs font-bold rounded-xl shadow"
            >
              인증 승인
            </button>
          </div>
        </div>
      )}

      {/* 수령 - 보관함 열림 */}
      {step === 'pickup-open' && (
        <div className="space-y-6 text-center my-auto">
          <div className="inline-flex p-4 bg-emerald-500/20 text-emerald-400 rounded-full animate-bounce">
            <LockOpen size={36} />
          </div>
          <div>
            <span className="text-xs text-emerald-400 font-bold uppercase tracking-wider">잠금 해제 완료</span>
            <h2 className="text-3xl font-extrabold text-white mt-1">{pickup?.locker_number}번 보관함이 열렸습니다</h2>
            <p className="text-xs text-slate-400 mt-2">
              {pickupBlocked ? '보관함에 물건이 남아 있어요. 물건을 꺼내 주세요.' : '물품을 꺼내신 후 보관함 문을 꼭 닫아주세요.'}
            </p>
            {pickup?.dev && (
              <p className="mt-3 inline-block text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-full px-3 py-1">
                개발자 테스트 OTP · 백엔드 미호출, 실제 보관함은 열리지 않습니다
              </p>
            )}
          </div>
          {inlineError && <p className="text-xs text-rose-300">{inlineError}</p>}
          {pickupBlocked ? (
            <button
              disabled={disabled}
              onClick={reopenPickup}
              className="px-6 py-2.5 bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-slate-950 text-xs font-bold rounded-xl shadow inline-flex items-center gap-1"
            >
              <RefreshCw size={14} /> 보관함 다시 열기
            </button>
          ) : (
            <button
              disabled={disabled || !pickupReady}
              onClick={completePickup}
              className="px-6 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-xs font-bold rounded-xl shadow"
            >
              {pickupReady ? '수령 완료' : '문 닫힘 감지 대기 중...'}
            </button>
          )}
        </div>
      )}

      {/* 수령 - 완료 */}
      {step === 'pickup-done' && (
        <div className="space-y-6 text-center my-auto">
          <div className="inline-flex p-4 bg-emerald-500/20 text-emerald-400 rounded-full">
            <CircleCheck size={36} />
          </div>
          <div>
            <span className="text-xs text-emerald-400 font-bold uppercase tracking-wider">수령 완료</span>
            <h2 className="text-2xl font-extrabold text-white mt-1">물건을 찾아가셨습니다</h2>
            <p className="text-xs text-slate-400 mt-2">이용해 주셔서 감사합니다.</p>
          </div>
          <button
            onClick={reset}
            className="px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white text-sm font-bold rounded-xl border border-slate-700 inline-flex items-center gap-1.5"
          >
            <House size={16} /> 처음 화면으로 돌아가기
          </button>
        </div>
      )}

      {/* 실패 */}
      {step === 'fail' && (
        <div className="space-y-6 text-center my-auto">
          <div className="inline-flex p-4 bg-rose-500/20 text-rose-400 rounded-full">
            <TriangleAlert size={36} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">요청을 처리하지 못했습니다</h2>
            <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">{failMessage}</p>
          </div>
          <button
            onClick={reset}
            className="px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white text-sm font-bold rounded-xl border border-slate-700 inline-flex items-center gap-1.5"
          >
            <House size={16} /> 처음으로
          </button>
        </div>
      )}

      {/* 진행 중 오버레이 */}
      {busy && (
        <div role="status" className="absolute inset-0 z-30 bg-slate-950/80 rounded-[16px] flex flex-col items-center justify-center gap-3 text-center px-6">
          <LoaderCircle size={36} className="animate-spin text-indigo-400" />
          <p className="text-sm text-slate-200 font-semibold">{busy}</p>
        </div>
      )}

      {/* 1차 촬영 후 방향 안내 팝업 */}
      {shot2GuideOpen && (
        <div className="absolute inset-0 z-40 bg-slate-950/90 rounded-[16px] flex items-center justify-center px-6">
          <div className="bg-slate-900 border border-amber-500/40 rounded-2xl p-5 max-w-xs text-center space-y-3 shadow-2xl">
            <div className="inline-flex p-3 bg-amber-500/20 text-amber-400 rounded-full">
              <RotateCw size={24} />
            </div>
            <p className="text-sm text-white font-semibold leading-relaxed">{SHOT2_GUIDE}</p>
            <button
              onClick={() => setShot2GuideOpen(false)}
              className="w-full py-2 bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold rounded-lg"
            >
              확인
            </button>
          </div>
        </div>
      )}

      {/* Kiosk Footer */}
      <div className="border-t border-slate-800 pt-3 flex justify-between items-center gap-3 text-[11px] text-slate-500">
        <button
          onClick={reset}
          disabled={disabled || step === 'reg-deposit' || step === 'pickup-open'}
          title={step === 'reg-deposit' || step === 'pickup-open' ? '보관함 문을 닫고 완료 버튼을 눌러주세요' : undefined}
          className="inline-flex items-center gap-1.5 bg-slate-800 hover:bg-rose-600 border border-slate-700 hover:border-rose-500 text-slate-200 hover:text-white font-bold text-xs px-4 py-2 rounded-xl shadow disabled:opacity-40 disabled:hover:bg-slate-800 disabled:hover:border-slate-700 transition-colors"
        >
          <House size={16} /> 처음으로
        </button>
        <span>도움말: 1588-0000</span>
      </div>
    </div>
  );
}

/* ============================================================
 * App
 * ============================================================ */
export default function App() {
  const [mode, setMode] = useState('web');
  const [kiosks, setKiosks] = useState([]);
  const [kiosksError, setKiosksError] = useState('');
  const [categories, setCategories] = useState([]);
  const [categoriesError, setCategoriesError] = useState('');
  const [webKey, setWebKey] = useState(0);

  const loadData = useCallback(async () => {
    try {
      const data = await api('/kiosks?limit=100&offset=0');
      setKiosks(Array.isArray(data) ? data : []);
      setKiosksError('');
    } catch (err) {
      setKiosks([]);
      setKiosksError(`키오스크 목록을 불러오지 못했습니다. ${errorText(err)}`);
    }
    try {
      const data = await api('/categories');
      setCategories(Array.isArray(data) ? data : []);
      setCategoriesError('');
    } catch (err) {
      setCategories([]);
      setCategoriesError(`카테고리를 불러오지 못했습니다. ${errorText(err)}`);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const switchMode = (next) => {
    if (next === 'web') setWebKey((k) => k + 1); // 웹 포털은 첫 화면부터 다시 시작
    setMode(next);
  };

  return (
    <div className="slf-app bg-slate-900 text-slate-100 min-h-screen flex flex-col items-center justify-start p-2 sm:p-6">
      <style>{GLOBAL_STYLE}</style>

      {/* Header & Mode Switcher */}
      <header className="w-full max-w-5xl mb-6 flex flex-col sm:flex-row justify-between items-center bg-slate-800/80 p-4 rounded-2xl border border-slate-700 backdrop-blur">
        <div className="flex items-center gap-3 mb-3 sm:mb-0">
          <div className="p-2.5 bg-indigo-600 rounded-xl text-white shadow-lg shadow-indigo-500/30">
            <Boxes size={20} />
          </div>
          <div>
            <h1 className="font-bold text-lg text-white">Smart Lost &amp; Found</h1>
            <p className="text-xs text-slate-400">스마트 분실물 안심 보관 시스템</p>
          </div>
        </div>
        <div className="bg-slate-950 p-1 rounded-xl flex border border-slate-800">
          <button
            onClick={() => switchMode('web')}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-all duration-200 inline-flex items-center gap-1.5 ${
              mode === 'web' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Globe size={14} /> 웹 포털 모드
          </button>
          <button
            onClick={() => switchMode('kiosk')}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-all duration-200 inline-flex items-center gap-1.5 ${
              mode === 'kiosk' ? 'bg-emerald-600 text-white shadow' : 'text-slate-400 hover:text-white'
            }`}
          >
            <Monitor size={14} /> 현장 키오스크 모드
          </button>
        </div>
      </header>

      <main className="w-full max-w-5xl flex-1 flex flex-col">
        {mode === 'web' ? (
          <WebPortal
            key={webKey}
            kiosks={kiosks}
            kiosksError={kiosksError}
            onReload={loadData}
            onGoKiosk={() => switchMode('kiosk')}
          />
        ) : (
          <KioskView kiosks={kiosks} kiosksError={kiosksError} categories={categories}
            categoriesError={categoriesError} onReload={loadData} />
        )}
      </main>
    </div>
  );
}
