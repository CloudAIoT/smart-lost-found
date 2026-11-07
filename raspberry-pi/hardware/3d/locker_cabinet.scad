// 보관함 모듈: 왼쪽 잠금 기둥, 위아래 칸 2개, 맨 위 보관함 Pi 칸

front_y = -(t + 1);           // 문과 기둥 앞판의 바깥면
hinge_x = lk_w;
hinge_y = front_y + t / 2;
door_left = x_part + 1;       // 문의 왼쪽 끝
tab_x = x_cell + tab_gap;     // 걸쇠판 왼쪽 면

module cabinet_shell() {
    panel([0, 0, 0], [lk_w, lk_d, t]);                                  // 바닥
    if (!cutaway) panel([0, 0, t], [t, lk_d, lk_h - 2 * t]);            // 왼쪽
    difference() {                                                      // 오른쪽: Pi 칸 환기 구멍
        panel([lk_w - t, 0, t], [t, lk_d, lk_h - 2 * t]);
        for (i = [0:5]) translate([lk_w - t - 1, 50 + i * 30, lk_bay_z + 12]) cube([t + 2, 14, locker_bay_h - 24]);
    }
    difference() {                                                      // 뒤: 전원 구멍
        panel([t, cell_d, t], [lk_w - 2 * t, t, lk_h - 2 * t]);
        translate([lk_w - 80, cell_d - 1, lk_bay_z + 8]) cube([50, t + 2, 24]);
        translate([50, cell_d - 1, lk_bay_z + 30]) rotate([-90, 0, 0]) cylinder(d = 12, h = t + 2);
    }
    panel([x_cell, 0, t + cell_h], [cell_w, cell_d, t]);                // 칸 사이 선반
    difference() {                                                      // Pi 칸 바닥: 기둥 배선 구멍
        panel([t, 0, lk_bay_z - t], [lk_w - 2 * t, cell_d, t]);
        translate([t + lock_col_w / 2, 60, lk_bay_z - t - 1]) cylinder(d = 22, h = t + 2);
    }
    difference() {                                                      // 칸막이: 걸쇠 구멍, 리드 스위치 자리
        panel([x_part, 0, t], [t, cell_d, lk_bay_z - 2 * t]);
        for (n = [1, 2]) {
            translate([x_part - 1, lock_y - 8, lock_z(n) - 8]) cube([t + 2, 16, 16]);
            translate([x_part - 1, lock_y - 14, lock_z(n) + reed_dz - 8]) cube([t + 2, 29, 16]);
        }
    }
    panel([0, 0, lk_bay_z], [lk_w, t, locker_bay_h]);                   // Pi 칸 앞판
}

// 기둥 앞판: 번호와 상태 LED가 보이는 곳
module column_front() {
    difference() {
        panel([0, front_y, 0], [x_part, -front_y, lk_bay_z]);
        for (n = [1, 2]) translate([x_part / 2, front_y - 1, lock_z(n) - 28])
            rotate([-90, 0, 0]) cylinder(d = 6, h = -front_y + 2);
    }
}

module column_marks() {
    for (n = [1, 2]) {
        color("#43a047") translate([x_part / 2, front_y + 1, lock_z(n) - 28])
            rotate([90, 0, 0]) cylinder(d = 5.5, h = 3);
        color("#263238") translate([x_part / 2, front_y, lock_z(n) + 4]) rotate([90, 0, 0])
            linear_extrude(1) text(str(n), size = 26, halign = "center", valign = "center");
    }
}

// 문에 붙는 걸쇠판. 걸쇠가 창에 걸리고, 자석이 리드 스위치를 움직인다.
// 원점은 잠금장치 중심 높이에서 문 뒷면과 걸쇠판 왼쪽 면이 만나는 곳
module door_tab_shape() {
    tab_z0 = -14;
    tab_h = reed_dz + 8 - tab_z0;
    difference() {
        union() {
            translate([0, 0, tab_z0]) cube([tab_t, tab_len, tab_h]);
            translate([0, -3, tab_z0]) cube([24, 3, tab_h]);
        }
        translate([-1, lock_y - 8 + 1, -8]) cube([tab_t + 2, 16, 16]);    // 걸쇠 창
        for (z = [tab_z0 + 8, tab_z0 + tab_h - 8])
            translate([16, 1, z]) rotate([90, 0, 0]) cylinder(d = 3.4, h = 6);   // 문 고정 나사
    }
}

module door_assembly(n, angle) {
    z0 = cell_z(n) - t / 2 + 1;
    h = cell_h + t - 2;
    translate([hinge_x, hinge_y, 0]) rotate([0, 0, angle]) translate([-hinge_x, -hinge_y, 0]) {
        color("#1e88e5") panel([door_left, front_y, z0], [lk_w - 1 - door_left, t, h]);
        color("#0d47a1") translate([(door_left + lk_w) / 2, front_y, z0 + h / 2]) rotate([90, 0, 0])
            linear_extrude(1) text(str(n), size = 60, halign = "center", valign = "center");
        color("#37474f") translate([door_left + 24, front_y, lock_z(n)]) rotate([90, 0, 0]) cylinder(d = 16, h = 18);
        translate([tab_x, -1 + 0, lock_z(n)]) color("#ff8f00") door_tab_shape();
        color("#ef5350") translate([tab_x + tab_t, lock_y - 13, lock_z(n) + reed_dz - 7]) cube([8, 27, 14]);   // 자석
    }
    color("#607d8b") for (z = [z0 + 25, z0 + h - 25])                     // 경첩
        translate([hinge_x, hinge_y, z - 15]) cylinder(d = 7, h = 30);
}

// 문을 살짝 밀어 주는 스프링 받침
module door_kicker(n) {
    color("#90a4ae") translate([x_cell + 26, 2, cell_z(n)]) cube([14, 20, 12]);
    color("#c0c0c0") translate([x_cell + 33, 3, cell_z(n) + 6]) rotate([90, 0, 0]) cylinder(d = 6, h = 3);
}

module cell_hardware(n) {
    translate([x_part, lock_y, lock_z(n)]) solenoid_lock();
    translate([x_cell - 8, lock_y - 13, lock_z(n) + reed_dz - 7]) reed_body();
    door_kicker(n);
}

module locker_bay_parts() {
    z = lk_bay_z;
    px = lk_w - t - 62;
    py = 70;
    translate([px, py, z + standoff]) rotate([0, 0, 90]) pi4();
    color("#c9a227") for (p = pi_holes) translate([px - p[1], py + p[0], z]) cylinder(d = 5, h = standoff);
    translate([px - 56 - 80, py, z]) breadboard();
    translate([20, py + 60, z + 6]) relay_2ch();
    translate([50, lk_d - t - 10, z + 30]) dc_jack();
}

module locker_cabinet() {
    color("#eceff1") cabinet_shell();
    if (!cutaway) {
        color("#cfd8dc") column_front();
        column_marks();
        color("#90a4ae") panel([0, 0, lk_h - t], [lk_w, lk_d, t]);
    }
    for (n = [1, 2]) cell_hardware(n);
    door_assembly(1, door1_angle);
    door_assembly(2, door2_angle);
    locker_bay_parts();
    if (show_cables) locker_cables();
}

module print_door_tab() {
    rotate([0, -90, 0]) door_tab_shape();
}
