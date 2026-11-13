"""키오스크에서 분실물을 등록하는 전체 흐름을 백엔드와 실제 Pi로 확인한다.

흐름: 키오스크 선택 → 등록 시작 → 촬영 1장 → 물건 방향 바꿈 → 촬영 1장 더 →
미리보기 → 분석 → 보관함 개방 → 문 닫힘 확인 → 보관 완료.

촬영 상자에는 잠금장치가 없어 '투입구 개방' 명령은 통신 규약에 없다. 사용자는 상자에
물건을 넣고 촬영을 누른다. 보관함 문만 백엔드 명령으로 열린다.
"""

from uuid import UUID

import pytest
from sqlalchemy import select

from conftest import DOOR_OPEN_SECONDS, door_becomes, jpeg_size


def api(registration_id: str = "") -> str:
    return f"/api/registrations/{registration_id}" if registration_id else "/api/registrations"


def test_user_registers_an_item_and_the_locker_opens(kiosk):
    from app.models import DeviceCommand, FoundItem, ItemImage, Registration, StorageAssignment

    client = kiosk.client

    # 1. 사용자가 키오스크 화면을 열면 키오스크와 분류 목록을 받는다
    kiosks = client.get("/api/kiosks")
    assert kiosks.status_code == 200
    assert kiosk.kiosk_id in [row["id"] for row in kiosks.json()]
    assert client.get("/api/categories").status_code == 200

    # 2. 등록을 시작한다. 아직 사진도 보관함도 없다
    created = client.post(api(), json={"kiosk_id": kiosk.kiosk_id,
                                            "primary_category_id": kiosk.category_id,
                                            "secondary_description": "검은색 지갑"})
    assert created.status_code == 201, created.text
    registration_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["images"] == []
    assert created.json()["locker_number"] is None

    # 3. 물건을 촬영 상자에 넣고 첫 번째 사진을 찍는다. 실제 Pi가 찍어 보낸다
    first = client.post(f"{api(registration_id)}/shots/1")
    assert first.status_code == 200, first.text
    assert [image["shot_order"] for image in first.json()["images"]] == [1]
    assert first.json()["status"] == "draft"

    # 4. 물건을 돌려 놓고 두 번째 사진을 찍는다
    second = client.post(f"{api(registration_id)}/shots/2")
    assert second.status_code == 200, second.text
    assert [image["shot_order"] for image in second.json()["images"]] == [1, 2]

    # Pi가 보낸 사진이 저장소에 그대로 들어갔는지 확인한다
    stored = list(kiosk.store.objects.values())
    assert len(kiosk.store.objects) == 2
    assert all(jpeg_size(data) == (640, 480) for data in stored)
    assert stored[0] != stored[1], "같은 사진이 두 번 저장되었다"

    # 5. 미리보기 주소를 받는다
    for shot in (1, 2):
        preview = client.get(f"{api(registration_id)}/shots/{shot}/preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["url"].startswith("https://")

    # 6. 특징을 분석한다
    analyzed = client.post(f"{api(registration_id)}/analysis")
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["status"] == "ready"
    assert analyzed.json()["analysis_status"] == "succeeded"
    assert kiosk.analyzer.calls == 1

    # 7. 등록을 완료하면 보관함이 열리고, 번호는 열린 뒤에만 화면에 나온다
    opened = client.post(f"{api(registration_id)}/deposit/open")
    assert opened.status_code == 200, opened.text
    number = opened.json()["locker_number"]
    assert number in (1, 2)
    assert opened.json()["status"] == "awaiting_deposit"

    # 보관함 Pi에 직접 물어봐 문이 실제로 열렸는지 본다
    assert kiosk.locker_pi.get(f"/lockers/{number}").json() == {"locker_id": number, "door": "open"}

    # 8. 사용자가 물건을 넣고 문을 닫는다
    door_becomes(kiosk.locker_pi, number, "closed", timeout=DOOR_OPEN_SECONDS + 4)

    # 9. 화면에서 '넣었어요'를 누르면 백엔드가 문 상태를 확인한 뒤 보관을 확정한다
    completed = client.post(f"{api(registration_id)}/deposit/complete", json={"confirmed": True})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "stored"
    assert completed.json()["locker_number"] == number

    # 10. 남은 기록을 확인한다: 명령 3건 모두 성공, 사진 2장, 보관 1건
    with kiosk.Session(kiosk.engine) as session:
        commands = list(session.scalars(select(DeviceCommand).order_by(DeviceCommand.created_at)))
        assert [command.action for command in commands] == ["capture", "capture", "open"]
        assert {command.status for command in commands} == {"succeeded"}
        assert all(command.error_code is None for command in commands)
        assert len({command.command_id for command in commands}) == 3

        images = list(session.scalars(select(ItemImage).order_by(ItemImage.shot_order)))
        assert [image.shot_order for image in images] == [1, 2]
        assert all(image.byte_size == len(kiosk.store.objects[image.object_key]) for image in images)
        assert all(image.received_at is not None for image in images)

        registration = session.get(Registration, UUID(registration_id))
        assert registration.status == "stored"
        assert registration.analysis_input_image_ids == [str(image.id) for image in images]

        assignment = session.scalars(select(StorageAssignment)).one()
        assert assignment.status == "stored"
        assert assignment.deposited_at is not None
        assert assignment.ended_at is None
        assert session.scalars(select(FoundItem)).one().status == "stored"


def test_repeated_screen_taps_do_not_move_hardware_twice(kiosk):
    """사용자가 버튼을 두 번 누르거나 화면을 새로 고쳐도 사진과 개방은 한 번씩만 일어난다."""
    from app.models import DeviceCommand

    client = kiosk.client
    registration_id = client.post(api(), json={"kiosk_id": kiosk.kiosk_id}).json()["id"]
    base = api(registration_id)
    for shot in (1, 2):
        client.post(f"{base}/shots/{shot}")
    client.post(f"{base}/analysis")

    # 같은 촬영을 다시 눌러도 저장된 사진이 그대로 돌아온다
    again = client.post(f"{base}/shots/1")
    assert again.status_code == 200
    assert len(kiosk.store.objects) == 2
    assert client.post(f"{base}/analysis").json()["status"] == "ready"
    assert kiosk.analyzer.calls == 1

    first_open = client.post(f"{base}/deposit/open")
    number = first_open.json()["locker_number"]
    second_open = client.post(f"{base}/deposit/open")
    assert second_open.status_code == 200
    assert second_open.json()["locker_number"] == number

    with kiosk.Session(kiosk.engine) as session:
        commands = list(session.scalars(select(DeviceCommand)))
        assert [command.action for command in commands].count("capture") == 2
        assert [command.action for command in commands].count("open") == 1
        open_command = next(command for command in commands if command.action == "open")

    # 문이 닫힌 뒤 같은 개방 명령이 Pi에 다시 도착해도 보관함은 다시 열리지 않는다
    door_becomes(kiosk.locker_pi, number, "closed", timeout=DOOR_OPEN_SECONDS + 4)
    replay = kiosk.locker_pi.post(f"/lockers/{number}/open",
                                  {"command_id": open_command.command_id,
                                   "expires_at": open_command.expires_at.isoformat()})
    assert replay.status_code == 200
    assert replay.json() == {"locker_id": number, "result": "opened"}
    assert kiosk.locker_pi.get(f"/lockers/{number}").json()["door"] == "closed"


def test_locker_outage_blocks_registration_without_losing_the_locker(kiosk):
    """보관함 Pi가 멈추면 등록은 blocked로 남고, 결과를 모르는 명령은 다시 보내지 않는다."""
    from app.models import DeviceCommand, Registration, StorageAssignment

    client = kiosk.client
    registration_id = client.post(api(), json={"kiosk_id": kiosk.kiosk_id}).json()["id"]
    base = api(registration_id)
    for shot in (1, 2):
        client.post(f"{base}/shots/{shot}")
    assert client.post(f"{base}/analysis").json()["status"] == "ready"

    kiosk.locker_pi.stop()
    failed = client.post(f"{base}/deposit/open")
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] in ("DEVICE_UNREACHABLE", "INVALID_DEVICE_RESPONSE")

    view = client.get(base)
    assert view.json()["status"] == "blocked"
    assert view.json()["locker_number"] is None, "열렸는지 모르는 보관함 번호를 화면에 보여서는 안 된다"

    with kiosk.Session(kiosk.engine) as session:
        command = session.scalars(select(DeviceCommand).where(DeviceCommand.action == "open")).one()
        assert command.status == "unknown"
        assert command.error_code
        # 칸은 회수 정책이 정해질 때까지 예약 상태로 남는다
        assignment = session.scalars(select(StorageAssignment)).one()
        assert assignment.ended_at is None
        assert session.get(Registration, UUID(registration_id)).status == "blocked"

    # blocked 등록은 다시 열어 달라고 해도 진행되지 않는다
    assert client.post(f"{base}/deposit/open").status_code == 409


@pytest.mark.parametrize("swapped", ["capture_url", "locker_url"])
def test_wrong_pi_address_is_caught_before_hardware_moves(kiosk, swapped):
    """설치할 때 두 Pi의 주소가 바뀌어 꽂히면 하드웨어를 움직이기 전에 막는다."""
    client = kiosk.client
    device = kiosk.settings.pi_devices[kiosk.kiosk_id]
    device[swapped] = kiosk.locker_pi.url if swapped == "capture_url" else kiosk.capture_pi.url

    registration_id = client.post(api(), json={"kiosk_id": kiosk.kiosk_id}).json()["id"]
    base = api(registration_id)
    if swapped == "capture_url":
        response = client.post(f"{base}/shots/1")
    else:
        for shot in (1, 2):
            assert client.post(f"{base}/shots/{shot}").status_code == 200
        assert client.post(f"{base}/analysis").status_code == 200
        response = client.post(f"{base}/deposit/open")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "DEVICE_ROLE_MISMATCH"
    assert kiosk.locker_pi.get("/lockers/1").json()["door"] == "closed"
    assert kiosk.locker_pi.get("/lockers/2").json()["door"] == "closed"
