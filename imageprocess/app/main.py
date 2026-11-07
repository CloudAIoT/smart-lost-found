import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from app.api.routes import router
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.logging import configure_logging
from app.core.middleware import RequestMiddleware
from app.services.vision_service import VisionService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, vision_service=None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    service = vision_service if vision_service is not None else VisionService(settings)

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vision-inference')
        app.state.inference_executor = executor
        app.state.analysis_lock = asyncio.Lock()
        try:
            try:
                await asyncio.get_running_loop().run_in_executor(executor, service.load)
                logger.info('Model initialized backend=%s', settings.inference_backend)
            except Exception as exc:
                service.loaded = False
                # Exception messages may contain paths/tokens; log types, never input/output.
                logger.error('Model loading failed exception_type=%s; /health is degraded', type(exc).__name__)
            yield
        finally:
            try:
                closer = getattr(service, 'close', None)
                if callable(closer):
                    await asyncio.get_running_loop().run_in_executor(executor, closer)
            finally:
                await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    app = FastAPI(title='Lost Item AI Image Processing Service', version='0.1.0', lifespan=lifespan)
    app.state.settings = settings
    app.state.vision_service = service
    app.add_middleware(RequestMiddleware, max_upload_bytes=settings.max_upload_bytes)

    def error_response(request, code, message, status):
        request_id = request.state.request_id
        logger.warning('[%s] response error code=%s', request_id, code)
        return JSONResponse({'success': False, 'request_id': request_id,
                             'error': {'code': code, 'message': message}}, status_code=status)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return error_response(request, exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, 'REQUEST_VALIDATION_ERROR',
                              'Upload one image using the multipart field named image.', 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, 'HTTP_ERROR', 'Request could not be processed.', exc.status_code)

    app.include_router(router)
    return app


app = create_app()


if __name__ == '__main__':
    import uvicorn
    settings = Settings()
    uvicorn.run('app.main:app', host=settings.host, port=settings.port)
