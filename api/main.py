"""SchemaSage API: HTTP access to the core/ pipeline, with app state in app.db."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes import benchmark, chat, databases, docs, pipeline, relationships, settings, sql, tables
from api.appdb import NotReady, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SchemaSage API", lifespan=lifespan)
app.include_router(benchmark.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(databases.router, prefix="/api")
app.include_router(docs.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(relationships.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(sql.router, prefix="/api")
app.include_router(tables.router, prefix="/api")


@app.exception_handler(NotReady)
async def not_ready(request: Request, exc: NotReady) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(RuntimeError)
async def no_api_key(request: Request, exc: RuntimeError) -> JSONResponse:
    if not str(exc).startswith("OPENAI_API_KEY"):  # llm.client()'s message; anything else stays a 500
        raise exc
    return JSONResponse({"detail": str(exc)}, status_code=503)
