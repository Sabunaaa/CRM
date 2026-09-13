import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import get_settings

app = FastAPI(title="InstaTrack CRM API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])
app.include_router(router)
logger = logging.getLogger("instatrack.api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("unhandled_request_error path=%s", request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "The request could not be completed"})


frontend_dir = Path(os.getenv("FRONTEND_DIST_DIR", Path(__file__).resolve().parent.parent / "frontend_dist"))
assets_dir = frontend_dir / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    path = frontend_dir / "favicon.svg"
    if path.exists():
        return FileResponse(path, media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"detail": "Favicon not found"})


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    root = frontend_dir.resolve()
    requested_file = (root / full_path).resolve()
    if requested_file.is_relative_to(root) and requested_file.is_file():
        return FileResponse(requested_file)

    index = root / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"detail": "Frontend build not found"})
