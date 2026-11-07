"""3D 모델에서 STL과 설명 그림을 다시 만든다.

실행: python3 build.py   (OpenSCAD와 Pillow가 필요하다)
"""

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
MODEL = HERE / "kiosk.scad"
SIZE = (1400, 1000)
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

STLS = {
    "kiosk_all": {"show_cables": "false", "show_fov": "false"},
    # 뚜껑과 가까운 벽을 빼고 두 모듈을 벌려서 돌려 보면 안쪽 장치가 보인다
    "kiosk_inside": {"cutaway": "true", "show_cables": "false", "show_fov": "false", "module_gap": 450},
    "capture_station": {"view": '"capture"', "show_cables": "false", "show_fov": "false"},
    "locker_cabinet": {"view": '"locker"', "show_cables": "false", "door1_angle": 0},
    "camera_mount": {"view": '"camera_mount"'},
    "door_tab": {"view": '"door_tab"'},
}

X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
SIZE_KEYS = ("cap_size", "lk_size", "cell_size")
MINUS_Y = (0, -1, 0)

# axes는 그림의 오른쪽과 위쪽이 모델의 어느 방향인지 나타낸다. 정사영 그림에만 글자를 넣는다.
RENDERS = {
    "overview": {"camera": (900, -2900, 1500, 60, 130, 170), "options": {"show_cables": "false", "show_fov": "false"}},
    "summary_look": {
        "size": (1000, 640),
        "camera": (760, -2250, 1180, 50, 110, 190),
        "options": {"show_cables": "false", "show_fov": "false"},
    },
    "summary_front": {
        "size": (2000, 720),
        "camera": (54, -1318, 205, 54, 130, 205),
        "options": {"view": '"front_section"'},
        "axes": (X, Z),
        "labels": [("cap_top", "촬영 모듈 {cap_size}", 0, -34), ("lk_top", "보관함 모듈 {lk_size}", 0, -34),
                   ("w_monitor", "모니터 (촬영 Pi에 연결)", 0, 0), ("w_keyboard", "키보드 (단면)", 0, -34),
                   ("pi", "촬영 Pi", -130, -8), ("ribbon", "리본 케이블", 150, -46),
                   ("lens", "카메라", 150, 66), ("w_light", "LED 조명", 62, 58), ("item", "최대 크기 물건 220 × 140 × 100", 0, 20),
                   ("w_lk_pi", "보관함 Pi", 40, -56), ("w_board", "브레드보드", -30, -56), ("w_relay", "릴레이", -30, -56),
                   ("w_reed1", "리드 스위치 (문 센서)", 190, -34), ("w_lock1", "잠금장치", 100, 48), ("w_lock2", "잠금장치", 100, -10),
                   ("w_cell1", "1번 칸 {cell_size}", 30, 0), ("w_cell2", "2번 칸", 30, 0)],
        "dims": [("cap_left", "cap_right", 34), ("lk_left", "lk_right", 34)],
    },
    "summary_plan": {
        "size": (1000, 640),
        "camera": (54, 80, 2520, 54, 80, 0),
        "options": {"cutaway": "true", "show_fov": "false"},
        "axes": (X, Y),
        "labels": [("w_monitor", "모니터", 0, -60), ("w_keyboard", "키보드", -40, 74), ("w_mouse", "마우스", 40, 88),
                   ("pi", "촬영 Pi", -30, -96), ("w_lk_pi", "보관함 Pi", 30, -96), ("w_strip", "멀티탭", 130, -24),
                   ("w_door1", "1번 문이 열리는 쪽", -150, 36)],
    },
    "rear_cables": {"camera": (-700, 2500, 1300, 60, 130, 150), "options": {"cutaway": "true", "show_fov": "false"}},
    "capture_side": {
        "camera": (1400, 150, 230, 176, 150, 230),
        "options": {"view": '"capture"', "cutaway": "true"},
        "axes": (Y, Z),
        "labels": [("header", "앞쪽 가림판", -70, -30), ("mount", "카메라 거치대", -150, -70), ("lens", "카메라", -210, 60),
                   ("ribbon", "리본 케이블", 90, 150), ("pi", "촬영 Pi", 170, -130), ("light", "LED 조명", 170, 70),
                   ("fov", "촬영 범위", 150, -40), ("item", "최대 크기 물건", 230, 20), ("mat", "무늬 바닥판", -120, -60)],
    },
    "capture_top": {
        "camera": (176, 152, 1250, 176, 152, 0),
        "options": {"view": '"capture"', "cutaway": "true", "show_fov": "false"},
        "axes": (X, Y),
        "labels": [("pi", "촬영 Pi", -290, -70), ("slot", "리본 구멍 (아래에 카메라)", -270, 50), ("cable_out", "뒤쪽 배선 구멍", 200, 50),
                   ("light_hole", "조명 배선 구멍", 40, -90), ("mount_screw", "거치대 나사 구멍", 200, 60)],
    },
    "locker_inside": {
        "camera": (-1400, 120, 220, 170, 120, 220),
        "options": {"view": '"locker"', "cutaway": "true", "door1_angle": 0},
        "axes": (MINUS_Y, Z),
        "labels": [("reed1", "리드 스위치 (문 센서)", 240, 40), ("lock1", "잠금장치 1", 240, 40), ("lock2", "잠금장치 2", 240, 20),
                   ("col_wire", "기둥 배선", -230, 0)],
    },
    "locker_top": {
        "camera": (170, 120, 1250, 170, 120, 0),
        "options": {"view": '"locker"', "cutaway": "true", "door1_angle": 0},
        "axes": (X, Y),
        "labels": [("lk_pi", "보관함 Pi", 230, 40), ("board", "브레드보드", 20, 230), ("relay", "2채널 릴레이", -170, -120),
                   ("jack", "12V 입력", 170, -60), ("col_hole", "기둥 배선 구멍", -200, 90)],
    },
    "lock_section": {
        "camera": (120, 40, 560, 120, 40, 0),
        "options": {"view": '"lock_section"', "door1_angle": 0, "show_cables": "false"},
        "axes": (X, Y),
        "labels": [("sol", "잠금장치 몸체", -40, 190), ("latch", "빗면 걸쇠", 190, 150), ("tab", "걸쇠판", 330, 90),
                   ("partition", "칸막이", -60, -100), ("door", "문", 300, 110)],
    },
}


def run_openscad(output, options):
    command = [shutil.which("openscad") or "openscad", "-o", str(output), "--backend=manifold", "--render=true"]
    for key, value in options.items():
        command += ["-D", f"{key}={value}"]
    return command + [str(MODEL)]


def execute(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("openscad를 찾지 못했습니다. OpenSCAD를 설치하세요.")
    except subprocess.CalledProcessError as error:
        sys.exit(f"OpenSCAD 실행 실패:\n{error.stderr}")
    return result.stderr


def label_points(log):
    points = {}
    for key, text in re.findall(r'LABEL\|(\w+)\|\[(.+?)\]"', log):
        points[key] = tuple(float(value) for value in text.split(","))
    return points


def to_pixel(point, render):
    camera, axes = render["camera"], render["axes"]
    width, height = render.get("size", SIZE)
    eye, center = camera[:3], camera[3:]
    scale = height / (2 * math.tan(math.radians(22.5 / 2)) * math.dist(eye, center))
    offset = [p - c for p, c in zip(point, center)]
    right = sum(o * a for o, a in zip(offset, axes[0]))
    up = sum(o * a for o, a in zip(offset, axes[1]))
    return width / 2 + right * scale, height / 2 - up * scale


def size_text(values):
    return " × ".join(f"{value:g}" for value in values) + "mm"


def text_box(draw, position, text, font):
    box = draw.textbbox(position, text, font=font, anchor="mm")
    draw.rectangle([box[0] - 8, box[1] - 5, box[2] + 8, box[3] + 5], fill="white", outline="#d32f2f")
    draw.text(position, text, font=font, fill="#212121", anchor="mm")


def draw_labels(path, render, points):
    font = ImageFont.truetype(FONT, 26 if "size" not in render else 22)
    sizes = {key: size_text(points[key]) for key in SIZE_KEYS}
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for key, text, dx, dy in render["labels"]:
        x, y = to_pixel(points[key], render)
        draw.line([(x, y), (x + dx, y + dy)], fill="#d32f2f", width=2)
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill="#d32f2f")
        text_box(draw, (x + dx, y + dy), text.format(**sizes), font)
    for start, end, drop in render.get("dims", []):
        x0, y0 = to_pixel(points[start], render)
        x1, _ = to_pixel(points[end], render)
        y = y0 + drop
        draw.line([(x0, y), (x1, y)], fill="#1565c0", width=2)
        for x in (x0, x1):
            draw.line([(x, y - 9), (x, y + 9)], fill="#1565c0", width=2)
        text_box(draw, ((x0 + x1) / 2, y), f"폭 {math.dist(points[start], points[end]):g}mm", font)
    image.save(path)


def compose_summary(render_dir):
    title_font = ImageFont.truetype(FONT, 40)
    head_font = ImageFont.truetype(FONT, 26)
    sheet = Image.new("RGB", (2000, 1500), "#f8f8f8")
    draw = ImageDraw.Draw(sheet)
    draw.text((30, 24), "스마트 분실물 보관함 시제품 배치 (초안)", font=title_font, fill="#212121")
    draw.text((1970, 40), "치수는 폭 × 깊이 × 높이", font=head_font, fill="#455a64", anchor="ra")
    parts = [("summary_look", (0, 130), "전체 모습"), ("summary_plan", (1000, 130), "위에서 본 배치"),
             ("summary_front", (0, 780), "앞에서 자른 단면: 안쪽 장치 위치")]
    for name, position, heading in parts:
        sheet.paste(Image.open(render_dir / f"{name}.png"), position)
        draw.text((position[0] + 30, position[1] - 40), heading, font=head_font, fill="#455a64")
    sheet.save(render_dir / "summary.png")


def main():
    stl_dir, render_dir = HERE / "stl", HERE / "renders"
    stl_dir.mkdir(exist_ok=True)
    render_dir.mkdir(exist_ok=True)
    for name, options in STLS.items():
        command = run_openscad(stl_dir / f"{name}.stl", options)
        execute(command[:1] + ["--export-format=binstl"] + command[1:])
        print("STL", name)
    for name, render in RENDERS.items():
        path = render_dir / f"{name}.png"
        command = run_openscad(path, {"door2_angle": 0, **render["options"]})
        projection = "o" if "axes" in render else "p"
        width, height = render.get("size", SIZE)
        view_args = [f"--imgsize={width},{height}", "--colorscheme=Tomorrow", f"--projection={projection}",
                     "--camera=" + ",".join(str(value) for value in render["camera"])]
        log = execute(command[:1] + view_args + command[1:])
        if "labels" in render:
            draw_labels(path, render, label_points(log))
        print("PNG", name)
    compose_summary(render_dir)
    for name in ("summary_look", "summary_plan", "summary_front"):
        (render_dir / f"{name}.png").unlink()
    print("PNG summary")


if __name__ == "__main__":
    main()
