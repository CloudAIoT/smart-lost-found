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
            "PREVIEW_TTL_SECONDS", "RETURN_CODE_TTL_SECONDS", "PI_DEVICES"}


def render(payload: dict, output: Path) -> None:
    if not isinstance(payload, dict) or not REQUIRED <= set(payload) or set(payload) - REQUIRED - OPTIONAL:
        raise ValueError("Invalid deployment configuration keys")
    payload = {"BUSINESS_API_ENABLED": "false", **payload}
    if payload["BUSINESS_API_ENABLED"] not in ("true", "false"):
        raise ValueError("BUSINESS_API_ENABLED must be true or false")
    for key, value in payload.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
            raise ValueError(f"Invalid deployment setting: {key}")
    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", payload[key]):
            raise ValueError(f"Invalid PostgreSQL identifier: {key}")
    if len(payload["POSTGRES_PASSWORD"]) < 32:
        raise ValueError("POSTGRES_PASSWORD must contain at least 32 characters")
    for key in ("COMMAND_TTL_SECONDS", "PI_TIMEOUT_SECONDS", "MAX_IMAGE_BYTES", "PREVIEW_TTL_SECONDS",
               "RETURN_CODE_TTL_SECONDS"):
        if key in payload and not re.fullmatch(r"[1-9][0-9]{0,9}", payload[key]):
            raise ValueError(f"{key} must be a positive integer")
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
