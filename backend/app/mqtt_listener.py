"""Subscribe to the device broker and hand every message to app.device_telemetry.

The subscriber lives inside the API container rather than in its own process: this server is a
t3.micro and a second Python runtime would not fit next to PostgreSQL and the API.

Alerts are not retained by the broker, so a deployment that replaced this container would lose
every alert published while it was down. A fixed client id with `clean_session=False` is what
prevents that: the broker keeps the subscription and queues QoS 1 messages until we return. For
that to hold, two backends must never share one client id.
"""

import logging

from app.config import Settings
from app.db import SessionLocal
from app.device_telemetry import TOPIC_FILTERS, apply_message, prune_alerts

log = logging.getLogger("app.mqtt")
QOS = 1
KEEPALIVE_SECONDS = 30


class DeviceTelemetryListener:
    """Owns the paho client and its background thread. Start and stop come from the app lifespan."""

    def __init__(self, settings: Settings, client=None, sessions=SessionLocal):
        import paho.mqtt.client as mqtt  # Imported only when a broker is configured.

        self.host = settings.mqtt_host
        self.port = settings.mqtt_port
        self.retention_days = settings.mqtt_alert_retention_days
        self.sessions = sessions
        self.client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                            client_id=settings.mqtt_client_id, clean_session=False)
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username,
                                        settings.mqtt_password.get_secret_value())
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def start(self) -> None:
        # Never block startup on the broker; paho's thread owns connecting and reconnecting.
        with self.sessions() as session:
            removed = prune_alerts(session, self.retention_days)
        if removed:
            log.info("보관 기간이 지난 경고 %d건을 지웠습니다", removed)
        self.client.connect_async(self.host, self.port, keepalive=KEEPALIVE_SECONDS)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("MQTT 브로커 연결 실패: %s", reason_code)
            return
        log.info("MQTT 브로커 연결됨: %s:%s", self.host, self.port)
        # Resubscribing on every connect is required after a session the broker did not keep.
        client.subscribe([(topic, QOS) for topic in TOPIC_FILTERS])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.warning("MQTT 브로커 연결 끊김: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        # paho calls this on its own thread, so the message gets its own short-lived DB session.
        try:
            with self.sessions() as session:
                outcome = apply_message(session, message.topic, message.payload)
        except Exception:
            # A raise here would kill paho's loop thread and silently stop all telemetry.
            log.exception("MQTT 메시지 처리 중 예외: %s", message.topic)
            return
        if outcome.startswith("ignored"):
            log.warning("MQTT 메시지를 건너뜀(%s): %s", outcome, message.topic)


def make_listener(settings: Settings) -> DeviceTelemetryListener | None:
    """No broker address means the feature is off, the same rule the Pi publisher uses."""
    if not settings.mqtt_host:
        return None
    try:
        return DeviceTelemetryListener(settings)
    except ImportError:
        log.warning("paho-mqtt가 없어 장치 상태 구독을 시작하지 않습니다")
        return None
