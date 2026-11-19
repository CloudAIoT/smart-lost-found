"""센서 상태와 경고를 MQTT로 보낸다 (결정 기록 0004, docs/mqtt-protocol.md).

토픽은 모두 slf/{키오스크 ID}/ 아래에 둔다. 브로커 주소가 없으면 로그로만 남긴다.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.config import Settings

log = logging.getLogger("pi")


def now_text() -> str:
    # 시간대가 붙은 지금 시각. 예: 2026-11-19T15:20:00+09:00
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Publisher(Protocol):
    def publish(self, path: str, payload: dict, retain: bool = False) -> None: ...


class LogPublisher:
    """브로커 없이 쓸 때: 보낼 내용을 로그로만 남긴다."""

    def __init__(self, prefix: str):
        self.prefix = prefix

    def publish(self, path: str, payload: dict, retain: bool = False) -> None:
        log.info("MQTT(브로커 없음) %s/%s %s", self.prefix, path, json.dumps(payload, ensure_ascii=False))


class MqttPublisher:
    """paho-mqtt로 브로커에 보낸다. 끊기면 다시 붙고, 붙을 때마다 상태를 다시 보낸다."""

    def __init__(self, settings: Settings, client=None):
        import paho.mqtt.client as mqtt  # MQTT를 쓸 때만 불러온다

        self.prefix = f"slf/{settings.kiosk_id}"
        self.status_path = f"{settings.role}/status"
        self.host = settings.mqtt_host
        self.port = settings.mqtt_port
        self.on_connected: list[Callable[[], None]] = []  # 붙을 때마다 상태를 다시 보낼 함수
        self.client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                            client_id=f"slf-{settings.role}-{settings.kiosk_id}")
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password.get_secret_value())
        # Pi가 갑자기 꺼지거나 네트워크가 끊기면 브로커가 대신 offline을 보낸다 (유언 메시지)
        self.client.will_set(self.topic(self.status_path), json.dumps({"status": "offline"}), qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def topic(self, path: str) -> str:
        return f"{self.prefix}/{path}"

    def start(self) -> None:
        # 브로커가 아직 없어도 기다리지 않는다. 연결과 재연결은 paho의 스레드가 맡는다
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        # 정상 종료는 유언 메시지가 나가지 않으므로 offline을 직접 보낸다
        info = self.publish(self.status_path, {"status": "offline", "at": now_text()}, retain=True)
        try:
            if info is not None and self.client.is_connected():
                info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            log.warning("MQTT offline 메시지를 다 보내지 못하고 끝냅니다")
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, path: str, payload: dict, retain: bool = False):
        info = self.client.publish(self.topic(path), json.dumps(payload, ensure_ascii=False), qos=1, retain=retain)
        if info.rc != 0:
            log.warning("MQTT 보내기 실패(rc=%s): %s", info.rc, path)
        return info

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("MQTT 연결 실패: %s", reason_code)
            return
        log.info("MQTT 연결됨: %s:%s", self.host, self.port)
        self.publish(self.status_path, {"status": "online", "at": now_text()}, retain=True)
        for resend in self.on_connected:
            try:
                resend()
            except Exception:
                log.exception("상태를 다시 보내지 못했습니다")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.warning("MQTT 연결 끊김: %s", reason_code)
