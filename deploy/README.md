# EC2 배포

GitHub `production` Environment를 환경설정의 관리 원본으로 사용한다. `main` push 또는 `main`에서 수동 실행 시 CI 통과 후 배포한다. PR에서는 AWS 권한·운영 Secrets 없이 테스트와 이미지 빌드만 실행한다.

## 대상과 현재 공개 범위

- 리전: `ap-northeast-2`
- 인스턴스: `i-09572eeeedf30263e`, Ubuntu 24.04, x86_64, t3.micro
- 사진 버킷: `smart-lost-found-images-s3` (기존 비공개 S3)
- 배포 경로: `/opt/smart-lost-found`
- API 도메인·FE 주소·인증: 미정

현재는 HTTP `/health`, `/ready`, `/docs`, `/openapi.json`을 공개한다. 도메인 없이도 `http://52.78.12.168/docs`에서 Swagger와 API 계약을 확인할 수 있다. 업무 API는 Nginx에서 503으로 차단하며 앱의 `LOCAL_DEVELOPMENT=false`도 강제하므로 Swagger에서 업무 API를 실행하면 503이 반환된다. GitHub Variables에 같은 이름으로 true를 추가해도 활성화되지 않는다. 인증·공개 API 계약이 정해진 뒤 별도 코드 변경으로 개방한다. CORS는 FE 주소가 확정될 때 허용 출처를 지정한다.

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
| Secret | POSTGRES_PASSWORD | 생성된 32자 이상 임의 비밀번호 |
| Variable | API_DOMAIN | DNS 연결 후 입력. 현재 생략 |
| Variable | TLS_EMAIL | 도메인 설정 시 함께 입력할 인증서 연락 이메일 |

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

1. PostgreSQL 17 테스트, 배포 설정 테스트, 운영 이미지 빌드와 readiness 확인.
2. GitHub OIDC 임시 인증으로 ECR에 `커밋SHA-runID-attempt` 태그를 push. EC2에는 태그 대신 digest를 전달.
3. GitHub 설정을 검증해 SSM SecureString에 동기화. SSM 명령 본문에는 비밀번호 없이 Parameter 경로만 전달.
4. EC2가 이미지를 pull하고 이미지에 포함된 배포 스크립트를 해당 릴리스 디렉터리로 복사. EC2에서 소스를 빌드하지 않음.
5. EC2 역할로 설정을 읽어 권한 0600의 env 파일 생성. Compose raw env 형식으로 `$`·따옴표를 그대로 보존.
6. 고정 PostgreSQL 볼륨 유지, 마이그레이션 전 pg_dump, `alembic upgrade head`.
7. 백엔드 교체 후 `/ready`와 업무 API 503 확인. 실패 시 가능한 경우 이전 이미지로 복귀하고 실패로 보고.

GitHub와 서버의 배포 잠금으로 동시 배포를 직렬화한다. GitHub 실행을 강제 취소해도 이미 전송된 SSM 명령은 계속 실행될 수 있으므로 SSM 명령 상태를 확인한 뒤 재실행한다. 이 구성은 단일 서버이며 컨테이너 교체 중 잠깐의 중단이 있을 수 있다. DB 스키마는 자동 downgrade하지 않는다. 마이그레이션은 이전 앱과 호환되도록 작성해야 이미지 복귀가 가능하다.

`smart-lost-found-postgres-production` 볼륨은 개발 Compose 볼륨과 분리한다. 배포 스크립트는 `down -v`나 DB 볼륨 삭제를 실행하지 않는다. t3.micro에 맞춰 앱 worker는 1개, 컨테이너 메모리와 로그 크기를 제한했다. 부하 검증과 필요 시 인스턴스 확대는 별도 작업이다.

백업은 `/opt/smart-lost-found/backups/릴리스ID.dump`에 저장한다. 서버 디스크 장애 대비 원격 백업과 보존 기간·자동 정리는 아직 미구현이다. 릴리스·이미지·백업이 누적되므로 디스크 사용량을 확인해야 한다. 복구는 별도 DB에 `pg_restore`로 검증한 뒤 진행한다.

## 도메인이 정해진 뒤 HTTPS

1. API 도메인의 A 레코드를 EC2 탄력적 IP에 연결한다.
2. GitHub production Variables에 `API_DOMAIN`, `TLS_EMAIL`을 추가한다.
3. main에서 워크플로를 재실행한다.

Certbot HTTP-01 검증으로 인증서를 발급한 뒤 Nginx가 HTTPS를 제공하고 HTTP를 리다이렉트한다. systemd timer가 하루 두 번 갱신 여부를 확인한다. 80 포트는 갱신 검증을 위해 유지한다. HTTPS 활성화는 업무 API의 인증·공개 승인과 별개이며 업무 API는 계속 503이다.

도메인이 없으면 인증서 발급은 실행하지 않는다. TLS 설정 실패는 배포 실패로 표시하되 이미 readiness를 통과한 백엔드는 유지한다. `enable_https.sh`를 통한 HTTPS 설정은 실제 DNS와 인증서 발급을 통해 별도로 확인해야 한다.

## 점검 명령

```bash
cd /opt/smart-lost-found/current
sudo docker compose --env-file compose.env ps
curl -f http://127.0.0.1:8000/ready
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/kiosks
```

마지막 요청은 현재 503이 정상이다. 환경값이 출력되는 `docker compose config`, `docker inspect` 전체 출력이나 env 파일 내용을 Actions/공개 로그에 붙이지 않는다.
