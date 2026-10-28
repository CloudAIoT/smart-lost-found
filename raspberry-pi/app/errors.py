"""통신 규약 4절의 오류 형식: {"detail": {"code": ..., "message": ...}}"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DeviceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(code)


def error_body(code: str, message: str) -> dict:
    return {"detail": {"code": code, "message": message}}


async def handle_device_error(request: Request, error: DeviceError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content=error_body(error.code, error.message))


async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # FastAPI 기본값(422) 대신 규약의 400 INVALID_COMMAND로 응답한다
    return JSONResponse(status_code=400, content=error_body("INVALID_COMMAND", "요청 형식이 올바르지 않습니다"))
