from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.db import engine, get_session
from app.api import router
from app.errors import BusinessError, IntegrationError


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await run_in_threadpool(engine.dispose)


app = FastAPI(title="Smart Lost Found", lifespan=lifespan)
app.include_router(router)


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
