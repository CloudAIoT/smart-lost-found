# 문제 해결 기록

라즈베리파이 파트에서 막혔던 문제와 원인, 해결 방법을 기록한다.

## 1. 학교 SD 카드의 운영체제로는 Camera Module 3를 쓸 수 없음 (2026-10-26)

- **상황**: 키트의 microSD 2장에 운영체제가 이미 설치되어 있었다.
- **확인**
  - `issue.txt`: Raspberry Pi reference 2023-05-03 → Debian 11 Bullseye, 32비트
  - `config.txt`: `start_x=1`, `dtoverlay=vc4-fkms-v3d`, `gpu_mem=128` → 예전 방식(레거시) 카메라 설정
  - 루트 영역이 카드 전체로 확장되어 있어 이미 사용한 카드였다.
- **원인**: Camera Module 3(IMX708)는 libcamera 방식으로만 동작해서 레거시 카메라 설정에서는 인식되지 않는다. 수업 기준(Trixie)보다 두 세대 전 운영체제였고 계정 정보도 알 수 없었다.
- **해결**: Raspberry Pi Imager로 최신 Raspberry Pi OS(64비트, trixie)를 두 카드에 새로 썼다. 새 이미지의 `config.txt`에는 `camera_auto_detect=1`, `dtoverlay=vc4-kms-v3d`가 기본으로 들어 있다. 설치 순서는 [설치와 접속](../deploy/setup.md)에 정리했다.

## 2. 카메라가 인식되지 않음 (2026-10-26)

- **증상**: `rpicam-hello --list-cameras` → `No cameras available!`
- **확인**: 커널 기록에 imx708 메시지가 없고 `/dev/video0`도 없었다. `config.txt`의 `camera_auto_detect=1`은 정상이었다. 부팅 때 펌웨어가 카메라를 찾지 못한 것이다.
- **원인**: 카메라 케이블이 CAMERA 단자에 반대 방향으로 꽂혀 있었다.
- **해결**: 전원을 끄고 은색 접점이 HDMI 단자 쪽을 향하게 다시 꽂았다. 재부팅 후 `imx708 [4608x2592 10-bit RGGB]`로 인식되었고 커널 기록에 `camera module ID 0x0301`이 남았다.
- **참고**: 카메라 케이블은 전원이 꺼진 상태에서만 꽂고 뺀다.

## 3. 학교에서 Pi가 와이파이에 연결되지 않음 (2026-11-13)

- **증상**: 학교에서 두 Pi의 전원을 켰지만 맥에서 `slf-capture.local`, `slf-locker.local`이 응답하지 않았다.
- **원인**: Pi에는 운영체제를 설치할 때 넣은 와이파이 한 곳만 저장되어 있었다. 학교 와이파이는 로그인 방식과 기기 간 통신 차단 때문에 쓰지 않기로 했고, 휴대폰 핫스팟은 Pi에 저장되어 있지 않았다. 화면 없이 쓰는 Pi라 와이파이를 추가하려면 먼저 접속이 되어야 했다.
- **해결**: 휴대폰 핫스팟의 이름과 비밀번호를 Pi에 저장된 와이파이와 똑같이 바꿔 두 Pi를 핫스팟에 붙였다. 저장된 와이파이 이름은 SD 카드를 만들 때 쓴 PC의 Raspberry Pi Imager 설정에서 찾았다.
- **참고**: 접속한 뒤 핫스팟을 Pi에 추가해 두면 다음부터는 이름을 바꾸지 않아도 된다. 명령은 [설치와 접속 6절](../deploy/setup.md#6-네트워크)에 적었다.

## 4. 핫스팟에서 맥이 Pi를 IPv4로 찾지 못함 (2026-11-13)

- **증상**: 맥과 두 Pi를 같은 아이폰 핫스팟에 연결했지만 `ping slf-capture.local`이 응답하지 않았다.
- **확인**: 맥은 IPv4 주소 없이 IPv6 주소만 받았고, IPv4 사이트는 macOS의 변환 기능으로 접속하고 있었다. Pi는 IPv4(`172.20.10.x`)와 IPv6를 모두 받았다. 맥의 `ping`은 IPv4만 쓰기 때문에 응답이 없었다.
- **해결**: IPv6로 접속했다. `ping6 slf-capture.local`과 `ssh pi@slf-capture.local`은 정상 동작했고, Pi에서 Tailscale 설치·GitHub·패키지 저장소 접속도 정상이었다.
