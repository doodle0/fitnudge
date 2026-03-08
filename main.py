"""
FitNudge — FastAPI application entry point.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.models import create_tables
from routes.auth import router as auth_router
from routes.webhook import router as webhook_router
from routes.internal import router as internal_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="FitNudge",
    description="LLM-powered KakaoTalk fitness accountability bot",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(webhook_router)
app.include_router(internal_router)


@app.on_event("startup")
async def on_startup():
    # Create all DB tables (idempotent)
    await create_tables()

    # Start APScheduler
    from scheduler.jobs import scheduler
    if not scheduler.running:
        scheduler.start()

    logging.getLogger(__name__).info("FitNudge started. DB tables ensured. Scheduler running.")


@app.on_event("shutdown")
async def on_shutdown():
    from scheduler.jobs import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/health")
async def health():
    return {"status": "ok"}
