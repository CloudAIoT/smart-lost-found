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
