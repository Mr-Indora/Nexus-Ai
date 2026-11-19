from contextlib import asynccontextmanager
import logging
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from db import init_db
from ai.local_llm import local_llm
from routes import api, auth_routes, linkedin as linkedin_routes, pids as pids_routes


class OAuthAccessLogFilter(logging.Filter):
    """Keep one-time OAuth codes and state values out of local access logs."""

    _callback = re.compile(r"(/api/auth/linkedin/callback)\?[^\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            safe_args = list(args)
            safe_args[2] = self._callback.sub(r"\1?[redacted]", safe_args[2])
            record.args = tuple(safe_args)
        return True


logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        yield
    finally:
        local_llm.unload()


app = FastAPI(title="Nexus AI API", lifespan=lifespan)


@app.middleware("http")
async def protect_state_changing_requests(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        request_origin = origin or (
            f"{urlsplit(referer).scheme}://{urlsplit(referer).netloc}" if referer else None
        )
        if request_origin and request_origin not in settings.FRONTEND_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
    return await call_next(request)


# Frontend normally proxies /api via Next.js rewrites (same-origin), CORS is only a safety net.
app.add_middleware(CORSMiddleware, allow_origins=list(settings.FRONTEND_ORIGINS), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_routes.router)
app.include_router(linkedin_routes.router)
app.include_router(api.router)
app.include_router(pids_routes.router)
