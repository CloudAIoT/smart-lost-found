FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
COPY imageprocess/requirements-core.txt ./imageprocess-requirements.txt
RUN pip install --no-cache-dir -r requirements-dev.txt -r imageprocess-requirements.txt && pip check
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY imageprocess/__init__.py ./imageprocess/__init__.py
COPY imageprocess/app ./imageprocess/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
