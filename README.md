# Smart Lost Found

백엔드 로컬 개발 환경의 준비·실행·테스트 방법은 [backend/README.md](backend/README.md)를 참고한다.

## 파트 전체를 잇는 종단 테스트

사용자가 키오스크에서 분실물을 등록하는 흐름(등록 시작 → 촬영 2장 → 분석 → 보관함 개방
→ 문 닫힘 확인 → 보관 완료)을 백엔드와 실제 Pi 컨트롤러를 함께 띄워 확인한다.
`tests/`에 있고, 저장소 루트에서 실행한다.

```bash
python -m pytest -q          # 저장소 루트: 종단 테스트
```

- Pi 컨트롤러를 가짜 하드웨어 모드로 **별도 프로세스 2개**(촬영 Pi, 보관함 Pi)에 띄우고,
  백엔드가 평소처럼 HTTP(`PiGateway`)로 호출한다. 두 파트의 통신 규약이 어긋나면 여기서 깨진다.
- 보관함 문이 실제로 열렸는지는 보관함 Pi의 `GET /lockers/{id}`로 직접 확인한다.
- 테스트 대역으로 바꾸는 것은 **AI 분석기와 S3**뿐이다. AI의 외부 응답 계약이 아직 정해지지
  않았고, S3는 실제 자격증명이 필요하다. 그 사이의 백엔드·Pi 코드는 모두 실제 코드다.
- DB는 기본적으로 SQLite를 쓴다. `TEST_DATABASE_URL`을 주면 PostgreSQL의 임시 스키마에서 돌린다.

파트별 테스트는 각 폴더에서 실행한다.

```bash
cd backend && python -m pytest -q
cd raspberry-pi && python -m pytest -q
cd deploy && python -m pytest -q
```
