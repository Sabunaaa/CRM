FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/home/instatrack \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY backend/requirements.txt backend/requirements-collector.txt ./
RUN pip install --no-cache-dir -r requirements-collector.txt \
    && scrapling install --force \
    && groupadd --gid 10001 instatrack \
    && useradd --uid 10001 --gid instatrack --create-home --home-dir /home/instatrack --shell /usr/sbin/nologin instatrack \
    && chmod -R a+rX /ms-playwright \
    && chown -R instatrack:instatrack /home/instatrack
COPY backend/app ./app
COPY --from=frontend /build/frontend/dist ./frontend_dist
USER instatrack
CMD ["python", "-m", "app.serve"]
