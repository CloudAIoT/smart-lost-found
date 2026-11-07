from starlette.exceptions import HTTPException


class ServiceError(HTTPException):
    # FastAPI preserves HTTPException raised during multipart parsing. A plain
    # exception here would incorrectly turn a streamed upload limit into HTTP 400.
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(status_code=status, detail=message)
