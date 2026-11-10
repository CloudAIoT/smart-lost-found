// 배선 경로와 주변 장비

// 점들을 차례로 잇는 선
module cable(points, d, c) {
    color(c) for (i = [0:len(points) - 2]) hull() {
        translate(points[i]) sphere(d = d, $fn = 8);
        translate(points[i + 1]) sphere(d = d, $fn = 8);
    }
}

function path_length(p, i = 0) =
    i >= len(p) - 1 ? 0 : norm(p[i + 1] - p[i]) + path_length(p, i + 1);

// 카메라 단자에서 촬영 Pi 카메라 단자까지
function ribbon_path() = let (
        z = bay_floor_z + ribbon_z,
        end_y = pi_y + 46.5) [
    pivot + tilt_point([0, board_y + 23.9, plate_top + 2.5]),
    pivot + tilt_point([0, board_y + 31.9, plate_top + 2.5]),
    [cam_x, slot_y, roof_z - 6],
    [cam_x, slot_y, z],
    [cam_x, end_y, z],
    [cam_x, end_y, bay_floor_z + standoff + 7]
];

module capture_cables() {
    cable(ribbon_path(), 2, "#ff6f00");
    // 조명 전원: USB에서 천장 구멍을 지나 양쪽 조명으로
    usb_x = pi_x - 18;
    cable([[usb_x, pi_y + 90, bay_floor_z + 16], [usb_x, pi_y + 112, bay_floor_z + 16],
           [cap_w - 40, pi_y + 112, bay_floor_z + 16], [cap_w - 40, slot_y, bay_floor_z + 6],
           [cap_w - 40, slot_y, roof_z - 6], [cap_w - 21, slot_y, roof_z - 6]], 3, "#fbc02d");
    cable([[cap_w - 40, slot_y, roof_z - 6], [34, slot_y, roof_z - 6]], 3, "#fbc02d");
}

// 카메라 쪽을 뺀 나머지는 모듈 밖으로 나가므로 전체 좌표로 그린다
monitor_x = -(monitor_inch * 25.4 * 0.8716 + 14) / 2 - 50;
strip_pos = [cap_w / 2 - 160, cap_d + 40, 0];

module surroundings() {
    span_l = monitor_x * 2 - 60;
    span_r = locker_pos[0] + lk_w + 60;
    color("#d7ccc8") translate([span_l, -80, -20]) cube([span_r - span_l, cap_d + 200, 20]);
    translate([monitor_x, 70, 0]) monitor(monitor_inch);
    translate([monitor_x - 240, -70, 0]) keyboard();
    translate([monitor_x + 215, -55, 0]) mouse();
    translate(strip_pos) power_strip();
}

rear = cap_d + 14;

// 모니터 영상: Pi 오른쪽 HDMI에서 뒤로 나가 모니터로
function hdmi_path() = [
    [pi_x + 8, pi_y + 26, bay_floor_z + 12], [pi_x + 34, pi_y + 26, bay_floor_z + 12],
    [pi_x + 34, rear, bay_floor_z + 20], [pi_x + 34, rear, 40], [monitor_x, rear, 40], [monitor_x, 215, 160]
];

// 키보드: USB에서 뒤로 나가 왼쪽 앞으로
function keyboard_path() = [
    [pi_x - 27, pi_y + 90, bay_floor_z + 16], [pi_x - 27, rear, bay_floor_z + 20], [pi_x - 27, rear, 6],
    [-20, rear, 6], [-20, 40, 6], [monitor_x + 180, 66, 6], [monitor_x + 100, 66, 6]
];

function mouse_path() = [
    [pi_x - 45, pi_y + 90, bay_floor_z + 16], [pi_x - 45, rear - 8, bay_floor_z + 20], [pi_x - 45, rear - 8, 3],
    [-30, rear - 8, 3], [-30, 90, 3], [monitor_x + 245, 90, 3], [monitor_x + 245, 45, 8]
];

module room_cables() {
    z = bay_floor_z + 20;
    sy = strip_pos[1];
    cable(hdmi_path(), 5, "#212121");
    cable(keyboard_path(), 3, "#616161");
    cable(mouse_path(), 3, "#9e9e9e");
    // 전원: 멀티탭에서 각 장치로
    cable([[pi_x + 6, pi_y + 11, bay_floor_z + 12], [pi_x + 24, pi_y + 11, bay_floor_z + 12], [pi_x + 24, rear - 2, z],
           [pi_x + 24, rear - 2, 30], [pi_x + 24, sy, 30]], 4, "#c62828");
    cable([[monitor_x, 215, 60], [monitor_x, sy + 28, 12], [strip_pos[0], sy + 28, 12]], 4, "#c62828");
    lx = locker_pos[0];
    cable([[lx + lk_w - t - 62 + 8, 81, lk_bay_z + 12], [lx + lk_w - 34, 81, lk_bay_z + 12], [lx + lk_w - 34, lk_d + 16, lk_bay_z + 12],
           [lx + lk_w - 34, lk_d + 16, 20], [strip_pos[0] + 320, sy + 28, 20]], 4, "#c62828");
    cable([[lx + 50, lk_d + 6, lk_bay_z + 30], [lx + 50, lk_d + 30, lk_bay_z + 30], [lx + 50, lk_d + 30, 20],
           [strip_pos[0] + 320, sy + 14, 20]], 4, "#e53935");
}

module locker_cables() {
    z = lk_bay_z;
    hole = [t + lock_col_w / 2, 60];
    // 잠금장치 2개, 리드 스위치 2개, LED 2개 배선이 기둥을 타고 위로 올라간다
    for (n = [1, 2]) {
        cable([[x_part - lock_body[0], lock_y, lock_z(n)], [hole[0] - 12, lock_y + 40, lock_z(n)], [hole[0] - 6, hole[1], z + 10],
               [45, 130, z + 14]], 2.5, "#c62828");
        cable([[x_part - 8, lock_y, lock_z(n) + reed_dz], [hole[0], lock_y + 30, lock_z(n) + reed_dz], [hole[0], hole[1], z + 10],
               [140, 120, z + 16]], 2, "#1565c0");
        cable([[x_part / 2, 1, lock_z(n) - 28], [hole[0] + 6, lock_y + 30, lock_z(n) - 28], [hole[0] + 6, hole[1], z + 10],
               [150, 130, z + 16]], 2, "#2e7d32");
    }
    // 보관함 Pi GPIO에서 브레드보드와 릴레이로
    gx = lk_w - t - 62 - 54;
    cable([[gx, 102, z + 19], [gx - 24, 102, z + 26], [gx - 42, 110, z + 18]], 6, "#78909c");
    cable([[138, 110, z + 16], [100, 145, z + 14], [72, 145, z + 12]], 3, "#7b1fa2");
    // 12V 입력에서 릴레이로
    cable([[50, lk_d - t - 10, z + 30], [50, 175, z + 16], [50, 165, z + 12]], 3, "#e53935");
}
