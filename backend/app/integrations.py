"""Replaceable boundaries. No fake AI or device is installed in the application."""

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Literal, Protocol
from uuid import UUID
import warnings

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import httpx
from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.errors import IntegrationError


class DeviceGateway(Protocol):
    def capture(self, kiosk_id: UUID, command_id: str, registration_id: UUID, expires_at: datetime) -> bytes: ...
    def open(self, kiosk_id: UUID, locker_number: int, command_id: str, expires_at: datetime) -> None: ...
    def door(self, kiosk_id: UUID, locker_number: int) -> Literal["open", "closed"]: ...


class ImageStore(Protocol):
    def put(self, key: str, jpeg: bytes) -> None: ...
    def read(self, key: str) -> bytes: ...
    def preview(self, key: str) -> str: ...


@dataclass(frozen=True)
class AnalysisInput:
    image_id: UUID
    jpeg: bytes


@dataclass(frozen=True)
class AnalysisResult:
    # Internal BE boundary only; AI's external response contract is still pending.
    image_ids: tuple[UUID, UUID]
    features: dict


class FeatureAnalyzer(Protocol):
    def analyze(self, registration_id: UUID, images: tuple[AnalysisInput, AnalysisInput]) -> AnalysisResult: ...


class UnconfiguredAnalyzer:
    def analyze(self, registration_id: UUID, images: tuple[AnalysisInput, AnalysisInput]) -> AnalysisResult:
        raise IntegrationError("AI_NOT_CONFIGURED")


def validate_jpeg(data: bytes, max_bytes: int) -> None:
    if not data or len(data) > max_bytes:
        raise IntegrationError("INVALID_IMAGE")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as img:
                if img.format != "JPEG":
                    raise IntegrationError("INVALID_IMAGE")
                img.verify()
            # verify() alone does not decode JPEG scan data.
            with Image.open(BytesIO(data)) as img:
                img.load()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise IntegrationError("INVALID_IMAGE") from None


class PiGateway:
    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings = settings
        self.client = client

    def _request(self, kiosk_id: UUID, role: str, method: str, path: str, body: dict | None = None) -> httpx.Response:
        device = self.settings.pi_devices.get(str(kiosk_id), {})
        url = device.get(f"{role}_url")
        if not url or not self.settings.pi_timeout_seconds:
            raise IntegrationError("DEVICE_NOT_CONFIGURED")
        headers = {}
        if key := device.get(f"{role}_api_key"):
            headers["X-API-Key"] = key
        try:
            with self.client.stream(method, url.rstrip("/") + path, json=body, headers=headers,
                                    timeout=self.settings.pi_timeout_seconds, follow_redirects=False) as streamed:
                limit = self.settings.max_image_bytes if path == "/capture" and streamed.status_code == 200 else 65536
                if not limit:
                    raise IntegrationError("CAPTURE_NOT_CONFIGURED")
                chunks = bytearray()
                for chunk in streamed.iter_bytes(chunk_size=65536):
                    if len(chunks) + len(chunk) > limit:
                        raise IntegrationError("DEVICE_RESPONSE_TOO_LARGE", uncertain=True)
                    chunks.extend(chunk)
                response = httpx.Response(streamed.status_code, headers=streamed.headers, content=bytes(chunks))
        except httpx.RequestError:
            # Even a network error can arrive after the command executed.
            raise IntegrationError("DEVICE_UNREACHABLE", uncertain=True) from None
        if response.status_code != 200:
            known = {400: {"COMMAND_EXPIRED", "INVALID_COMMAND"}, 401: {"UNAUTHORIZED"},
                     404: {"LOCKER_NOT_FOUND"}, 409: {"DEVICE_BUSY"},
                     500: {"CAMERA_ERROR", "LOCK_ERROR"}}
            try:
                code = response.json()["detail"]["code"]
                if isinstance(code, str) and code in known.get(response.status_code, set()):
                    raise IntegrationError(code)
            except (ValueError, TypeError, KeyError):
                pass
            raise IntegrationError("INVALID_DEVICE_RESPONSE", uncertain=True)
        return response

    def _check_role(self, kiosk_id: UUID, role: str) -> None:
        response = self._request(kiosk_id, role, "GET", "/health")
        try:
            data = response.json()
            if data["status"] == "ok" and data["role"] == role:
                return
        except (ValueError, TypeError, KeyError):
            pass
        raise IntegrationError("DEVICE_ROLE_MISMATCH")

    def capture(self, kiosk_id: UUID, command_id: str, registration_id: UUID, expires_at: datetime) -> bytes:
        self._check_role(kiosk_id, "capture")
        response = self._request(kiosk_id, "capture", "POST", "/capture", {
            "command_id": command_id, "registration_id": str(registration_id), "expires_at": expires_at.isoformat(),
        })
        if response.headers.get("content-type", "").split(";")[0].strip().lower() != "image/jpeg":
            raise IntegrationError("INVALID_DEVICE_RESPONSE", uncertain=True)
        return response.content

    def open(self, kiosk_id: UUID, locker_number: int, command_id: str, expires_at: datetime) -> None:
        self._check_role(kiosk_id, "locker")
        response = self._request(kiosk_id, "locker", "POST", f"/lockers/{locker_number}/open", {
            "command_id": command_id, "expires_at": expires_at.isoformat(),
        })
        try:
            data = response.json()
            if type(data["locker_id"]) is int and data["locker_id"] == locker_number and data["result"] == "opened":
                return
        except (ValueError, KeyError, TypeError):
            pass
        raise IntegrationError("INVALID_DEVICE_RESPONSE", uncertain=True)

    def door(self, kiosk_id: UUID, locker_number: int) -> Literal["open", "closed"]:
        response = self._request(kiosk_id, "locker", "GET", f"/lockers/{locker_number}")
        try:
            data = response.json()
            if type(data["locker_id"]) is int and data["locker_id"] == locker_number and data["door"] in ("open", "closed"):
                return data["door"]
        except (ValueError, KeyError, TypeError):
            pass
        raise IntegrationError("INVALID_DEVICE_RESPONSE", uncertain=True)


class S3ImageStore:
    def __init__(self, client, bucket: str, preview_ttl_seconds: int, max_image_bytes: int):
        self.client = client
        self.bucket = bucket
        self.preview_ttl = preview_ttl_seconds
        self.max_image_bytes = max_image_bytes

    def put(self, key: str, jpeg: bytes) -> None:
        try:
            # No public ACL. Bucket public access blocking must also be configured.
            self.client.put_object(Bucket=self.bucket, Key=key, Body=jpeg, ContentType="image/jpeg")
        except (BotoCoreError, ClientError):
            raise IntegrationError("IMAGE_STORAGE_FAILED") from None

    def read(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with response["Body"] as body:
                data = body.read(self.max_image_bytes + 1)
            validate_jpeg(data, self.max_image_bytes)
            return data
        except (BotoCoreError, ClientError, OSError):
            raise IntegrationError("IMAGE_STORAGE_FAILED") from None

    def preview(self, key: str) -> str:
        try:
            return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                                     ExpiresIn=self.preview_ttl)
        except (BotoCoreError, ClientError):
            raise IntegrationError("IMAGE_STORAGE_FAILED") from None


def make_store(settings: Settings) -> S3ImageStore:
    if not all((settings.s3_bucket, settings.s3_region, settings.preview_ttl_seconds, settings.max_image_bytes)):
        raise IntegrationError("IMAGE_STORAGE_NOT_CONFIGURED")
    return S3ImageStore(boto3.client("s3", region_name=settings.s3_region), settings.s3_bucket,
                        settings.preview_ttl_seconds, settings.max_image_bytes)
