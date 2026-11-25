"""MQTT로 오는 센서 상태와 경고를 화면에 찍는다 (시연·점검용, docs/mqtt-protocol.md).

예: python tools/watch.py --host 100.73.97.89
    python tools/watch.py --host 127.0.0.1 --port 18830 --kiosk 00000000-0000-0000-0000-000000000001
브로커 계정이 있으면 --user와 환경변수 MQTT_PASSWORD(또는 --password)를 준다.
"""

import argparse
import json
import os
from datetime import datetime

import paho.mqtt.client as mqtt

LABELS = {"alarm": "경보", "warning": "경고"}


def show(message: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(message.payload)
    except ValueError:
        payload = message.payload.decode(errors="replace")
    time = datetime.now().strftime("%H:%M:%S")
    if message.topic.endswith("/alerts") and isinstance(payload, dict):
        label = LABELS.get(payload.get("severity"), "알림")
        print(f"{time} [{label}] {payload.get('code')}: {payload.get('message')}", flush=True)
        return
    kept = " (보존)" if message.retain else ""
    print(f"{time} {message.topic}{kept} {json.dumps(payload, ensure_ascii=False)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT 센서 메시지 보기")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--user")
    parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD"))
    parser.add_argument("--kiosk", default="+", help="키오스크 ID. 생략하면 모든 키오스크")
    args = parser.parse_args()

    topic = f"slf/{args.kiosk}/#"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.user:
        client.username_pw_set(args.user, args.password or "")

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"연결 실패: {reason_code}", flush=True)
            return
        print(f"{args.host}:{args.port}에 연결, {topic} 구독", flush=True)
        client.subscribe(topic, qos=1)

    client.on_connect = on_connect
    client.on_message = lambda client, userdata, message: show(message)
    client.connect(args.host, args.port, keepalive=30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("끝", flush=True)


if __name__ == "__main__":
    main()
