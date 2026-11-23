"""Fetch a runtime copy from SSM and write raw Compose env files, never stdout."""
import argparse
import json
import os
from pathlib import Path
import re

import boto3


# Pi 설정은 전부 선택값이다. 값이 없으면 백엔드가 기존처럼 PI_NOT_CONFIGURED류 오류를 낸다.
REQUIRED = {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "S3_BUCKET", "S3_REGION"}
OPTIONAL = {"BUSINESS_API_ENABLED", "COMMAND_TTL_SECONDS", "PI_TIMEOUT_SECONDS", "MAX_IMAGE_BYTES",
            "PREVIEW_TTL_SECONDS", "RETURN_CODE_TTL_SECONDS", "PI_DEVICES", "GEMINI_API_KEY", "GEMINI_MODEL",
            "IMAGE_ANALYSIS_TIMEOUT_SECONDS", "DEV_FAKE_DEVICE",
            # 소유 확인 검색의 예산·대기·동시 실행 (backend/app/ai_ownership_verification/workflow.md).
            "OWNERSHIP_SEARCH_BUDGET_SECONDS", "OWNERSHIP_SYNC_WAIT_SECONDS",
            "OWNERSHIP_SEARCH_CONCURRENCY", "OWNERSHIP_MAX_CONCURRENT_SEARCHES",
            # 사진 분석의 대기·동시 실행. 분석 예산은 IMAGE_ANALYSIS_TIMEOUT_SECONDS가 그대로 쓰인다.
            "ANALYSIS_SYNC_WAIT_SECONDS", "MAX_CONCURRENT_ANALYSES",
            # 장치 상태 구독 (docs/mqtt-protocol.md). MQTT_HOST가 없으면 구독을 시작하지 않는다.
            "MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_CLIENT_ID",
            "MQTT_ALERT_RETENTION_DAYS", "MQTT_BIND_ADDRESS"}


def render(payload: dict, output: Path) -> None:
    if not isinstance(payload, dict) or not REQUIRED <= set(payload) or set(payload) - REQUIRED - OPTIONAL:
        raise ValueError("Invalid deployment configuration keys")
    payload = {"BUSINESS_API_ENABLED": "false", **payload}
    if payload["BUSINESS_API_ENABLED"] not in ("true", "false"):
        raise ValueError("BUSINESS_API_ENABLED must be true or false")
    # Development stand-in for the Pi. Only an explicit "true" enables it.
    if payload.get("DEV_FAKE_DEVICE", "false") not in ("true", "false"):
        raise ValueError("DEV_FAKE_DEVICE must be true or false")
    for key, value in payload.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
            raise ValueError(f"Invalid deployment setting: {key}")
    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", payload[key]):
            raise ValueError(f"Invalid PostgreSQL identifier: {key}")
    if len(payload["POSTGRES_PASSWORD"]) < 32:
        raise ValueError("POSTGRES_PASSWORD must contain at least 32 characters")
    if "GEMINI_MODEL" in payload and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", payload["GEMINI_MODEL"]):
        raise ValueError("Invalid Gemini model name")
    if "IMAGE_ANALYSIS_TIMEOUT_SECONDS" in payload:
        timeout = payload["IMAGE_ANALYSIS_TIMEOUT_SECONDS"]
        if not re.fullmatch(r"[0-9]{1,2}", timeout) or not 5 <= int(timeout) <= 90:
            raise ValueError("IMAGE_ANALYSIS_TIMEOUT_SECONDS must be between 5 and 90")
    # 검색 예산은 nginx의 proxy_read_timeout 120초보다 작아야 한다. 더 크면 프록시가 먼저 끊는다.
    for key, low, high in (("OWNERSHIP_SEARCH_BUDGET_SECONDS", 5, 110), ("OWNERSHIP_SYNC_WAIT_SECONDS", 0, 30),
                           ("OWNERSHIP_SEARCH_CONCURRENCY", 1, 10), ("OWNERSHIP_MAX_CONCURRENT_SEARCHES", 1, 16),
                           ("ANALYSIS_SYNC_WAIT_SECONDS", 0, 30), ("MAX_CONCURRENT_ANALYSES", 1, 8)):
        if key in payload and (not re.fullmatch(r"[0-9]{1,3}", payload[key])
                               or not low <= int(payload[key]) <= high):
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("COMMAND_TTL_SECONDS", "PI_TIMEOUT_SECONDS", "MAX_IMAGE_BYTES", "PREVIEW_TTL_SECONDS",
               "RETURN_CODE_TTL_SECONDS", "MQTT_PORT", "MQTT_ALERT_RETENTION_DAYS"):
        if key in payload and not re.fullmatch(r"[1-9][0-9]{0,9}", payload[key]):
            raise ValueError(f"{key} must be a positive integer")
    # 두 백엔드가 같은 client id를 쓰면 서로의 구독 세션을 끊는다.
    if "MQTT_CLIENT_ID" in payload and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", payload["MQTT_CLIENT_ID"]):
        raise ValueError("Invalid MQTT client id")
    # 브로커 포트를 공개할 주소. 0.0.0.0을 막아 평문 MQTT가 인터넷에 열리지 않게 한다.
    if "MQTT_BIND_ADDRESS" in payload:
        address = payload["MQTT_BIND_ADDRESS"]
        if not re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", address) or address.startswith("0."):
            raise ValueError("MQTT_BIND_ADDRESS must be a specific IPv4 address on this host")
    if "PI_DEVICES" in payload:
        try:
            devices = json.loads(payload["PI_DEVICES"])
        except json.JSONDecodeError:
            raise ValueError("PI_DEVICES must be valid JSON") from None
        if not isinstance(devices, dict) or not devices:
            raise ValueError("PI_DEVICES must be a non-empty JSON object keyed by kiosk id")
        required_device_keys = {"capture_url", "capture_api_key", "locker_url", "locker_api_key"}
        for kiosk_id, device in devices.items():
            if not isinstance(device, dict) or set(device) != required_device_keys:
                raise ValueError(f"PI_DEVICES entry for {kiosk_id!r} must contain exactly {sorted(required_device_keys)}")
    output.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    for name, values in (("backend.env", payload), ("db.env", {k: v for k, v in payload.items() if k.startswith("POSTGRES_")})):
        target = output / name
        target.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8")
        target.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameter", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    response = boto3.client("ssm", region_name=args.region).get_parameter(Name=args.parameter, WithDecryption=True)
    render(json.loads(response["Parameter"]["Value"]), args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # SDK responses or parser values must never expose the decrypted configuration.
        raise SystemExit("Runtime configuration could not be prepared; check keys and instance permissions.") from None
