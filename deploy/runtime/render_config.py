"""Fetch a runtime copy from SSM and write raw Compose env files, never stdout."""
import argparse
import json
import os
from pathlib import Path
import re

import boto3


def render(payload: dict, output: Path) -> None:
    allowed = {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "S3_BUCKET", "S3_REGION"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("Invalid deployment configuration keys")
    for key, value in payload.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
            raise ValueError(f"Invalid deployment setting: {key}")
    for key in ("POSTGRES_DB", "POSTGRES_USER"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", payload[key]):
            raise ValueError(f"Invalid PostgreSQL identifier: {key}")
    if len(payload["POSTGRES_PASSWORD"]) < 32:
        raise ValueError("POSTGRES_PASSWORD must contain at least 32 characters")
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
