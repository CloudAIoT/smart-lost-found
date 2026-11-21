# EC2 배포

GitHub `production` Environment를 환경설정의 관리 원본으로 사용한다. `main` push 또는 `main`에서 수동 실행 시 CI 통과 후 배포한다. PR에서는 AWS 권한·운영 Secrets 없이 테스트와 이미지 빌드만 실행한다.

## 대상과 현재 공개 범위

- 리전: `ap-northeast-2`
- 인스턴스: `i-09572eeeedf30263e`, Ubuntu 24.04, x86_64, t3.micro
- 사진 버킷: `smart-lost-found-images-s3` (기존 비공개 S3)
- 배포 경로: `/opt/smart-lost-found`
- 프론트엔드: 같은 서버의 `/`에서 nginx가 정적 파일로 서비스한다. 주소 `http://52.78.12.168/`
- API 도메인·인증: 미정

Swagger 주소는 `http://52.78.12.168/docs`다. 업무 API는 GitHub production Variable `BUSINESS_API_ENABLED`를 `true`로 설정하고 재배포하면 사용할 수 있다. 값이 없거나 `false`면 503을 반환한다. `LOCAL_DEVELOPMENT=false`는 유지한다.

아직 인증이 없어 활성화한 API는 누구나 호출할 수 있다. Pi·AI 설정이 필요한 기능은 활성화 후에도 미설정 오류를 반환한다.

## GitHub 설정

저장소 Settings → Environments → production에서 관리한다. 배포 브랜치 정책은 **branch `main`만 허용**, 수동 승인자는 두지 않는다.

| 종류 | 이름 | 값/의미 |
| --- | --- | --- |
| Variable | AWS_REGION | ap-northeast-2 |
| Variable | AWS_ROLE_ARN | bootstrap이 생성한 배포 역할 ARN |
| Variable | EC2_INSTANCE_ID | i-09572eeeedf30263e |
| Variable | ECR_REPOSITORY | smart-lost-found/backend |
| Variable | CONFIG_PARAMETER | /smart-lost-found/production/config |
| Variable | POSTGRES_DB / POSTGRES_USER | smart_lost_found |
| Variable | S3_BUCKET | smart-lost-found-images-s3 |
| Variable | BUSINESS_API_ENABLED | true: 업무 API 활성화, false 또는 미등록: 비활성화 |
| Secret | POSTGRES_PASSWORD | 생성된 32자 이상 임의 비밀번호 |
| Variable | API_DOMAIN | DNS 연결 후 입력. 현재 생략 |
| Variable | TLS_EMAIL | 도메인 설정 시 함께 입력할 인증서 연락 이메일 |
| Secret | PI_DEVICES | 키오스크 ID별 Pi 주소·API 키 JSON. Pi API 키가 들어 있어 Variable이 아닌 Secret으로 둔다 |
| Variable | COMMAND_TTL_SECONDS | 장치 명령 만료 시간(초). 비우면 Pi 기능이 미설정 오류를 낸다 |
| Variable | PI_TIMEOUT_SECONDS | 백엔드가 Pi 응답을 기다리는 시간(초) |
| Variable | MAX_IMAGE_BYTES | 백엔드가 받는 촬영 사진 크기 상한 |
| Variable | PREVIEW_TTL_SECONDS | 미리보기 서명 URL 유효 시간(초) |
| Variable | RETURN_CODE_TTL_SECONDS | 반환 코드 유효 시간(초) |
| Secret | GEMINI_API_KEY | 등록 사진 분석과 분실물 검색·문답 생성용 Gemini 키 |
| Variable | GEMINI_MODEL | 키오스크 등록 사진 분석에 사용할 Gemini 모델 이름 |
| Variable | DEV_FAKE_DEVICE | true: Pi 대신 개발용 가짜 장치 사용. 값이 없거나 false면 실제 Pi를 호출한다 |
| Variable | IMAGE_ANALYSIS_TIMEOUT_SECONDS | 사진 2장 분석 대기 시간. 기본 90초, 허용 5~90초. 소유 확인 텍스트 요청의 대기 시간과 별도 |
| Variable | MQTT_HOST | 장치 상태 구독 대상. Compose에서는 `mqtt`. 비우면 구독을 시작하지 않는다 |
| Variable | MQTT_PORT | 기본 1883 |
| Variable | MQTT_USERNAME | 백엔드용 브로커 계정. ACL에서 읽기 전용이다 |
| Secret | MQTT_PASSWORD | 그 계정의 비밀번호 |
| Variable | MQTT_CLIENT_ID | 기본 `slf-backend`. 백엔드가 꺼진 동안의 경고 큐가 이 값에 묶인다 |
| Variable | MQTT_ALERT_RETENTION_DAYS | 경고 보관 기간. 기본 14일 |
| Variable | MQTT_BIND_ADDRESS | 브로커 1883을 공개할 EC2 주소. **Tailscale 주소(`100.x.y.z`)만 넣는다.** 비우면 127.0.0.1에만 열려 Pi가 닿지 못한다 |

로컬 `backend/.env`의 Gemini 설정은 EC2에 자동으로 전달되지 않는다. 위 GitHub production Secret·Variable을 설정하고 배포해야 SSM SecureString과 런타임 환경에 반영된다. 사진 분석 경로의 Nginx 응답 대기 시간은 120초다. 배포 후 등록 ID로 호출하고 저장 JSON을 읽는 방법은 [분석·DB 저장 확인](../docs/api.md#등록-id로-사진-분석db-저장-확인)을 따른다.

Pi 설정 6종은 모두 선택값이다. 전부 비우면 지금처럼 Pi·수령 기능이 미설정 오류를 낸다. 하나라도
빈 문자열로 두면 그 키 자체를 보내지 않는다(`deploy/publish.py`). `PI_DEVICES`는 EC2가 두 Pi와
같은 tailnet에 있어야 닿는다. 값은 `backend/.env.example`의 `PI_DEVICES` 예시와 같은 JSON 한 줄이고,
키오스크 ID마다 `capture_url`·`capture_api_key`·`locker_url`·`locker_api_key` 네 개가 모두 있어야
한다. EC2의 Tailscale 설치·인증은 이 문서 범위 밖이며 이미 되어 있다고 가정한다.

`DEV_FAKE_DEVICE=true`는 Pi를 부르는 자리에 `app.dev_device`의 개발용 가짜 장치를 끼운다. 촬영은 이미지에
들어 있는 더미 JPEG 2장을 촬영 순서대로 돌려주고, 보관함 개방은 성공으로, 문 상태는 항상 `closed`로
답한다. S3 저장·미리보기 URL·상태 전이·DB 기록은 실제 경로 그대로다. 이 값은 Pi 없이 FE 흐름을
확인할 때만 켜고, 실제 물건을 보관하는 운영에서는 끈다. 장치 호출이 실패했다고 자동으로 가짜 장치로
넘어가지는 않는다.

`DEV_FAKE_DEVICE=true`를 켠 상태에서 FE 질문 화면용 보관 물품 10건을 만들거나 보관 상태로 되돌리려면
`sudo docker exec smart-lost-found-prod-backend-1 python -m app.seed_demo_items`를 실행한다. 내용과 되돌리기
규칙은 [개발 확인용 보관 물품](../docs/api.md#개발-확인용-보관-물품-10건)에 있다.

Variables/Secrets 변경만으로 배포 이벤트가 발생하지 않는다. 저장소 Actions → Backend CI and EC2 deployment → Run workflow → **main**으로 재배포한다. `.env`나 장기 AWS 액세스 키를 Git에 올리지 않는다. 이미지 안에도 비밀값을 넣지 않는다.

DB 비밀번호는 변경 전에 PostgreSQL 역할의 비밀번호 변경 절차를 먼저 계획해야 한다. 기존 DB와 다른 값이 들어오면 배포 스크립트가 중단한다. 단순 Secret 수정은 PostgreSQL 역할의 비밀번호를 바꾸지 않는다. 현재 자동 회전 API는 없다.

## 최초 연결

관리자 AWS 자격증명과 저장소 관리자 권한이 있는 GitHub CLI로 실행한다. 로컬 AWS 키를 GitHub로 복사하지 않는다.

```powershell
python deploy/bootstrap.py --region ap-northeast-2 --instance i-09572eeeedf30263e --bucket smart-lost-found-images-s3 --repository CloudAIoT/smart-lost-found
```

이 명령은 전용 ECR·OIDC 배포 역할·GitHub Environment와 변수를 준비한다. 기존 EC2 역할에 해당 ECR pull·해당 SSM Parameter 읽기 권한만 추가하고 기존 S3 정책은 변경하지 않는다. 이미 있는 비밀번호 Secret은 덮어쓰지 않는다. 최초 생성 비밀번호는 로그나 파일에 출력하지 않고 GitHub Secret과 암호화된 SSM 실행용 사본으로 전달한다. 기존 계정의 GitHub OIDC Provider가 필요하다.

이 저장소는 immutable OIDC subject를 사용한다. bootstrap은 GitHub API의 실제 `sub_claim_prefix`를 읽어 `:environment:production`을 붙인다. 예전 문서의 `repo:소유자/저장소` 형식을 임의로 쓰지 않는다. AWS IAM trust와 GitHub의 main 전용 환경 정책을 함께 적용한다.

EC2에서 사전 확인할 항목:

- Docker Compose 2.30 이상, AWS CLI, curl, flock 설치
- SSM Agent Online 및 AmazonSSMManagedInstanceCore 역할
- IMDS endpoint 활성화, IMDSv2 필수, 응답 홉 제한 2
- 아웃바운드 ECR·S3·SSM·인증서 발급 서버 접속 가능
- 80/443은 프록시만 사용. 5432는 호스트에 공개하지 않고 8000은 loopback만 바인딩

Ubuntu 24.04의 현재 저장소에서는 `apt install awscli` 설치 후보가 없었으므로, AWS 공식 x86_64 설치 프로그램 `https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip`을 사용한다. SSM/관리 세션에서 다운로드·압축 해제 후 `sudo ./aws/install`을 실행하고 `aws --version`을 확인한다. SSH 인바운드나 개인키는 Actions 배포에 필요하지 않다.

## 배포 동작

1. PostgreSQL 17 테스트, 배포 설정 테스트, 운영 이미지 빌드와 readiness 확인. 이미지 빌드 첫 단계에서 `frontend/`를 `npm ci && npm run build`로 빌드해 `dist/`를 `/opt/deploy/frontend`에 넣는다(최종 이미지에 Node는 없다).
2. GitHub OIDC 임시 인증으로 ECR에 `커밋SHA-runID-attempt` 태그를 push. EC2에는 태그 대신 digest를 전달.
3. GitHub 설정을 검증해 SSM SecureString에 동기화. SSM 명령 본문에는 비밀번호 없이 Parameter 경로만 전달.
4. EC2가 이미지를 pull하고 이미지에 포함된 배포 스크립트를 해당 릴리스 디렉터리로 복사. EC2에서 소스를 빌드하지 않음.
5. EC2 역할로 설정을 읽어 권한 0600의 env 파일 생성. Compose raw env 형식으로 `$`·따옴표를 그대로 보존.
6. 고정 PostgreSQL 볼륨 유지, 마이그레이션 전 pg_dump, `alembic upgrade head`.
7. 백엔드 교체 후 `/ready`, nginx의 `/`(프론트), `/api/kiosks` 확인. 활성화 상태면 200, 비활성화 상태면 503이어야 한다. 실패 시 가능한 경우 이전 이미지로 복귀하고 실패로 보고.

GitHub와 서버의 배포 잠금으로 동시 배포를 직렬화한다. GitHub 실행을 강제 취소해도 이미 전송된 SSM 명령은 계속 실행될 수 있으므로 SSM 명령 상태를 확인한 뒤 재실행한다. 이 구성은 단일 서버이며 컨테이너 교체 중 잠깐의 중단이 있을 수 있다. DB 스키마는 자동 downgrade하지 않는다. 마이그레이션은 이전 앱과 호환되도록 작성해야 이미지 복귀가 가능하다.

`smart-lost-found-postgres-production` 볼륨은 개발 Compose 볼륨과 분리한다. 배포 스크립트는 `down -v`나 DB 볼륨 삭제를 실행하지 않는다. t3.micro에 맞춰 앱 worker는 1개, 컨테이너 메모리와 로그 크기를 제한했다. 부하 검증과 필요 시 인스턴스 확대는 별도 작업이다.

백업은 `/opt/smart-lost-found/backups/릴리스ID.dump`에 저장한다. 서버 디스크 장애 대비 원격 백업과 보존 기간·자동 정리는 아직 미구현이다. 릴리스·이미지·백업이 누적되므로 디스크 사용량을 확인해야 한다. 복구는 별도 DB에 `pg_restore`로 검증한 뒤 진행한다.

## 장치 상태 MQTT 브로커

Pi가 보내는 센서 상태와 이상 상황을 받는 Mosquitto를 같은 EC2에 둔다. 규약은 [MQTT 규약](../docs/mqtt-protocol.md)이다.

AWS 쪽에서 **바꿀 것이 없다.** Pi와 EC2가 같은 tailnet에 있으므로 1883을 보안 그룹에 열지 않는다. 열면 평문 MQTT가 인터넷에 노출된다. Tailscale 조율 트래픽은 기존 아웃바운드로 충분하고, 탄력적 IP·Route 53·IAM 역할·인스턴스 타입도 그대로다. 브로커는 AWS API를 호출하지 않는다.

연결 범위는 두 구간으로 나뉜다.

- Pi → 브로커: EC2의 Tailscale 주소 `100.x.y.z:1883`. Compose가 그 주소에만 포트를 공개한다(`MQTT_BIND_ADDRESS`).
- 백엔드 → 브로커: Compose 내부 네트워크의 `mqtt:1883`. 호스트 네트워크를 거치지 않는다.

계정·권한 파일은 비밀값이라 저장소에 두지 않고 서버에 직접 넣는다. [deploy/mosquitto/](mosquitto/)에 `mosquitto.conf`와 `acl` 사본이 있고, `passwords`만 서버에서 만든다. 세 파일이 모두 있을 때만 `deploy.sh`가 브로커를 올리므로, 넣지 않은 서버의 배포는 지금과 똑같이 동작한다.

```bash
ssh -i ~/.ssh/smart-lost-found.pem ubuntu@100.73.97.89

sudo mkdir -p /opt/smart-lost-found/mosquitto

# 저장소의 두 파일을 올린다. 워크스테이션에서 홈으로 scp한 뒤 옮긴다.
#   scp -i <키> deploy/mosquitto/mosquitto.conf deploy/mosquitto/acl ubuntu@<주소>:~/
sudo mv ~/mosquitto.conf ~/acl /opt/smart-lost-found/mosquitto/

# 계정 3개. 비밀번호는 대화형으로 두 번씩 입력한다.
# --user root가 없으면 이미지의 mosquitto 사용자가 root 소유 디렉터리에 파일을 만들지 못한다.
#
# 한 번에 세 줄을 붙여넣지 않는다. mosquitto_passwd가 붙여넣기 버퍼에 남은 다음 줄을
# 비밀번호로 읽어, 틀렸다는 말도 없이 엉뚱한 값이 등록된다. 한 줄씩 실행하고 끝까지 기다린다.
# -c는 첫 계정에만 쓴다. 두 번째부터 쓰면 앞의 계정이 지워진다.
sudo docker run --rm -it --user root -v /opt/smart-lost-found/mosquitto:/work \
  eclipse-mosquitto:2.0 mosquitto_passwd -c /work/passwords capture-pi

sudo docker run --rm -it --user root -v /opt/smart-lost-found/mosquitto:/work \
  eclipse-mosquitto:2.0 mosquitto_passwd /work/passwords locker-pi

sudo docker run --rm -it --user root -v /opt/smart-lost-found/mosquitto:/work \
  eclipse-mosquitto:2.0 mosquitto_passwd /work/passwords backend

# 컨테이너의 mosquitto 사용자(uid 1883)가 읽을 수 있어야 한다. 빠뜨리면 브로커가 뜨다 죽는다.
# 배포 디렉터리는 root 전용이라 cd로 들어갈 수 없다(sudo cd도 쉘 빌트인이라 안 된다). 절대 경로로 쓴다.
sudo chown 1883:1883 /opt/smart-lost-found/mosquitto/{mosquitto.conf,acl,passwords}
sudo chmod 0400 /opt/smart-lost-found/mosquitto/{mosquitto.conf,acl,passwords}
sudo ls -l /opt/smart-lost-found/mosquitto   # -r-------- 1 1883 1883 세 줄
```

그다음 GitHub production에 위 표의 `MQTT_*`를 넣고 main에서 워크플로를 재실행한다. `MQTT_BIND_ADDRESS`는 이 서버의 Tailscale 주소이고 `MQTT_HOST`는 `mqtt`다. 둘을 바꿔 넣으면 Pi가 닿지 못하거나 포트가 잘못 열린다.

`mqtt` 서비스와 브로커 분기는 이미지에 담겨 배포된다. 그래서 **브로커를 추가한 커밋이 main에 들어간 뒤에 재배포해야** 한다. 그 전에 설정만 넣고 배포하면 브로커 없이 백엔드가 재접속만 반복한다.

점검:

```bash
# 브로커가 떴는지, Tailscale 주소에만 열렸는지
sudo docker ps --filter name=smart-lost-found-prod-mqtt-1
sudo ss -lntp | grep 1883          # 100.x.y.z:1883 만 보여야 한다

# 백엔드가 붙어 구독을 시작했는지
sudo docker logs --tail 20 smart-lost-found-prod-backend-1 | grep -i mqtt

# 메시지가 들어오는지 (백엔드 계정은 읽기 전용이라 이 구독이 ACL 확인도 된다)
sudo docker exec -it smart-lost-found-prod-mqtt-1 \
  mosquitto_sub -h 127.0.0.1 -u backend -P '<비밀번호>' -t 'slf/#' -v
```

Pi 두 대의 `.env`에는 `PI_MQTT_HOST=100.73.97.89`과 각자의 계정(`capture-pi`, `locker-pi`)을 넣고 서비스를 다시 켠다([라즈베리파이 README](../raspberry-pi/README.md#가상-센서와-mqtt)).

경고는 보존 메시지가 아니라서, 백엔드가 배포로 교체되는 동안 발행된 경고는 브로커의 세션 큐에만 남는다. 그래서 백엔드는 고정 `MQTT_CLIENT_ID`로 세션을 유지한다. 같은 ID를 쓰는 두 번째 구독자를 붙이면 서로 연결을 끊고 그 큐를 잃는다. 로컬에서 운영 브로커에 붙어 볼 때는 다른 ID를 쓴다.

브로커 상태와 큐는 `smart-lost-found-mosquitto-production` 볼륨에 남는다. 배포 스크립트는 이 볼륨을 지우지 않는다.

## 도메인이 정해진 뒤 HTTPS

1. API 도메인의 A 레코드를 EC2 탄력적 IP에 연결한다.
2. GitHub production Variables에 `API_DOMAIN`, `TLS_EMAIL`을 추가한다.
3. main에서 워크플로를 재실행한다.

Certbot HTTP-01 검증으로 인증서를 발급한 뒤 Nginx가 HTTPS를 제공하고 HTTP를 리다이렉트한다. systemd timer가 하루 두 번 갱신 여부를 확인한다. 80 포트는 갱신 검증을 위해 유지한다. 업무 API 활성화 여부는 `BUSINESS_API_ENABLED`로 관리한다.

도메인이 없으면 인증서 발급은 실행하지 않는다. TLS 설정 실패는 배포 실패로 표시하되 이미 readiness를 통과한 백엔드는 유지한다. `enable_https.sh`를 통한 HTTPS 설정은 실제 DNS와 인증서 발급을 통해 별도로 확인해야 한다.

## 점검 명령

```bash
cd /opt/smart-lost-found/current
sudo docker compose --env-file compose.env ps
curl -f http://127.0.0.1:8000/ready
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/kiosks
```

마지막 요청은 업무 API를 활성화했으면 200, 비활성화했으면 503이 정상이다. 환경값이 출력되는 `docker compose config`, `docker inspect` 전체 출력이나 env 파일 내용을 Actions/공개 로그에 붙이지 않는다.

## 프론트엔드 서비스

nginx는 릴리스 디렉터리의 `frontend/`(이미지에서 복사된 빌드 결과물)를 `/usr/share/nginx/html`로 읽기 전용 마운트한다.
`/api/`, `/docs`, `/openapi.json`, `/health`, `/ready`는 지금처럼 백엔드로 넘기고, 나머지 경로는 `index.html`로 보낸다.
`/assets/`의 해시 파일은 1년 캐시, `index.html`은 `no-cache`다. 프론트와 API가 같은 출처이므로 CORS 설정이 필요 없다.

배포 실패로 이전 릴리스로 되돌릴 때는 nginx 컨테이너를 다시 만들어 이전 릴리스의 `frontend/`를 마운트한다.
이 변경 이전 릴리스에는 `frontend/`가 없으므로, 그 릴리스로 되돌리면 `/`에는 nginx 기본 페이지가 보이고 API는 정상 동작한다.

현재 위치 기반 거리 계산은 브라우저 정책상 HTTPS에서만 동작한다. 도메인과 HTTPS를 설정하기 전에는 지도에 키오스크 목록만 표시된다.
