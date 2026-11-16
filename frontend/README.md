# Frontend (웹 포털 + 현장 키오스크)

React 19 + Vite + Tailwind CSS 3로 만든 단일 페이지 앱이다. 한 화면에서 상단 토글로 두 모드를 오간다.

- **웹 포털**: 습득자용 키오스크 위치 조회(지도), 분실자용 분실물 찾기(화면만, 아래 참고)
- **현장 키오스크**: 분실물 등록(정보 입력 → 촬영 2회 → 분석·보관함 개방 → 투입 완료), 분실물 수령(OTP 입력 → 보관함 개방 → 수령 완료)

API 계약은 [docs/api.md](../docs/api.md)를 따른다. 모든 호출은 같은 출처의 상대 경로 `/api`로 보낸다.

## 로컬 개발

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173
```

백엔드에 CORS 설정이 없으므로 개발 서버는 `vite.config.js`의 프록시로 `/api` 요청을
EC2 백엔드(`http://52.78.12.168`)에 넘긴다. 로컬 백엔드를 쓰려면 프록시의 `target`을 바꾼다.

## 배포

별도 서버가 없다. `deploy/Dockerfile`이 백엔드 이미지를 만들 때 `npm ci && npm run build`로
`dist/`를 만들어 함께 넣고, EC2의 nginx가 `/`에서 정적 파일을, `/api/`는 백엔드로 넘긴다.
프론트와 API가 같은 주소이므로 운영에서도 CORS가 필요 없다. 자세한 내용은 [deploy/README.md](../deploy/README.md).

`main`에 병합되면 기존 배포 워크플로가 백엔드와 함께 배포한다. 접속 주소: `http://52.78.12.168/`

## 현재 제한

- 분실자 검색·소유 확인(질문/답변) API가 없어 분실자 화면은 UI만 있다. `src/App.jsx`의
  `searchAndVerifyLostItem()`을 실제 호출로 바꾸면 된다. 성공 시 OTP는 백엔드의
  `POST /api/found-items/{id}/return-code`가 생성한다.
- 수령 OTP는 8자리(현재 백엔드는 Base32 A~Z·2~7)이며 영문 대문자·숫자를 받는다.
- 개발 서버(`npm run dev`)에서만 테스트 OTP `DEV00000`이 동작한다. 백엔드를 호출하지 않고 수령 화면 흐름만 보여 주며, 빌드본에서는 꺼진다.
- 현재 위치(지도)는 HTTPS 또는 localhost에서만 사용할 수 있다. HTTP 배포에서는 키오스크 목록만 보인다.
- 2차 촬영 각도 안내 문구(`SHOT2_GUIDE`)는 이미지 처리 AI 파트의 가이드가 나오면 바꾼다.
