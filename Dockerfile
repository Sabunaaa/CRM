FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY backend/requirements.txt backend/requirements-collector.txt ./
RUN pip install --no-cache-dir -r requirements-collector.txt \
    && scrapling install \
    && chmod -R a+rX /ms-playwright
COPY backend/app ./app
COPY --from=frontend /build/frontend/dist ./frontend_dist
USER 65532:65532
CMD ["python", "-m", "app.serve"]
