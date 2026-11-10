// 설계 점검: 치수를 바꿀 때마다 콘솔에 결과를 찍는다

// 시야가 사다리꼴이라 앞쪽 폭이 가장 좁다. 물건의 앞쪽 모서리가 그 안에 들어와야 한다.
function half_width_at(z, y) = let (
        c = fov_corners(z),
        near = c[0][1], far = c[2][1],
        hn = (c[1][0] - c[0][0]) / 2, hf = (c[2][0] - c[3][0]) / 2)
    hn + (hf - hn) * (y - near) / (far - near);

function item_in_view() = let (
        c = fov_corners(t + 1 + item_h),
        y0 = sample_y() - item_d / 2,
        y1 = sample_y() + item_d / 2)
    y0 >= c[0][1] && y1 <= c[2][1] && item_w / 2 <= half_width_at(t + 1 + item_h, y0);

function fits_cell() = item_w <= cell_w - 20 - tab_gap - tab_t && item_h <= cell_h - 14 && item_d <= cell_d - tab_len;

module design_report() {
    floor_c = fov_corners(t + 1);
    top_c = fov_corners(t + 1 + item_h);
    need = path_length(ribbon_path()) + 10;
    door_w = lk_w - 1 - door_left;
    echo(str("촬영 모듈 바깥 치수 ", cap_w, " x ", cap_d, " x ", cap_h));
    echo(str("보관함 모듈 바깥 치수 ", lk_w, " x ", lk_d - front_y, " x ", lk_h));
    echo(str("렌즈 높이 (바닥에서) ", round(lens_pos()[2] - t - 1)));
    echo(str("바닥 촬영 범위: 앞뒤 ", round(floor_c[0][1]), " ~ ", round(floor_c[2][1]),
             ", 폭 ", round(floor_c[1][0] - floor_c[0][0]), " ~ ", round(floor_c[2][0] - floor_c[3][0])));
    echo(str("물건 윗면 높이 촬영 범위: 앞뒤 ", round(top_c[0][1]), " ~ ", round(top_c[2][1]),
             ", 폭 ", round(top_c[1][0] - top_c[0][0]), " ~ ", round(top_c[2][0] - top_c[3][0])));
    echo(str("물건이 시야에 다 들어온다: ", item_in_view()));
    echo(str("물건이 보관함 칸에 들어간다: ", fits_cell()));
    echo(str("리본 케이블 필요 길이 ", round(need), ", 보유 ", ribbon_len, ", 충분: ", need <= ribbon_len));
    echo(str("문 폭 ", round(door_w), ", 문이 호를 그릴 때 걸쇠판 끝이 칸막이 쪽으로 파고드는 최대 거리 ",
             round(pow(tab_len + 3, 2) / (2 * (lk_w - tab_x)) * 10) / 10, ", 여유 ", tab_gap));
    echo(str("걸쇠판이 걸쇠를 받치는 폭 ", tab_len - 1 - lock_y - 8));
    echo(str("걸쇠가 걸쇠판을 넘어가는 길이 ", lock_latch - t - tab_gap - tab_t));
    echo(str("리드 스위치와 자석 간격 ", tab_gap + tab_t, " (판매처 표기 최소 작동 거리 14)"));
    echo(str("배선 길이: HDMI ", round(path_length(hdmi_path())), ", 키보드 ", round(path_length(keyboard_path())),
             ", 마우스 ", round(path_length(mouse_path()))));
    echo(str("책상에 필요한 폭 ", round(locker_pos[0] + lk_w - monitor_x * 2 + 40)));
}

// build.py가 설명 그림에 글자를 넣을 위치
module label_points() {
    bay = bay_floor_z + 14;
    lk = lk_bay_z + 12;
    fov_mid = (lens_pos() + fov_corners(t + 1)[2]) / 2;
    points = [
        ["lens", lens_pos()], ["mount", pivot + [0, 0, 8]], ["ribbon", [cam_x, slot_y, roof_z + 3]],
        ["pi", [pi_x - 28, pi_y + 42, bay]], ["light", [t + 14, 200, roof_z - 4]],
        ["item", [cam_x, sample_y(), t + item_h / 2]], ["fov", fov_mid], ["mat", [cam_x, 30, t + 1]],
        ["header", [cam_x, 3, roof_z - 25]], ["slot", [cam_x, slot_y, bay]], ["cable_out", [cam_x, cap_d - 3, bay]],
        ["light_hole", [cap_w - 40, slot_y, bay]], ["mount_screw", [cam_x + 22, cam_y, bay]],
        ["lock1", [x_part - 27, lock_y, lock_z(1)]], ["reed1", [x_cell - 6, lock_y, lock_z(1) + reed_dz]],
        ["col_wire", [t + 25, 62, lock_z(1) + 70]], ["lock2", [x_part - 27, lock_y, lock_z(2)]],
        ["lk_pi", [lk_w - t - 90, 112, lk]], ["board", [lk_w - t - 62 - 56 - 52, 112, lk]],
        ["relay", [45, 150, lk]], ["jack", [50, lk_d - 16, lk]], ["col_hole", [t + lock_col_w / 2, 60, lk]],
        ["partition", [x_part + 3, 120, lk]], ["latch", [x_cell + 6, lock_y, lk]], ["tab", [tab_x + 1, 31, lk]],
        ["door", [150, front_y + 3, lk]], ["sol", [x_part - 27, lock_y, lk]],
        // 전체 그림용: 보관함 위치까지 더한 전체 좌표
        ["w_monitor", [monitor_x, 190, 260]], ["w_keyboard", [monitor_x, 0, 20]], ["w_mouse", [monitor_x + 245, 0, 20]],
        ["w_strip", strip_pos + [160, 28, 30]], ["w_light", [t + 14, 100, roof_z - 4]],
        ["w_lock1", locker_pos + [x_part - 27, lock_y, lock_z(1)]], ["w_lock2", locker_pos + [x_part - 27, lock_y, lock_z(2)]],
        ["w_reed1", locker_pos + [x_cell - 6, lock_y, lock_z(1) + reed_dz]],
        ["w_lk_pi", locker_pos + [lk_w - t - 90, 112, lk]], ["w_board", locker_pos + [lk_w - t - 62 - 56 - 52, 112, lk]],
        ["w_relay", locker_pos + [45, 150, lk]], ["w_cell1", locker_pos + [x_cell + cell_w / 2, 120, cell_z(1) + cell_h / 2]],
        ["w_cell2", locker_pos + [x_cell + cell_w / 2, 120, cell_z(2) + cell_h / 2]],
        ["w_door1", locker_pos + [lk_w + 25, -140, 300]],
        ["cap_left", [0, 0, -20]], ["cap_right", [cap_w, 0, -20]], ["cap_top", [cap_w / 2, 0, cap_h]],
        ["lk_left", locker_pos + [0, 0, -20]], ["lk_right", locker_pos + [lk_w, 0, -20]], ["lk_top", locker_pos + [lk_w / 2, 0, lk_h]],
        ["cap_size", [cap_w, cap_d, cap_h]], ["lk_size", [lk_w, lk_d - front_y, lk_h]], ["cell_size", [cell_w, cell_d, cell_h]]
    ];
    for (p = points) echo(str("LABEL|", p[0], "|", p[1]));
}
