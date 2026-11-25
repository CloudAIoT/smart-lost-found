import { useEffect, useRef } from 'react';
import { useBlocker } from 'react-router-dom';

export default function KioskNavigationGuard({ locked }) {
  const blocker = useBlocker(locked);
  const stayButton = useRef(null);

  useEffect(() => {
    if (!locked) return;
    const beforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [locked]);

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (!locked) blocker.reset();
    else stayButton.current?.focus();
  }, [blocker, locked]);

  if (blocker.state !== 'blocked' || !locked) return null;
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/90 flex items-center justify-center p-6">
      <div role="alertdialog" aria-modal="true" aria-labelledby="kiosk-navigation-title"
        aria-describedby="kiosk-navigation-description"
        onKeyDown={(event) => {
          if (event.key === 'Escape') blocker.reset();
          if (event.key === 'Tab') { event.preventDefault(); stayButton.current?.focus(); }
        }}
        className="max-w-sm bg-slate-900 border border-amber-500/50 rounded-2xl p-6 space-y-4 text-center">
        <h2 id="kiosk-navigation-title" className="text-lg font-bold text-white">진행 중인 업무가 있습니다</h2>
        <p id="kiosk-navigation-description" className="text-sm text-slate-300">
          등록·수령을 마칠 때까지 현재 키오스크에서 계속 진행해 주세요.
          처음으로 돌아가거나 화면을 닫아도 서버의 작업은 취소되지 않습니다.
        </p>
        <button ref={stayButton} onClick={() => blocker.reset()}
          className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl py-3 font-bold">
          현재 업무 계속하기
        </button>
      </div>
    </div>
  );
}
