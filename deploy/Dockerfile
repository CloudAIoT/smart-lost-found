# 프론트엔드 정적 파일 빌드. 결과물만 최종 이미지로 옮기고 Node는 운영 이미지에 남기지 않는다.
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.js frontend/postcss.config.js frontend/tailwind.config.js ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip check \
    && useradd --uid 10001 --create-home appuser
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY deploy/runtime /opt/deploy
# 배포 시 publish.py가 /opt/deploy를 릴리스 디렉터리로 복사하고 nginx가 frontend/를 서비스한다.
COPY --from=frontend /frontend/dist /opt/deploy/frontend
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
