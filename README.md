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

### Tailscale로 연결한 Pi에 보내기

Pi 주소를 주면 프로세스를 띄우지 않고 그 주소의 Pi를 그대로 부른다. 백엔드와 테스트는 이 PC에서
돌고, Pi로 가는 요청만 tailnet을 건넌다. 두 Pi와 이 PC가 같은 tailnet에 있어야 한다.

```bash
E2E_CAPTURE_URL=http://slf-capture:8000 E2E_LOCKER_URL=http://slf-locker:8000 \
E2E_CAPTURE_API_KEY=<촬영 Pi 키> E2E_LOCKER_API_KEY=<보관함 Pi 키> \
python -m pytest -q
```

PowerShell에서는 `$env:E2E_CAPTURE_URL="http://slf-capture:8000"`처럼 변수를 먼저 정한 뒤 실행한다.
MagicDNS 이름이 안 되면 Pi에서 `tailscale ip -4`로 본 `100.x.y.z` 주소를 쓴다.

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `E2E_CAPTURE_URL`, `E2E_LOCKER_URL` | 없음 | 둘 다 주면 원격 Pi 모드로 돌린다 |
| `E2E_PI_API_KEY` | 로컬 전용 키 | 두 Pi가 같은 키를 쓸 때 |
| `E2E_CAPTURE_API_KEY`, `E2E_LOCKER_API_KEY` | `E2E_PI_API_KEY` | Pi마다 키가 다를 때 |
| `E2E_DOOR_OPEN_SECONDS` | 원격 5, 로컬 1 | Pi의 `PI_FAKE_DOOR_OPEN_SECONDS`와 맞춘다 |
| `E2E_PI_TIMEOUT_SECONDS` | 원격 20, 로컬 10 | 백엔드가 Pi 응답을 기다리는 시간 |
| `E2E_MAX_IMAGE_BYTES` | 원격 10MB, 로컬 1MB | 백엔드가 받는 사진 크기 상한 |
| `E2E_IMAGE_SIZE` | 로컬 `640x480` | 사진 크기를 검사할 값. 원격은 기본적으로 크기를 보지 않는다 |

- 원격 Pi는 테스트가 멈출 수 없다. 보관함 Pi가 응답하지 못하는 상황은 백엔드 설정의 보관함 주소를
  받는 곳이 없는 포트로 바꿔 만든다. 로컬 모드에서는 지금처럼 Pi 프로세스를 멈춘다.
- 원격 Pi는 테스트 사이에 문 상태와 명령 기록이 남는다. 각 테스트는 두 칸의 문이 닫힌 뒤에 시작한다.
- 주소나 키가 틀리면 테스트가 Tailscale과 `PI_API_KEY` 확인 방법을 적어 실패한다.
- Pi 쪽 준비는 [Tailscale 연결](raspberry-pi/deploy/setup.md)을 따른다.

## 실제 Pi와 S3로 손으로 확인하기

종단 테스트는 S3와 AI를 대역으로 바꾼다. 실제 버킷에 사진이 올라가는지와 보관함이 실제로
열리는지는 백엔드를 이 PC에서 띄워 손으로 확인한다. Pi는 Tailscale로 연결해 둔다.

1. `backend/.env`에 값을 채운다. 자격증명과 API 키는 저장소에 올리지 않는다.

```
LOCAL_DEVELOPMENT=true
COMMAND_TTL_SECONDS=30
PI_TIMEOUT_SECONDS=20
MAX_IMAGE_BYTES=10485760
PREVIEW_TTL_SECONDS=60
RETURN_CODE_TTL_SECONDS=600
S3_BUCKET=<버킷 이름>
S3_REGION=ap-northeast-2
PI_DEVICES={"00000000-0000-0000-0000-000000000001":{"capture_url":"http://100.x.x.x:8000","capture_api_key":"<촬영 Pi 키>","locker_url":"http://100.y.y.y:8000","locker_api_key":"<보관함 Pi 키>"}}
```

`PI_DEVICES`의 키는 키오스크 ID다. 시드가 만드는 개발용 키오스크는 `00000000-0000-0000-0000-000000000001`이다.
주소는 Pi에서 `tailscale ip -4`로 본 값을 쓴다. AWS 자격증명은 boto3의 환경변수나 프로필을 쓴다.

2. DB를 띄우고 마이그레이션과 시드를 돌린다. 백엔드는 컨테이너가 아니라 이 PC에서 돌리므로
   DB는 `127.0.0.1:5432`로 붙는다.

```powershell
docker compose --env-file backend/.env up -d db
cd backend; $env:DB_HOST="127.0.0.1"; python -m alembic upgrade head; python -m app.seed_local; cd ..
```

3. 백엔드를 띄운다. Swagger는 `http://127.0.0.1:8000/docs`에서 열린다.

```powershell
python tests/manual/local_server.py --stub-analyzer
```

`--stub-analyzer`는 AI 분석기만 대역으로 바꾼다. 빼면 `POST /api/registrations/{id}/analysis`가
503 `AI_NOT_CONFIGURED`이고 개방은 409 `ANALYSIS_REQUIRED`로 막힌다. 대역은 특징을 만들지
않으므로 분석 결과 자체는 이 방법으로 확인할 수 없다. 설정이 빠지면 어떤 값이 없는지 적고 멈춘다.

4. 다른 터미널에서 사용자 흐름을 끝까지 돌린다.

```powershell
python tests/manual/check_deposit_flow.py --shots-only   # 촬영·S3·DB까지만
python tests/manual/check_deposit_flow.py                # 보관 완료까지
python tests/manual/check_deposit_flow.py --pickup       # 쓴 칸을 수령까지 진행해 비운다
```

`--shots-only`는 촬영 2장과 S3·DB 저장만 확인하고 멈춘다. 분석기 대역도 보관함 Pi도 필요 없으므로
촬영 Pi와 S3만 준비되면 돌릴 수 있다.

확인하는 것은 S3 `head_object`의 크기·Content-Type, DB에 적힌 바이트 수와의 일치, 미리보기
서명 URL로 내려받은 JPEG, 보관함 Pi에 직접 물어본 문 상태(open 뒤 closed), 등록 상태 `stored`다.
Swagger로 직접 누르려면 같은 순서를 쓴다. 경로와 본문은 [웹-백엔드 API](docs/api.md)에 있다.

시드 키오스크는 칸이 2개라 보관만 반복하면 `NO_AVAILABLE_LOCKER`가 난다. `--pickup`으로 칸을 비운다.

파트별 테스트는 각 폴더에서 실행한다.

```bash
cd backend && python -m pytest -q
cd raspberry-pi && python -m pytest -q
cd deploy && python -m pytest -q
```
