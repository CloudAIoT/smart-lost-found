"""MQTT로 보내는 부분(app/publisher.py)을 paho 클라이언트 대역으로 확인한다. 브로커는 필요 없다."""

import json

from app.publisher import MqttPublisher
from conftest import make_settings

KIOSK = "00000000-0000-0000-0000-000000000001"


class FakeInfo:
    rc = 0

    def wait_for_publish(self, timeout=None):
        self.timeout = timeout


class FakeClient:
    """paho.mqtt.client.Client 가운데 쓰는 부분만 흉내 낸다."""

    def __init__(self):
        self.published = []
        self.will = None
        self.auth = None
        self.address = None
        self.looping = False
        self.connected = False

    def username_pw_set(self, username, password):
        self.auth = (username, password)

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, json.loads(payload), qos, retain)

    def reconnect_delay_set(self, min_delay, max_delay):
        pass

    def connect_async(self, host, port, keepalive):
        self.address = (host, port, keepalive)

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def is_connected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, json.loads(payload), qos, retain))
        return FakeInfo()


class Reason:
    def __init__(self, failure: bool):
        self.is_failure = failure


def make_publisher(client, **values):
    settings = make_settings("locker", mqtt_host="broker", **values)
    return MqttPublisher(settings, client=client)


def test_will_message_says_offline_and_uses_device_account():
    client = FakeClient()
    make_publisher(client, mqtt_username="locker-pi", mqtt_password="secret-pw")
    assert client.will == (f"slf/{KIOSK}/locker/status", {"status": "offline"}, 1, True)
    assert client.auth == ("locker-pi", "secret-pw")


def test_connect_announces_online_and_resends_states():
    client = FakeClient()
    publisher = make_publisher(client)
    resent = []
    publisher.on_connected.append(lambda: resent.append(True))
    publisher.start()
    assert client.address == ("broker", 1883, 30) and client.looping
    publisher._on_connect(client, None, None, Reason(False), None)
    topic, payload, qos, retain = client.published[0]
    assert (topic, payload["status"], qos, retain) == (f"slf/{KIOSK}/locker/status", "online", 1, True)
    assert resent == [True]


def test_failed_connect_sends_nothing():
    client = FakeClient()
    make_publisher(client)._on_connect(client, None, None, Reason(True), None)
    assert client.published == []


def test_messages_go_under_kiosk_prefix_with_qos_1():
    client = FakeClient()
    make_publisher(client).publish("alerts", {"code": "FORCED_OPEN"})
    assert client.published == [(f"slf/{KIOSK}/alerts", {"code": "FORCED_OPEN"}, 1, False)]


def test_stop_says_offline_before_leaving():
    client = FakeClient()
    publisher = make_publisher(client)
    publisher.start()
    client.connected = True
    publisher.stop()
    topic, payload, _, retain = client.published[-1]
    assert (topic, payload["status"], retain) == (f"slf/{KIOSK}/locker/status", "offline", True)
    assert not client.looping
