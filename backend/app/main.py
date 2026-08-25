from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app.config import settings
from app.routers import documents, search, chat, agent, tasks, evaluations
from redis.asyncio import Redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="InsightFlow", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(agent.router)
app.include_router(tasks.router)
app.include_router(evaluations.router)

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
    }


@app.get("/api/ready")
async def ready():
    from sqlalchemy import text
    checks = {"database": "ok", "redis": "ok"}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        checks["database"] = type(exc).__name__
    try:
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        try:
            await asyncio.wait_for(redis.ping(), timeout=1.5)
        finally:
            await redis.close()
    except Exception as exc:
        checks["redis"] = type(exc).__name__
    if any(value != "ok" for value in checks.values()):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail={"status": "not_ready", **checks})
    return {"status": "ready", **checks}
