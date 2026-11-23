from contextlib import asynccontextmanager
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import engine, get_session
from app.api import router
from app.ai_ownership_verification import search_jobs
from app.ai_ownership_verification.api import router as ownership_router
from app.errors import BusinessError, IntegrationError
from app.mqtt_listener import make_listener


log = logging.getLogger("app")

# uvicorn configures only its own loggers, so without a root handler this package's INFO lines
# are dropped and only WARNING and above reach stderr. An operator could then not tell a running
# MQTT subscriber from one that never started. Added without a format so uvicorn's own output,
# which sets propagate=False, keeps its usual shape.
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Device telemetry is optional: without a broker address the API runs exactly as before.
    listener = await run_in_threadpool(make_listener, get_settings())
    if listener is None:
        # Said once at startup so the absence of telemetry is visible, not guessed from silence.
        log.info("장치 상태 MQTT 구독을 시작하지 않습니다 (MQTT_HOST 미설정)")
    else:
        await run_in_threadpool(listener.start)
    try:
        yield
    finally:
        if listener is not None:
            await run_in_threadpool(listener.stop)
        # Background searches hold DB sessions, so they stop before the engine goes away.
        await run_in_threadpool(search_jobs.shutdown)
        await run_in_threadpool(engine.dispose)


app = FastAPI(title="Smart Lost Found", lifespan=lifespan)
app.include_router(router)
app.include_router(ownership_router)


@app.exception_handler(BusinessError)
async def business_error(request, error: BusinessError):
    return JSONResponse(status_code=error.status, content={"detail": {"code": error.code}})


@app.exception_handler(IntegrationError)
async def integration_error(request, error: IntegrationError):
    status = 503 if error.code.endswith("NOT_CONFIGURED") else 502
    return JSONResponse(status_code=status, content={"detail": {"code": error.code}})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", responses={503: {"description": "Database unavailable"}})
def ready(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        # Never return or log exception details containing connection information.
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready"}
