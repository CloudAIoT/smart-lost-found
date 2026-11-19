# 프론트 화면 개선 추천

- 작성: 정형식, 2026-11-18
- 대상: 키오스크와 웹 포털 화면 (`frontend/src/App.jsx`)
- 상태: 추천. 적용 여부와 방법은 프론트 담당이 정한다.

공개된 UI 컴포넌트 사이트와 AI 제품용 프론트 사이트를 둘러보고, 지금 화면에 무리 없이 들어갈 것만 골랐다. 아래 코드 예시는 지금 색과 구조에 맞춰 새로 쓴 것이고, 사이트의 코드를 그대로 옮기지 않았다.

## 지금 화면

- React 19, Vite, Tailwind CSS 3.4, lucide-react 아이콘, Pretendard 글꼴을 쓴다. UI 라이브러리 없이 `App.jsx` 하나에 화면이 모여 있다.
- 어두운 slate 바탕에 주 색은 indigo, 성공은 emerald, 오류는 rose를 쓴다.
- 키오스크 화면은 촬영 상자 옆 아이패드로 띄운다(2026-11-18 회의). 손가락으로 누르는 터치 화면이다.

고른 기준은 네 가지다.

1. MIT처럼 조건 없이 쓸 수 있는 라이선스
2. Tailwind 3.4에서 그대로 되거나 클래스 몇 개만 바꾸면 되는 것
3. 추가 패키지가 없거나 하나
4. 공공장소 키오스크에서 눈에 잘 띄되 요란하지 않은 것

## 한눈에 보기

| 화면 | 추천 | 출처, 라이선스 | 들이는 방법 |
| --- | --- | --- | --- |
| 수령 OTP 8자리 입력 | input-otp | [guilhermerodz/input-otp](https://github.com/guilhermerodz/input-otp), MIT | `npm install input-otp`, 의존성 없음 |
| 웹 포털 알림 | sonner | [emilkowalski/sonner](https://github.com/emilkowalski/sonner), MIT | `npm install sonner` |
| 등록 단계 표시 | Steps (Dark) | [HyperUI](https://www.hyperui.dev/components/application/steps), MIT | 예시 3처럼 직접 작성 |
| 분석 대기 문구 | Text Shimmer | [prompt-kit](https://www.prompt-kit.com/docs/text-shimmer), MIT | CSS 몇 줄로 옮김 |
| 보관함 개방 | 칸 번호와 LED 색 맞추기 | 직접 작성 | 의존성 없음 |
| 소유 확인 5지선다 | 고른 보기 강조 | 직접 작성 | 클래스만 추가 |
| 로딩 표시 | Tailwind Loaders | [Uiverse](https://uiverse.io/ui/tailwind-loaders), MIT | 마음에 드는 것 복사 |
| 등록 완료 | 축하 효과 (선택) | [canvas-confetti](https://github.com/catdad/canvas-confetti), ISC | `npm install canvas-confetti` |

## 코드 예시

### 1. 수령 OTP 입력 (input-otp)

지금 직접 만든 8칸 입력도 잘 동작한다. input-otp로 바꾸면 붙여넣기, 깜빡이는 커서, 스크린리더 지원을 라이브러리가 맡는다. 스타일이 없는 라이브러리라 지금 클래스를 그대로 쓸 수 있다.

```jsx
import { OTPInput } from 'input-otp';

function ReturnCodeInput({ value, onChange, onComplete, disabled }) {
  return (
    <OTPInput
      maxLength={OTP_LENGTH}
      value={value}
      onChange={(next) => onChange(normalizeOtp(next))}
      onComplete={onComplete}
      pattern={/^[A-Za-z0-9]*$/}
      pasteTransformer={normalizeOtp}
      disabled={disabled}
      containerClassName="flex justify-center gap-2"
      render={({ slots }) => slots.map((slot, i) => <CodeSlot key={i} {...slot} />)}
    />
  );
}

function CodeSlot({ char, isActive, hasFakeCaret }) {
  return (
    <div className={`flex h-14 w-11 items-center justify-center rounded-xl border bg-slate-900 font-mono text-2xl font-bold text-white ${
      isActive ? 'border-emerald-400 ring-2 ring-emerald-400/40' : 'border-slate-700'}`}>
      {char ?? (hasFakeCaret && <span className="h-7 w-px bg-emerald-300 motion-safe:animate-pulse" />)}
    </div>
  );
}
```

`OTP_LENGTH`와 `normalizeOtp`는 지금 코드에 있는 것을 그대로 쓴다.

### 2. 웹 포털 알림 (sonner)

웹 포털에서 잠깐 보여 주고 사라져도 되는 알림에 쓴다. 키오스크의 보관함 개방처럼 사용자가 꼭 읽어야 하는 오류는 지금처럼 버튼 옆에 남겨 두는 편이 낫다. 알림은 몇 초 뒤 사라진다.

```jsx
import { Toaster, toast } from 'sonner';

// 웹 포털 화면 맨 아래에 한 번
<Toaster theme="dark" position="top-center" richColors />

// 오류가 났을 때
toast.error(errorText(err));
```

### 3. 등록 단계 표시

등록이 정보 입력, 촬영, 보관으로 이어진다는 것을 위쪽에 보여 준다. HyperUI의 Steps 다크 버전을 참고해 지금 색으로 다시 썼다.

```jsx
import { Check } from 'lucide-react'; // 지금 import 목록에 추가

const REG_STEPS = ['정보 입력', '촬영·확인', '보관'];

// current: reg-info 0, reg-review 1, reg-deposit 2, reg-done 3
function RegSteps({ current }) {
  return (
    <ol className="flex items-center gap-2 text-xs" aria-label="등록 단계">
      {REG_STEPS.map((label, i) => {
        const done = i < current;
        const now = i === current;
        return (
          <li key={label} className="flex flex-1 items-center gap-2" aria-current={now ? 'step' : undefined}>
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-bold ${
              done ? 'border-emerald-500 bg-emerald-500 text-slate-950'
                : now ? 'border-indigo-400 text-indigo-300'
                : 'border-slate-700 text-slate-500'}`}>
              {done ? <Check size={14} /> : i + 1}
            </span>
            <span className={now ? 'font-semibold text-white' : 'text-slate-400'}>{label}</span>
            {i < REG_STEPS.length - 1 && (
              <span className={`h-px flex-1 ${done ? 'bg-emerald-500/60' : 'bg-slate-700'}`} />
            )}
          </li>
        );
      })}
    </ol>
  );
}
```

### 4. 분석 대기 문구

지금은 돌아가는 아이콘 아래에 "사진을 분석하고 빈 보관함을 여는 중..."을 보여 준다. 글자에 빛이 지나가는 효과를 주면 기다리는 동안 멈춘 화면이 아니라는 것이 더 잘 보인다. prompt-kit의 Text Shimmer와 같은 효과를 CSS로만 만들었다.

```css
/* GLOBAL_STYLE에 추가 */
.slf-shimmer {
  background: linear-gradient(90deg, #94a3b8 25%, #f8fafc 50%, #94a3b8 75%);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: slf-shimmer 2.2s linear infinite;
}
@keyframes slf-shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
@media (prefers-reduced-motion: reduce) {
  .slf-shimmer { animation: none; color: #e2e8f0; background: none; }
  .scanner-laser { animation: none; }
}
```

```jsx
<p className="slf-shimmer text-sm font-semibold">{busy}</p>
```

백엔드가 분석 진행률을 따로 보내 주지 않으므로, 단계별 완료 표시처럼 실제와 다를 수 있는 진행 바는 넣지 않는다.

### 5. 보관함 개방: LED 색과 맞추기

시제품 보관함은 열린 칸의 LED가 깜빡인다(1번 초록, 2번 빨강, [보관함 LED·버튼](../raspberry-pi/README.md#보관함-led버튼-보관함-pi)). 화면에도 같은 색 점을 띄우면 어느 칸인지 바로 찾는다.

```jsx
const LOCKER_LED = {
  1: { name: '초록', dot: 'bg-emerald-400' },
  2: { name: '빨간', dot: 'bg-rose-500' },
};
const led = LOCKER_LED[registration?.locker_number];

<div className="flex items-center justify-center gap-3">
  {led && (
    <span className="relative flex h-4 w-4">
      <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 motion-safe:animate-ping ${led.dot}`} />
      <span className={`relative inline-flex h-4 w-4 rounded-full ${led.dot}`} />
    </span>
  )}
  <h2 className="text-3xl font-extrabold text-white">{registration?.locker_number}번 보관함이 열렸습니다</h2>
</div>
<p className="mt-2 text-sm text-slate-300">
  {led ? `${led.name} 불이 깜빡이는 칸에 ` : ''}물건을 넣고, 그 칸 앞 버튼을 누른 뒤 아래 버튼을 눌러 주세요.
</p>
```

수령 화면(`pickup-open`)에도 같은 방법을 쓸 수 있다.

### 6. 소유 확인: 고른 보기 강조

지금 보기 카드에 클래스만 더하면, 고른 보기에 테두리와 바탕색이 생기고 키보드로 옮겨 다닐 때 위치가 보인다. Tailwind 3.4의 `has-*` 변형을 쓴다.

```jsx
<label key={option.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-700 bg-slate-900 p-3 text-sm text-slate-200 hover:border-slate-500 has-[:checked]:border-emerald-500 has-[:checked]:bg-emerald-500/10 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-emerald-400">
```

### 7. 등록 완료 효과 (선택)

`reg-done`으로 넘어갈 때 한 번만 터뜨린다. 움직임을 줄이도록 설정한 사용자에게는 나오지 않는다.

```jsx
import confetti from 'canvas-confetti';

confetti({ particleCount: 80, spread: 70, origin: { y: 0.7 }, disableForReducedMotion: true });
```

## 문구와 접근성

- 키오스크는 아이패드에서 손가락으로 누르므로 버튼과 보기 카드를 넉넉하게 둔다. 애플은 누르는 영역을 최소 44×44pt로 권한다.
- 웹 포털을 키보드로 쓰는 사람도 위치를 알 수 있게 버튼에 `focus-visible:ring-2 focus-visible:ring-indigo-400`을 붙인다. 지금은 `focus-visible`이 한 군데도 없다.
- 키오스크 화면의 `text-xs` 문구가 41곳이다. 아이패드를 세워 두고 서서 읽으므로 핵심 안내는 `text-sm` 이상이 낫다.
- `animate-bounce`, `animate-pulse` 앞에 `motion-safe:`를 붙이면, 움직임을 줄이도록 설정한 사용자에게는 멈춰 보인다.

## 사이트별 확인 결과

2026-11-18에 각 사이트와 라이선스를 직접 확인했다.

| 사이트 | 라이선스 | 우리 구조에서 | 판단 |
| --- | --- | --- | --- |
| [shadcn/ui](https://ui.shadcn.com) | MIT | 새 프로젝트는 Tailwind v4 기준이고, CLI 설정(경로 별칭, 유틸 함수, CSS 변수)이 필요하다 | 통째로 들이지 않고 바탕 라이브러리(input-otp, sonner)만 쓴다. shadcn도 자체 toast 대신 sonner를 권한다 |
| [HyperUI](https://www.hyperui.dev) | MIT | 마크업 복사형, Tailwind v4 기준 | Steps, Badges, Empty content의 다크 버전을 참고하고 v3에 없는 클래스만 바꾼다 |
| [Uiverse](https://uiverse.io) | MIT | HTML/CSS, Tailwind, React로 복사 | 로딩 표시에 좋다 |
| [Flowbite](https://flowbite.com) | 코드 MIT, Pro는 별도 계약 | v4는 `text-fg-brand` 같은 자체 색 이름을 쓴다 | 쓰려면 색 클래스를 모두 바꿔야 한다 |
| [daisyUI](https://daisyui.com) | MIT | `@plugin "daisyui"`로 넣는 Tailwind v4 플러그인 | 테마 체계가 통째로 들어와 지금 색과 섞인다. 비추천 |
| [Preline](https://preline.co) | 확인 안 함 | 스테퍼가 Preline 전용 JavaScript와 함께 동작한다 | 비추천 |
| [Magic UI](https://magicui.design) | MIT | shadcn CLI로 설치하고 motion 패키지가 필요하다 | Border Beam, Number Ticker 같은 효과를 꼭 필요한 곳 한두 군데에만 |
| [Aceternity UI](https://ui.aceternity.com) | 자체 라이선스 | 완성된 서비스에는 쓸 수 있지만 컴포넌트 자체를 다시 배포하거나 판매하면 안 된다 | 디자인 참고만 |
| [21st.dev](https://21st.dev) | 컴포넌트마다 다름 | 1만 2천 개가 넘는 커뮤니티 컴포넌트를 AI 에이전트에 붙여 넣을 프롬프트로 제공한다 | 아이디어 찾기용. 가져올 때 각 라이선스를 확인한다 |
| [prompt-kit](https://www.prompt-kit.com) | MIT | AI 화면용 부품(Text Shimmer, Steps, Loader). shadcn과 Tailwind v4가 전제다 | 효과만 CSS로 옮겼다(예시 4) |
| [Vercel AI Elements](https://elements.ai-sdk.dev) | 확인 안 함 | shadcn 기반 AI 컴포넌트 | 2026년 11월 말 지원 종료 예정이라 쓰지 않는다 |
| [assistant-ui](https://www.assistant-ui.com) | 확인 안 함 | 채팅 앱용 런타임과 부품 | 채팅 화면이 없어 우리에게는 과하다 |
| [v0](https://v0.app/docs) | 서비스 | 말이나 그림으로 React와 Tailwind 화면을 만들어 준다 | 시안을 빠르게 볼 때만 쓰고, 결과 코드는 우리 색과 구조에 맞게 옮겨 쓴다 |

## 들이는 순서 제안

1. 패키지 없이 바로: 누르는 영역 크기, `focus-visible`, `motion-safe:`, 보관함 LED 색, 분석 문구, 고른 보기 강조
2. 패키지 하나씩: input-otp, sonner
3. 여유가 있으면: 등록 단계 표시, 완료 효과
