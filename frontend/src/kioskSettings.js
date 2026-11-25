import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { useKioskOperation } from './KioskOperationGuard.jsx';

export const KIOSK_STORAGE_KEY = 'smart-lost-found.kiosk_id.v1';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STORAGE_NOTICE = '이 브라우저에서는 설정을 저장할 수 없어 다음 접속 때 다시 선택해야 합니다.';

export async function loadAllKiosks({ signal } = {}) {
  const kiosks = [];
  for (let offset = 0; ; offset += 100) {
    const page = await api(`/kiosks?limit=100&offset=${offset}`, { signal });
    if (!Array.isArray(page)) throw new Error('INVALID_KIOSK_RESPONSE');
    kiosks.push(...page);
    if (page.length < 100) return kiosks;
  }
}

// 저장값은 단말 선택 편의 설정이다. 업무 ID·OTP는 저장하지 않는다.
export function useKioskSetting(kiosks, { loading, error, locked: workflowLocked }) {
  const [kioskId, setKioskId] = useState('');
  const operation = useKioskOperation(kioskId);
  const locked = workflowLocked || operation.locked;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const memoryOnly = useRef(false);

  useEffect(() => {
    const changed = (event) => {
      if (event.key === KIOSK_STORAGE_KEY || event.key === null) setRevision((value) => value + 1);
    };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, []);

  useEffect(() => {
    // 다른 탭의 변경도 업무가 끝난 뒤 전체 목록으로 재검증한다.
    if (locked || loading || error || memoryOnly.current) return;
    try {
      const saved = localStorage.getItem(KIOSK_STORAGE_KEY);
      if (saved && UUID.test(saved) && kiosks.some((kiosk) => kiosk.id === saved)) {
        setKioskId(saved);
        setDraft(saved);
        setEditing(false);
      } else {
        setKioskId('');
        setDraft('');
        setEditing(false);
        if (saved) {
          localStorage.removeItem(KIOSK_STORAGE_KEY);
          setNotice('저장된 키오스크를 사용할 수 없습니다. 다시 설정해 주세요.');
        }
      }
    } catch {
      memoryOnly.current = true;
      setNotice(STORAGE_NOTICE);
    }
  }, [kiosks, loading, error, locked, revision]);

  const confirm = () => {
    if (locked || loading || error || !kiosks.some((kiosk) => kiosk.id === draft)) return;
    try {
      localStorage.setItem(KIOSK_STORAGE_KEY, draft);
      memoryOnly.current = false;
      setNotice('');
    } catch {
      memoryOnly.current = true;
      setNotice(STORAGE_NOTICE);
    }
    setKioskId(draft);
    setEditing(false);
  };

  const clear = () => {
    if (locked) return;
    try {
      localStorage.removeItem(KIOSK_STORAGE_KEY);
    } catch {
      setNotice('저장된 설정을 삭제하지 못했습니다. 설정 초기화가 완료되지 않았습니다.');
      return;
    }
    setKioskId('');
    setDraft('');
    setEditing(false);
    setNotice('');
  };

  return { kioskId, operation, editing, draft, notice, setDraft, confirm, clear,
    change: () => { if (!locked) { setDraft(kioskId); setEditing(true); } },
    cancel: () => setEditing(false) };
}
