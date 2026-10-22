"""SchemaSage API: HTTP access to the core/ pipeline, with app state in app.db."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes import databases, pipeline, relationships
from api.appdb import NotReady, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SchemaSage API", lifespan=lifespan)
app.include_router(databases.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(relationships.router, prefix="/api")


@app.exception_handler(NotReady)
async def not_ready(request: Request, exc: NotReady) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)
