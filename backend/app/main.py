import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import dashboard as dashboard_api
from app.api import sim as sim_api
from app.config import Settings, get_settings
from app.store.db import get_session, init_db
from app.store.seed import seed_if_empty
from app.telephony import vobiz_routes
from app.tts.tts import get_tts_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("guardrail")


def _init_and_seed() -> None:
    settings = get_settings()
    init_db(settings)
    with get_session(settings) as session:
        if seed_if_empty(session):
            logger.info("database was empty — seeded demo data")


async def _prerender_greeting(settings: Settings) -> None:
    settings.require_vobiz()
    settings.require_stt()
    tts = get_tts_client(settings)  # also calls settings.require_tts()
    audio = await tts.synthesize(vobiz_routes.GREETING_TEXT)
    vobiz_routes.set_greeting_audio(audio)
    logger.info("pre-rendered greeting audio (%d bytes)", len(audio))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_init_and_seed)
    await _prerender_greeting(get_settings())
    yield


app = FastAPI(title="GuardRail", lifespan=lifespan)
app.include_router(vobiz_routes.router)
app.include_router(sim_api.router)
app.include_router(dashboard_api.router)

settings = get_settings()
if settings.APP_ENV == "dev":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app_env": settings.APP_ENV}
