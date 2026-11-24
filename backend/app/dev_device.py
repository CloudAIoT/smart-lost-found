"""Development stand-in for the Pi, used only when DEV_FAKE_DEVICE=true.

The registration flow needs a camera and a locker to run end to end. Where no Pi is
reachable this gateway answers with bundled dummy JPEGs and an always-closed door, so the
front end can exercise the real contract: the same state transitions, shot ordering, S3
objects and preview URLs. It is selected by an explicit setting, never as a fallback after a
device error. A failed real command must stay failed; turning one into a success would let
production store items in lockers that never opened.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.errors import IntegrationError

ASSETS = Path(__file__).resolve().parent / "dev_assets"


class FakeDeviceGateway:
    """Returns the bundled dummy photo for the requested shot and reports every door closed."""

    def capture(self, kiosk_id: UUID, command_id: str, registration_id: UUID, expires_at: datetime,
                shot_order: int) -> bytes:
        try:
            return (ASSETS / f"fake_capture_{shot_order}.jpg").read_bytes()
        except OSError:
            raise IntegrationError("CAPTURE_NOT_CONFIGURED") from None

    def open(self, kiosk_id: UUID, locker_number: int, command_id: str, expires_at: datetime) -> None:
        return None

    def door(self, kiosk_id: UUID, locker_number: int) -> Literal["open", "closed"]:
        # The deposit and pickup completion steps both require a closed door.
        return "closed"
