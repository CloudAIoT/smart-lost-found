// 촬영 모듈: 앞이 열린 촬영 상자, 천장 아래 카메라, 천장 위 촬영 Pi 칸

module panel(pos, size) {
    translate(pos) cube(size);
}

// 회전축 기준 좌표를 기울기만큼 돌린다
function tilt_point(p, a = cam_tilt) =
    [p[0], p[1] * cos(a) - p[2] * sin(a), p[1] * sin(a) + p[2] * cos(a)];

function lens_pos() = pivot + tilt_point([0, board_y + 14.4, plate_top - 7]);

// 카메라 시야의 네 모서리가 높이 z 평면에 닿는 점
function fov_corners(z) = let (c = lens_pos()) [
    for (s = [[-1, -1], [1, -1], [1, 1], [-1, 1]])
        let (d = tilt_point([s[0] * tan(fov_h / 2), s[1] * tan(fov_v / 2), -1]))
        c + d * (c[2] - z) / (-d[2])
];

module capture_shell() {
    panel([0, 0, 0], [cap_w, cap_d, t]);                        // 바닥
    panel([0, 0, t], [t, cap_d, box_h]);                        // 왼쪽
    if (!cutaway) panel([cap_w - t, 0, t], [t, cap_d, box_h]);  // 오른쪽
    panel([t, box_d, t], [box_w, t, box_h]);                    // 뒤
    panel([t, 0, t + box_h - header_h], [box_w, t, header_h]);  // 앞쪽 위 가림판
    difference() {                                              // 천장
        panel([0, 0, roof_z], [cap_w, cap_d, t]);
        translate([cam_x - 12, slot_y - 3, roof_z - 1]) cube([24, 6, t + 2]);    // 리본 구멍
        translate([cap_w - 40, slot_y, roof_z - 1]) cylinder(d = 10, h = t + 2); // 조명 배선 구멍
        for (x = [-22, 22]) translate([cam_x + x, cam_y, roof_z - 1]) cylinder(d = 3.4, h = t + 2);   // 거치대 나사
        for (p = pi_holes) translate([pi_x - p[1], pi_y + p[0], roof_z - 1]) cylinder(d = 2.7, h = t + 2);
    }
}

module capture_bay_walls() {
    z = bay_floor_z;
    panel([0, 0, z], [cap_w, t, bay_h]);                        // 앞
    difference() {                                              // 뒤: 배선 구멍
        panel([0, cap_d - t, z], [cap_w, t, bay_h]);
        translate([cam_x - 70, cap_d - t - 1, z + 8]) cube([140, t + 2, 30]);
    }
    for (x = cutaway ? [0] : [0, cap_w - t]) difference() {     // 양옆: 환기 구멍
        panel([x, t, z], [t, cap_d - 2 * t, bay_h]);
        for (i = [0:5]) translate([x - 1, 70 + i * 30, z + 12]) cube([t + 2, 14, bay_h - 24]);
    }
}

// 천장에 나사로 붙이는 받침과 회전축 귀 2개
module mount_base() {
    difference() {
        union() {
            translate([-26, -14, -3]) cube([52, 28, 3]);
            for (x = [16.5, -20]) hull() {
                translate([x, -9, -4]) cube([3.5, 18, 2]);
                translate([x, 0, -mount_drop]) rotate([0, 90, 0]) cylinder(d = 12, h = 3.5);
            }
        }
        for (x = [-22, 22]) translate([x, 0, -5]) cylinder(d = 3.4, h = 6);      // 천장 고정 나사
        translate([-25, 0, -mount_drop]) rotate([0, 90, 0]) cylinder(d = 3.4, h = 50);  // 회전축 M3
    }
}

// 카메라를 얹는 판. 회전축 나사를 조여 기울기를 고정한다.
module mount_plate() {
    plate_len = board_y + 25;
    difference() {
        union() {
            hull() {
                translate([-16, 0, 0]) rotate([0, 90, 0]) cylinder(d = 10, h = 32);
                translate([-16, -5, plate_top - 3]) cube([32, 10, 3]);
            }
            translate([-16, -5, plate_top - 3]) cube([32, plate_len + 5, 3]);
        }
        translate([-17, 0, 0]) rotate([0, 90, 0]) cylinder(d = 3.4, h = 34);
        translate([-8, board_y + 5, plate_top - 4]) cube([16, 19, 5]);           // 렌즈 창
        for (x = [-10.5, 10.5], y = [2, 14.5])                                   // M2 나사 구멍
            translate([x, board_y + y, plate_top - 4]) cylinder(d = 2.2, h = 5);
    }
}

module camera_assembly() {
    translate([cam_x, cam_y, roof_z]) color("#ff8f00") mount_base();
    translate(pivot) rotate([cam_tilt, 0, 0]) {
        color("#ff8f00") mount_plate();
        translate([12.5, board_y, plate_top]) rotate([0, 180, 0]) camera_module3();
    }
}

module capture_bay_parts() {
    translate([pi_x, pi_y, bay_floor_z + standoff]) rotate([0, 0, 90]) pi4();
    color("#c9a227") for (p = pi_holes)
        translate([pi_x - p[1], pi_y + p[0], bay_floor_z]) cylinder(d = 5, h = standoff);
}

module capture_lights() {
    for (x = [t + 8, cap_w - t - 20]) translate([x + 12, 30, roof_z - 8]) rotate([0, 0, 90]) led_bar(box_d - 60);
}

// 촬영 상자 바닥: 초점이 잘 맞게 무늬가 있는 판을 깐다
module photo_mat() {
    color("#cfd8dc") panel([t, 0, t], [box_w, box_d, 1]);
    color("#78909c") for (x = [20:40:box_w - 10], y = [20:40:box_d - 10])
        translate([t + x, y, t + 1]) cylinder(d = 8, h = 0.4);
}

function sample_y() = let (c = fov_corners(item_h)) (c[0][1] + c[2][1]) / 2;

module sample_item() {
    color("#ef5350") translate([cam_x - item_w / 2, sample_y() - item_d / 2, t + 1.4]) cube([item_w, item_d, item_h]);
}

// 카메라 시야: 네 모서리 선과 바닥에 닿는 범위. 상자 밖으로 나가는 부분은 자른다.
module fov_lines() {
    c = fov_corners(t + 1.5);
    intersection() {
        union() {
            for (p = c) cable([lens_pos(), p], 1.5, "#f9a825");
            color("#fff59d") translate([0, 0, t + 1.5])
                linear_extrude(0.3) polygon([for (p = c) [p[0], p[1]]]);
        }
        panel([t, 0, t], [box_w, box_d, box_h]);
    }
}

module capture_station() {
    color("#eceff1") capture_shell();
    color("#b0bec5") capture_bay_walls();
    if (!cutaway) color("#90a4ae") panel([0, 0, bay_floor_z + bay_h], [cap_w, cap_d, t]);
    photo_mat();
    capture_lights();
    camera_assembly();
    capture_bay_parts();
    if (show_fov) {
        sample_item();
        fov_lines();
    }
    if (show_cables) capture_cables();
}

// 3D 프린터로 뽑을 때의 자세: 넓은 면을 바닥에 둔다
module print_camera_mount() {
    rotate([180, 0, 0]) mount_base();
    translate([70, 0, -(plate_top - 3)]) mount_plate();
}
