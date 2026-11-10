// 실제 장치 모양
// Pi 4와 카메라는 공식 기계 도면 치수이고, 나머지는 판매처 표기 기준이라 구매 후 실측한다.

pi_holes = [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]];

// 원점은 보드 아랫면 모서리. X는 SD 카드 쪽에서 USB 쪽으로, Y는 전원 쪽에서 GPIO 쪽으로 간다.
module pi4() {
    top = 1.4;
    color("#2e7d32") difference() {
        linear_extrude(top) translate([3, 3]) offset(3) square([79, 50]);
        for (p = pi_holes) translate([p[0], p[1], -1]) cylinder(d = 2.7, h = 4);
    }
    color("#222222") translate([7, 50, top]) cube([51, 5, 8.5]);              // GPIO 40핀
    color("#c0c0c0") {
        for (y = [9, 27]) translate([70.6, y - 6.6, top]) cube([17.5, 13.2, 16]);  // USB
        translate([66.8, 45.75 - 7.8, top]) cube([21.3, 15.6, 13.5]);         // 이더넷
        translate([6.8, -1, top]) cube([8.8, 7.3, 3.2]);                      // USB-C 전원
        for (x = [26, 39.5]) translate([x - 3.1, -0.5, top]) cube([6.2, 7, 3]);    // micro HDMI
    }
    color("#222222") translate([50.5, -2.5, top]) cube([7, 15, 6]);           // 오디오
    color("#f0f0f0") translate([45, 0.2, top]) cube([3, 22.6, 5.5]);          // 카메라 단자
    color("#b87333") translate([22, 25.5, top + 1]) cube([14, 14, 6]);        // 구리 방열판
}

// 원점은 보드 앞면 모서리. 렌즈가 +Z를 보고, 리본 단자는 Y가 큰 쪽 뒷면에 있다.
module camera_module3() {
    color("#2e7d32") difference() {
        translate([0, 0, -1.12]) cube([25, 23.86, 1.12]);
        for (x = [2, 23], y = [2, 14.5]) translate([x, y, -2]) cylinder(d = 2.2, h = 3);
    }
    color("#222222") translate([7.1, 9, 0]) cube([10.8, 10.8, 3.9]);
    color("#111111") translate([12.5, 14.4, 3.9]) cylinder(d = 8, h = 3.1);
    color("#f0f0f0") translate([2.5, 18.2, -4.4]) cube([20, 5.7, 3.3]);
}

// 원점은 걸쇠가 나오는 면의 가운데. 몸체는 -X로 뻗고 빗면 걸쇠는 +X로 나온다.
module solenoid_lock() {
    color("#c9a227") translate([-lock_body[0], -lock_body[2] / 2, -lock_body[1] / 2])
        cube([lock_body[0], lock_body[2], lock_body[1]]);
    color("#e0e0e0") translate([0, 0, -6]) linear_extrude(12)
        polygon([[0, -6], [0, 6], [lock_latch, 6], [lock_latch - 6, -6]]);
}

// 문 센서: 판매처 표기 약 27 x 14 x 8
module reed_body() {
    color("#f5f5f5") cube([8, 27, 14]);
}

// 2채널 릴레이 모듈: 판매처 표기 약 50 x 38 x 19
module relay_2ch() {
    color("#1565c0") cube([50, 38, 2]);
    color("#0d47a1") for (x = [6, 28]) translate([x, 8, 2]) cube([16, 19, 16]);
}

// 키트의 브레드보드 (반 크기 기준)
module breadboard() {
    color("#fafafa") cube([55, 85, 9]);
    color("#222222") translate([18, 12, 9]) cube([19, 56, 8]);    // T자 확장보드
}

// 12V 전원 입력 단자
module dc_jack() {
    color("#222222") rotate([-90, 0, 0]) cylinder(d = 11, h = 14);
}

module led_bar(len) {
    color("#fff59d") cube([len, 12, 8]);
}

// 원점은 받침대 앞쪽 가운데, 책상 위
module monitor(inch) {
    w = inch * 25.4 * 0.8716 + 14;
    h = inch * 25.4 * 0.4903 + 22;
    color("#333333") {
        translate([-110, 0, 0]) cube([220, 180, 16]);
        translate([-30, 120, 0]) cube([60, 24, 200]);
    }
    translate([0, 110, 95]) rotate([-5, 0, 0]) {
        color("#222222") translate([-w / 2, 0, 0]) cube([w, 24, h]);
        color("#90caf9") translate([-w / 2 + 7, -0.5, 11]) cube([w - 14, 1, h - 22]);
    }
}

module keyboard() {
    color("#424242") cube([440, 135, 22]);
}

module mouse() {
    color("#424242") hull() {
        translate([30, 30, 8]) scale([1, 1.6, 0.8]) sphere(r = 30);
        translate([0, 0, 0]) cube([60, 100, 1]);
    }
}

module power_strip() {
    color("#eeeeee") cube([320, 56, 38]);
}
