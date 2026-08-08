from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from zeroth.config import settings
from zeroth.db import init_db
from zeroth.api.routers import bundle, gallery, jobs, stream

# The interactive docs are off by default. This API is public, and /docs
# published the whole surface - including the verify endpoint that takes a
# Zerops personal access token - as a ready-made form for anyone who found it.
# Set EXPOSE_API_DOCS=true for local work.
_docs = settings.expose_api_docs
app = FastAPI(
    title="Zeroth",
    version="0.1.0",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(stream.router)
app.include_router(gallery.router)
app.include_router(bundle.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})
