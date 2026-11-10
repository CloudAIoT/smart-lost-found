import asyncio
import logging
from functools import partial
from time import perf_counter
from fastapi import APIRouter, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from app.core.errors import ServiceError
from app.models.schemas import AnalyzeResponse, ErrorResponse
from app.services.preprocessing import preprocess
from app.services.vision_service import normalize_category, parse_features

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get('/health')
async def health(request: Request):
    loaded = request.app.state.vision_service.loaded
    return JSONResponse({'status': 'ok' if loaded else 'degraded', 'model_loaded': loaded,
                         'backend': request.app.state.settings.inference_backend}, status_code=200 if loaded else 503)


@router.post('/analyze', response_model=AnalyzeResponse,
             responses={code: {'model': ErrorResponse} for code in (413, 422, 502, 503)})
async def analyze(request: Request, image: UploadFile = File(...), category: str | None = Form(None)):
    started = perf_counter()
    request_id = request.state.request_id
    state = request.app.state
    try:
        category = normalize_category(category)
        if not state.vision_service.loaded:
            raise ServiceError('MODEL_NOT_READY', 'Model is unavailable. Check server startup logs.', 503)
        if state.analysis_lock.locked():
            raise ServiceError('SERVER_BUSY', 'Another analysis is running. Retry later.', 503)
        async with state.analysis_lock:
            # Read bounded chunks rather than buffering an arbitrary upload.
            content = bytearray()
            while chunk := await image.read(64 * 1024):
                content.extend(chunk)
                if len(content) > state.settings.max_upload_bytes:
                    raise ServiceError('IMAGE_TOO_LARGE', 'Image exceeds the upload byte limit.', 413)
            logger.info('[%s] preprocessing started', request_id)
            prepared = await run_in_threadpool(preprocess, bytes(content), state.settings)
            logger.info('[%s] preprocessing completed quality_flags=%s', request_id, prepared.warnings)
            inference_started = perf_counter()
            logger.info('[%s] inference started', request_id)
            try:
                # MLX loads and generates on the same single worker; never block the event loop.
                raw = await asyncio.get_running_loop().run_in_executor(
                    state.inference_executor, partial(state.vision_service.infer, prepared.image, category=category))
            except ServiceError:
                raise
            except Exception as exc:
                logger.error('[%s] inference failed exception_type=%s', request_id, type(exc).__name__)
                raise ServiceError('AI_INFERENCE_FAILED', 'Image inference failed. Retry or check server logs.', 502) from None
            finally:
                logger.info('[%s] inference duration_ms=%.2f', request_id, (perf_counter() - inference_started) * 1000)
            logger.info('[%s] inference completed', request_id)
            try:
                features = parse_features(raw)
            except ServiceError:
                logger.warning('[%s] parsing failure', request_id)
                raise
            logger.info('[%s] parsing success', request_id)
            flags = prepared.warnings
            if state.settings.inference_backend == 'mock':
                flags.append('MOCK_INFERENCE')
            return AnalyzeResponse(request_id=request_id, input_category=category, data=features,
                                   processing_time_ms=round((perf_counter() - started) * 1000, 2), warnings=flags)
    finally:
        await image.close()
