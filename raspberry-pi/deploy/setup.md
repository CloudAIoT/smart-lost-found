# 라즈베리파이 설치와 접속

촬영 Pi와 보관함 Pi를 처음 준비하는 순서다. 비밀번호·와이파이 암호·API 키는 이 문서와 저장소에 적지 않는다.

## 1. 준비물

- Raspberry Pi 4 Model B 4GB 2대, microSD 64GB 2장, 5V 3A USB-C 전원 2개
- 촬영 Pi용 Camera Module 3와 흰색 15핀 카메라 케이블
- SD 카드 리더기가 있는 PC와 Raspberry Pi Imager

## 2. 운영체제 쓰기 (Raspberry Pi Imager 2.0)

| 항목 | 촬영 Pi | 보관함 Pi |
| --- | --- | --- |
| 장치 | Raspberry Pi 4 | Raspberry Pi 4 |
| 운영체제 | Raspberry Pi OS (64-bit) | Raspberry Pi OS (64-bit) |
| 호스트 이름 | `slf-capture` | `slf-locker` |
| 사용자 | `pi` | `pi` |
| 지역 | 시간대 Asia/Seoul, 키보드 kr | 같음 |
| 무선랜 | 설치 장소의 와이파이, 국가 KR | 같음 |
| SSH | 켜기, 공개키 인증만 | 같음 |
| Raspberry Pi Connect | 끄기 | 끄기 |

- 2026-10-26에 쓴 이미지는 Raspberry Pi reference 2026-10-06(Debian 13 trixie, 64비트)이다.
- 학교에서 받은 SD 카드에 설치되어 있던 운영체제는 쓰지 않고 새로 썼다([문제 해결 기록 1](../docs/troubleshooting.md)).
- 키보드를 kr로 두면 한/영·한자 키가 인식되어 나중에 한글 입력기를 쓸 때 편하다. 영문 배치는 us와 같다.
- 보관함 Pi는 화면 없이 쓰므로 Lite 버전이어도 되지만, 두 Pi를 같은 이미지로 맞춰 관리하기 쉽게 했다.
- 쓰기가 끝나면 카드를 다시 꽂아 `bootfs`의 설정 파일을 확인한다. 화면 없이 처음 켜는 방식이라 설정이 빠지면 원인을 찾기 어렵다.
  - `user-data`: 호스트 이름, 사용자, SSH 공개키, 비밀번호 로그인 꺼짐(`ssh_pwauth: false`)
  - `network-config`: 와이파이와 국가(`regulatory-domain: "KR"`)
  - `config.txt`: `camera_auto_detect=1`

## 3. 카메라 연결 (촬영 Pi, 전원을 끈 상태)

1. 오디오 잭과 HDMI 단자 사이의 CAMERA 단자 걸쇠를 살짝 들어 올린다. 보드 가장자리의 DISPLAY 단자는 화면용이라 카메라를 연결할 수 없다.
2. 흰색 15핀 케이블을 **은색 접점이 HDMI 단자 쪽을 향하게** 끝까지 꽂고 걸쇠를 누른다. 반대로 꽂으면 카메라가 인식되지 않는다([문제 해결 기록 2](../docs/troubleshooting.md)).
3. Pi 4에는 카메라 단자가 하나뿐이라 카메라는 1대만 연결한다([결정 기록 0001](../docs/decisions/0001-single-camera-two-shots.md)).

## 4. 첫 부팅과 접속

전원을 연결하면 첫 부팅 설정(cloud-init)과 저장 공간 확장에 약 5분 걸린다. 같은 네트워크의 PC에서 접속한다.

```bash
ssh pi@slf-capture.local
ssh pi@slf-locker.local
```

SSH는 공개키로만 접속된다. 사용자 비밀번호는 `sudo`를 쓸 때 입력한다.

## 5. 설치 확인

```bash
tr -d '\0' < /proc/device-tree/model; echo     # 보드 모델
cat /etc/os-release                           # 운영체제 버전
vcgencmd measure_temp; vcgencmd get_throttled # throttled=0x0이면 전원 부족·과열 기록 없음
timedatectl                                   # 시간 동기화 (명령 만료 시각 검사에 필요)
rpicam-hello --list-cameras                   # 촬영 Pi: imx708이 보여야 함
```

2026-10-26 확인 결과:

| 항목 | 촬영 Pi | 보관함 Pi |
| --- | --- | --- |
| 보드 | Raspberry Pi 4 Model B Rev 1.5, 메모리 4GB | 같음 |
| 운영체제 | Debian 13 trixie, 64비트, Python 3.13 | 같음 |
| 저장 공간 | 57GB로 확장 | 57GB로 확장 |
| 온도·전원 | 44.8℃, throttled=0x0 | 42.3℃, throttled=0x0 |
| 시간 | Asia/Seoul, 동기화됨 | 같음 |
| 카메라 | imx708 4608x2592 인식, picamera2 0.3.37 | 연결 안 함 |
| GPIO 도구 | | pinctrl, gpioinfo, gpiozero 기본 설치 |

두 Pi 모두 cloud-init 상태가 `degraded done`으로 나온다. 경고 내용은 `cc_netplan_nm_patch` 모듈을 찾지 못했다는 것뿐이고 네트워크는 정상이다.

## 6. 네트워크

- 개발: PC와 같은 공유기의 와이파이에 연결한다. 공인 주소를 바로 받는 유선 회선에는 Pi를 연결하지 않는다. Pi가 인터넷에 그대로 노출될 수 있다.
- 시연(학교): 학교 와이파이는 로그인 방식과 기기 간 통신 차단 때문에 쓰지 않고 휴대폰 핫스팟을 쓴다. 와이파이는 나중에 `nmcli`로 추가할 수 있다.
- 백엔드(EC2)와의 연결은 Tailscale을 쓸 예정이다. 계정과 기기 등록은 백엔드와 정한 뒤 이 문서에 추가한다.
