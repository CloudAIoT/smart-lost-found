import logging
import re
from time import perf_counter
from uuid import uuid4
from starlette.responses import JSONResponse
from app.core.errors import ServiceError

logger = logging.getLogger(__name__)


class RequestMiddleware:
    """Correlate every HTTP request and bound bodies before multipart spooling."""
    def __init__(self, app, max_upload_bytes: int):
        self.app = app
        # Permit multipart headers/framing; the endpoint enforces exact file bytes.
        self.max_body_bytes = max_upload_bytes + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        provided = headers.get(b'x-request-id', b'').decode('ascii', errors='ignore')
        request_id = provided if re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', provided) else str(uuid4())
        scope.setdefault('state', {})['request_id'] = request_id
        started = perf_counter()
        sent_response = False
        body_bytes = 0
        logger.info('[%s] request received method=%s', request_id, scope['method'])

        async def bounded_receive():
            nonlocal body_bytes
            message = await receive()
            if message['type'] == 'http.request':
                body_bytes += len(message.get('body', b''))
                if body_bytes > self.max_body_bytes:
                    raise ServiceError('IMAGE_TOO_LARGE', 'Request exceeds the upload limit.', 413)
            return message

        async def correlated_send(message):
            nonlocal sent_response
            if message['type'] == 'http.response.start':
                sent_response = True
                message['headers'] = [
                    (k, v) for k, v in message.get('headers', []) if k.lower() != b'x-request-id'
                ] + [(b'x-request-id', request_id.encode('ascii'))]
            await send(message)

        try:
            content_length = headers.get(b'content-length')
            if content_length:
                try:
                    too_large = int(content_length) > self.max_body_bytes
                except ValueError:
                    too_large = False
                if too_large:
                    raise ServiceError('IMAGE_TOO_LARGE', 'Request exceeds the upload limit.', 413)
            await self.app(scope, bounded_receive, correlated_send)
        except Exception as exc:
            if sent_response:
                raise
            if isinstance(exc, ServiceError):
                code, message, status = exc.code, exc.message, exc.status
            else:
                code, message, status = 'INTERNAL_ERROR', 'Unexpected server error.', 500
            logger.error('[%s] request failed code=%s exception_type=%s', request_id, code, type(exc).__name__)
            response = JSONResponse({'success': False, 'request_id': request_id,
                                     'error': {'code': code, 'message': message}}, status_code=status)
            await response(scope, receive, correlated_send)
        finally:
            logger.info('[%s] total request duration_ms=%.2f', request_id, (perf_counter() - started) * 1000)
