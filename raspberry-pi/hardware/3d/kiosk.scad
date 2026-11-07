// 스마트 분실물 보관함 시제품 배치 모델
// OpenSCAD로 이 파일을 열고 Window > Customizer에서 치수를 바꾸면 모델과 점검 결과가 같이 바뀐다.
// 좌표: X는 사용자가 볼 때 오른쪽, Y는 안쪽, Z는 위. 단위는 mm이고 책상 윗면이 Z = 0이다.

/* [보기] */
// 보여 줄 것
view = "all"; // [all:전체 배치, front_section:앞에서 자른 단면, capture:촬영 모듈, locker:보관함 모듈, lock_section:잠금 구조 단면, camera_mount:카메라 거치대, door_tab:문 걸쇠판]
// 속을 보려고 촬영 상자 오른쪽 벽, 보관함 왼쪽 벽과 앞판, Pi 칸 뚜껑을 뺀다
cutaway = false;
// 배선 표시
show_cables = true;
// 카메라 촬영 범위와 예시 물건 표시
show_fov = true;
// 1번 문 열림 각도
door1_angle = 100; // [0:5:110]
// 2번 문 열림 각도
door2_angle = 0; // [0:5:110]

/* [공통] */
// 패널 두께
panel_t = 6;
// 촬영 모듈과 보관함 모듈 사이 간격
module_gap = 60;

/* [촬영 상자] */
// 안쪽 폭
box_w = 340;
// 안쪽 깊이
box_d = 300;
// 안쪽 높이
box_h = 330;
// 앞쪽 위 가림판 높이
header_h = 50;
// 천장 위 촬영 Pi 칸 높이
bay_h = 60;

/* [카메라] */
// 상자 앞면에서 거치대 회전축까지 거리
cam_y = 50;
// 기울기 (0이면 바로 아래, 커질수록 상자 뒤쪽을 본다)
cam_tilt = 15; // [0:1:30]
// 가지고 있는 리본 케이블 길이
ribbon_len = 150;

/* [보관함] */
// 칸 안쪽 폭
cell_w = 260;
// 칸 안쪽 높이
cell_h = 170;
// 칸 안쪽 깊이
cell_d = 250;
// 잠금장치가 들어가는 왼쪽 기둥 폭
lock_col_w = 62;
// 위쪽 보관함 Pi 칸 높이
locker_bay_h = 70;

/* [물건과 주변 장비] */
// 받을 물건의 최대 폭
item_w = 220;
// 받을 물건의 최대 깊이
item_d = 140;
// 받을 물건의 최대 높이
item_h = 100;
// 모니터 크기 (인치)
monitor_inch = 24;

/* [Hidden] */
$fn = 32;
t = panel_t;

// 촬영 모듈 바깥 치수와 주요 높이
cap_w = box_w + 2 * t;
cap_d = box_d + t;
roof_z = t + box_h;           // 천장 패널 아랫면
bay_floor_z = roof_z + t;     // 촬영 Pi 칸 바닥
cap_h = bay_floor_z + bay_h + t;

// 카메라 거치대 (회전축 기준 좌표)
mount_drop = 24;              // 천장 아랫면에서 회전축까지
plate_top = -5;               // 카메라 보드가 놓이는 면
board_y = 6;                  // 회전축에서 카메라 보드 앞 끝까지
cam_x = cap_w / 2;
pivot = [cam_x, cam_y, roof_z - mount_drop];

// 카메라 화각 (공식 사양: 가로 66도, 세로 41도)
fov_h = 66;
fov_v = 41;

// 촬영 Pi 위치: 카메라 단자(보드 좌표 46.5, 11.5)가 리본 구멍과 일직선이 되게 놓는다
standoff = 8;
slot_y = cam_y + 45;
pi_x = cam_x + 11.5;
pi_y = slot_y + 8;
ribbon_z = standoff + 12;     // 방열판을 넘어가는 높이

// 보관함 모듈 바깥 치수와 주요 위치
x_part = t + lock_col_w;      // 칸막이 왼쪽 면
x_cell = x_part + t;          // 칸 왼쪽 벽
lk_w = x_cell + cell_w + t;
lk_d = cell_d + t;
lk_bay_z = 3 * t + 2 * cell_h;
lk_h = lk_bay_z + locker_bay_h + t;
locker_pos = [cap_w + module_gap, 0, 0];
function cell_z(n) = n == 1 ? 2 * t + cell_h : t;   // 1번이 위 칸, 2번이 아래 칸
function lock_z(n) = cell_z(n) + cell_h / 2;

// 잠금장치 (판매처 표기 기준 치수라 구매 후 실측해서 고친다)
lock_body = [55, 42, 30];     // 길이, 폭, 두께
lock_latch = 14;              // 몸체 밖으로 나오는 걸쇠 길이
lock_y = 17;                  // 앞면에서 걸쇠 중심까지
tab_gap = 3.5;                // 칸막이와 걸쇠판 사이 (문이 호를 그리며 닫혀서 필요하다)
tab_t = 3;
tab_len = 36;
reed_dz = 34;                 // 잠금장치 위 리드 스위치 높이

include <devices.scad>
include <capture_station.scad>
include <locker_cabinet.scad>
include <cables.scad>
include <checks.scad>

design_report();
label_points();

if (view == "all") {
    capture_station();
    translate(locker_pos) locker_cabinet();
    surroundings();
    if (show_cables) room_cables();
} else if (view == "capture") {
    capture_station();
} else if (view == "locker") {
    locker_cabinet();
} else if (view == "front_section") {
    // 앞판과 문 바로 뒤를 잘라 안쪽 장치가 한 번에 보이게 한다
    intersection() {
        union() {
            capture_station();
            translate(locker_pos) locker_cabinet();
            surroundings();
        }
        translate([-2000, t + 1, -100]) cube([4000, 2000, 2000]);
    }
} else if (view == "lock_section") {
    intersection() {
        locker_cabinet();
        translate([-50, -80, lock_z(1) - 40]) cube([lk_w + 100, lk_d + 160, 40]);
    }
} else if (view == "camera_mount") {
    print_camera_mount();
} else if (view == "door_tab") {
    print_door_tab();
}
